"""manage.py conferir_evolution — por que o WhatsApp da Evolution não sai.

Só leitura: estado da instância de EVOLUTION_INSTANCE, as instâncias conectadas
do servidor e de quantos grupos de loja (GrupoWhatsappLoja) cada uma participa.
Nada é enviado.
"""
from django.conf import settings
from django.core.management.base import BaseCommand

from core import evolution


def grupos_de_loja():
    from cursos.models import GrupoWhatsappLoja
    return list(GrupoWhatsappLoja.objects.filter(ativo=True).select_related('setor').order_by('setor__name'))


def _id_do_grupo(texto):
    return str(texto or '').strip().split('@')[0]


class Command(BaseCommand):
    help = 'Confere a instância da Evolution API (só leitura) e sugere a conectada quando a configurada não envia.'

    def handle(self, *args, **opcoes):
        configurada = getattr(settings, 'EVOLUTION_INSTANCE', '') or ''
        estado = evolution.estado_instancia()
        if estado['existe'] is None:
            self.stdout.write(self.style.ERROR(f'Não deu para conferir: {estado["detalhe"]}'))
            return
        if estado['existe'] is False:
            self.stdout.write(self.style.ERROR(f'EVOLUTION_INSTANCE="{configurada}": a instância não existe '
                                               f'no servidor. Nada sai pela Evolution.'))
        elif estado['estado'] != 'open':
            self.stdout.write(self.style.ERROR(f'EVOLUTION_INSTANCE="{configurada}": WhatsApp desconectado '
                                               f'(estado "{estado["estado"]}"). Leia o QR Code de novo no manager.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'EVOLUTION_INSTANCE="{configurada}": conectada.'))

        sucesso, instancias, detalhe = evolution.listar_instancias()
        if not sucesso:
            self.stdout.write(self.style.WARNING(f'Não deu para listar as instâncias: {detalhe}'))
            return
        abertas = [i for i in instancias if i['estado'] == 'open']
        self.stdout.write(f'\nInstâncias no servidor: {len(instancias)}; conectadas: {len(abertas)}.')
        lojas = grupos_de_loja()
        for inst in abertas:
            marca = ' (a configurada)' if inst['nome'] == configurada else ''
            self.stdout.write(f'\n- {inst["nome"]}{marca}: número {inst["numero"] or "?"}, '
                              f'perfil "{inst["perfil"]}", criada em {inst["criada_em"][:10] or "?"}')
            ok_grupos, grupos, detalhe = evolution.listar_grupos(instancia=inst['nome'], timeout=45)
            if not ok_grupos:
                self.stdout.write(f'  grupos: não deu para listar ({detalhe})')
            else:
                dela = {_id_do_grupo(g['id']) for g in grupos}
                fora = [g for g in lojas if _id_do_grupo(g.grupo) not in dela]
                self.stdout.write(f'  participa de {len(lojas) - len(fora)} de {len(lojas)} grupos de loja')
                if fora:
                    self.stdout.write('  fora de: ' + ', '.join(str(g.setor) for g in fora))
            if inst['nome'] != configurada and not (estado['existe'] and estado['estado'] == 'open'):
                self.stdout.write(self.style.WARNING(
                    f'  para usar esta: EVOLUTION_INSTANCE={inst["nome"]} no .env de cada servidor e reiniciar.'))
