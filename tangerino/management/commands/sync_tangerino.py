"""Sincroniza o vínculo entre usuários do portal e funcionários do Tangerino.

Uso:
    python manage.py sync_tangerino            # só quem ainda não tem vínculo
    python manage.py sync_tangerino --revincular   # refaz todos
    python manage.py sync_tangerino --simular      # mostra sem gravar
"""
from django.core.management.base import BaseCommand

from tangerino.client import TangerinoError, integracao_ativa
from tangerino.models import SincronizacaoTangerino
from tangerino.sync import sincronizar_vinculos


class Command(BaseCommand):
    help = 'Casa usuários do portal com funcionários do Tangerino (por CPF e por nome).'

    def add_arguments(self, parser):
        parser.add_argument('--revincular', action='store_true',
                            help='Refaz também quem já tem employeeId.')
        parser.add_argument('--simular', action='store_true',
                            help='Mostra o resultado sem gravar nada.')
        parser.add_argument('--dados', action='store_true',
                            help='Sincroniza marcações e férias para as tabelas locais.')
        parser.add_argument('--dias', type=int, default=30,
                            help='Janela de dias de ponto para trás (padrão: 30).')

    def handle(self, *args, **opcoes):
        if not integracao_ativa():
            self.stderr.write(self.style.ERROR(
                'Integração desligada: configure TANGERINO_TOKEN e TANGERINO_ENABLED.'))
            return

        if opcoes['dados']:
            self._sincronizar_dados(opcoes['dias'])
            return

        try:
            resultado = sincronizar_vinculos(revincular=opcoes['revincular'],
                                             aplicar=not opcoes['simular'])
        except TangerinoError as exc:
            self.stderr.write(self.style.ERROR(f'Falha: {exc}'))
            return

        if not opcoes['simular']:
            SincronizacaoTangerino.objects.create(
                casados_cpf=resultado['casados_cpf'],
                casados_nome=resultado['casados_nome'],
                ja_vinculados=resultado['ja_vinculados'],
                sem_correspondencia=resultado['sem_correspondencia'],
                sucesso=True)

        self.stdout.write(self.style.SUCCESS(
            f"Casados por CPF: {resultado['casados_cpf']}\n"
            f"Casados por nome: {resultado['casados_nome']}\n"
            f"Já vinculados: {resultado['ja_vinculados']}\n"
            f"Nomes ambíguos (não vinculados): {resultado['ambiguos']}\n"
            f"Sem correspondência: {resultado['sem_correspondencia']}"))

        for pendente in resultado['pendentes']:
            self.stdout.write(f"  - {pendente['nome']} ({pendente['cpf'] or 'sem CPF'})")

    def _sincronizar_dados(self, dias):
        """Espelha marcações e férias nas tabelas locais (bom para cron)."""
        from tangerino.sync import sincronizar_ferias, sincronizar_marcacoes

        for tipo, rotulo, funcao in (
                (SincronizacaoTangerino.Tipo.PONTO, 'Marcações',
                 lambda: sincronizar_marcacoes(dias=dias)),
                (SincronizacaoTangerino.Tipo.FERIAS, 'Férias', sincronizar_ferias)):
            registro = SincronizacaoTangerino(tipo=tipo)
            try:
                resultado = funcao()
                registro.criados = resultado['criados']
                registro.atualizados = resultado['atualizados']
                registro.sucesso = True
                registro.save()
                self.stdout.write(self.style.SUCCESS(
                    f"{rotulo}: {resultado['lidos']} lidos, {resultado['criados']} novos, "
                    f"{resultado['atualizados']} atualizados."))
            except TangerinoError as exc:
                registro.sucesso = False
                registro.detalhe = str(exc)[:2000]
                registro.save()
                self.stderr.write(self.style.ERROR(f'{rotulo}: {exc}'))
