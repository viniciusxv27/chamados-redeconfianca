from datetime import date

from django.core.management.base import BaseCommand

from vendas.planos_fora import importar_do_vivogo


class Command(BaseCommand):
    help = 'Sobe para "Planos fora do portfólio" os planos do VivoGo (vendas_servicos) que não existem como plano normal.'

    def add_arguments(self, parser):
        parser.add_argument('--ate', default='2026-10-27', help='Última data de venda considerada (AAAA-MM-DD).')

    def handle(self, *args, **opts):
        r = importar_do_vivogo(ate=date.fromisoformat(opts['ate']))
        self.stdout.write(f"{r['lidos']} combinações lidas · {r['novos']} novas · {r['atualizados']} atualizadas · "
                          f"{r['ja_normais']} já são plano normal")
