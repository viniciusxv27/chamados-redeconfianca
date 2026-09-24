"""Sincroniza o vínculo entre usuários do portal e funcionários do Tangerino.

Uso:
    python manage.py sync_tangerino            # só quem ainda não tem vínculo
    python manage.py sync_tangerino --revincular   # refaz todos
    python manage.py sync_tangerino --simular      # mostra sem gravar
    python manage.py sync_tangerino --dados --dias 30        # ponto dos últimos 30 dias
    python manage.py sync_tangerino --dados --desde 2026-01-01   # traz o histórico de uma vez
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
        parser.add_argument('--desde', default='',
                            help='Data inicial (AAAA-MM-DD) do ponto, no lugar de --dias. '
                                 'Serve para trazer o histórico de uma vez — o relatório '
                                 'também busca sozinho o que falta do período pedido.')

    def handle(self, *args, **opcoes):
        if not integracao_ativa():
            self.stderr.write(self.style.ERROR(
                'Integração desligada: configure TANGERINO_TOKEN e TANGERINO_ENABLED.'))
            return

        if opcoes['dados']:
            desde = None
            if opcoes['desde']:
                from django.utils.dateparse import parse_date
                desde = parse_date(opcoes['desde'])
                if desde is None:
                    self.stderr.write(self.style.ERROR('--desde precisa ser AAAA-MM-DD.'))
                    return
            self._sincronizar_dados(opcoes['dias'], desde)
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

    def _sincronizar_dados(self, dias, desde=None):
        """Espelha marcações e férias nas tabelas locais (bom para cron)."""
        from django.utils import timezone

        from tangerino.sync import (sincronizar_ferias, sincronizar_marcacoes,
                                    sincronizar_periodo, sincronizar_saldos)

        ponto = (lambda: sincronizar_periodo(desde, timezone.localdate())) if desde else (
            lambda: sincronizar_marcacoes(dias=dias))
        for tipo, rotulo, funcao in (
                (SincronizacaoTangerino.Tipo.PONTO, 'Marcações', ponto),
                (SincronizacaoTangerino.Tipo.FERIAS, 'Férias', sincronizar_ferias),
                (SincronizacaoTangerino.Tipo.SALDO, 'Saldo de horas', sincronizar_saldos)):
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
