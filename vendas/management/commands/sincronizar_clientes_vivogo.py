"""Atualiza agora o espelho dos clientes do Vivo GO (o automático roda de 5 em 5 min)."""
from django.core.management.base import BaseCommand

from vendas import vivogo


class Command(BaseCommand):
    help = 'Lê as views de vendas do Vivo GO (MySQL) e atualiza a base de clientes no Postgres.'

    def add_arguments(self, parser):
        parser.add_argument('--completa', action='store_true', help='Lê tudo e reconcilia (apaga o que saiu de lá).')

    def handle(self, *args, completa=False, **opcoes):
        self.stdout.write(vivogo.sincronizar(completa=completa))
