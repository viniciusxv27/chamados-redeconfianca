"""Retoma transcrições paradas, fecha gravações órfãs e limpa partes antigas.

Os workers do gunicorn já fazem isso sozinhos a cada minuto. Este comando é o
plano B (cron, manutenção, depois de um deploy demorado) e processa na própria
execução — não depende de thread nenhuma continuar viva.
"""
import time

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Retoma transcrições paradas, fecha gravações órfãs e limpa partes antigas.'

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Repete a varredura para sempre.')
        parser.add_argument('--intervalo', type=int, default=60, help='Segundos entre as passadas com --loop.')
        parser.add_argument('--id', type=int, action='append', dest='ids',
                            help='Só esta transcrição (pode repetir).')

    def handle(self, *args, **opcoes):
        from agenda import processamento

        while True:
            resumo = processamento.varrer(sincrono=True, somente_ids=opcoes.get('ids'))
            self.stdout.write(', '.join(f'{chave}: {valor}' for chave, valor in resumo.items()))
            if not opcoes['loop']:
                return
            time.sleep(max(10, opcoes['intervalo']))
