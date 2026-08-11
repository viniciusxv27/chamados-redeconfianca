"""Aquece o cache do realizado do simulador (mapas por vendedor/PDV do mês).

Executado periodicamente (ex.: a cada ~10 min via cron/tarefa agendada), mantém
o cache de ``get_realized_maps`` sempre quente, de forma que nenhum usuário pague
o custo de construir os mapas (6 consultas agrupadas no MySQL) no request.

Uso:
    python manage.py warm_realized_cache            # mês corrente
    python manage.py warm_realized_cache --year 2026 --month 8
"""

import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from simulator.sql_realizado import get_realized_maps


class Command(BaseCommand):
    help = 'Reconstrói e cacheia os mapas de realizado (vendedor/PDV) do mês.'

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=None, help='Ano (padrão: atual)')
        parser.add_argument('--month', type=int, default=None, help='Mês 1-12 (padrão: atual)')

    def handle(self, *args, **options):
        now = timezone.now()
        year = options['year'] or now.year
        month = options['month'] or now.month

        started = time.perf_counter()
        maps = get_realized_maps(year=year, month=month, force_refresh=True)
        elapsed = time.perf_counter() - started

        if maps.get('ok'):
            vendors = len(maps.get('vendors') or {})
            pdvs = len(maps.get('pdvs') or {})
            self.stdout.write(self.style.SUCCESS(
                f'Cache aquecido para {month:02d}/{year} em {elapsed:.1f}s '
                f'({vendors} vendedores, {pdvs} PDVs).'
            ))
        else:
            # Falha de conexão não é cacheada; apenas reporta.
            self.stderr.write(self.style.WARNING(
                f'Não foi possível construir os mapas de {month:02d}/{year} '
                f'(falha de conexão com o MySQL?). Nada foi cacheado.'
            ))
