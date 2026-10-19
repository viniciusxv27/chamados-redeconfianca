from django.core.management.base import BaseCommand, CommandError

from core.agendador_diario import TAREFAS, executar


class Command(BaseCommand):
    help = 'Roda agora uma tarefa diária (ex.: simulador_projecao, quadro_hc_lembretes).'

    def add_arguments(self, parser):
        parser.add_argument('chave', choices=[t[0] for t in TAREFAS])

    def handle(self, *args, **opcoes):
        sucesso, detalhe = executar(opcoes['chave'])
        if not sucesso:
            raise CommandError(detalhe)
        self.stdout.write(self.style.SUCCESS(detalhe))
