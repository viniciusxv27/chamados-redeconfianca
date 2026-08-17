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

    def handle(self, *args, **opcoes):
        if not integracao_ativa():
            self.stderr.write(self.style.ERROR(
                'Integração desligada: configure TANGERINO_TOKEN e TANGERINO_ENABLED.'))
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
