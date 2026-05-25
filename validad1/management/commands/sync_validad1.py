"""Comando para rodar via cron às 00:01 todos os dias.

Crontab sugerido:
    1 0 * * * cd /app && python manage.py sync_validad1
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from validad1.services import sync_d1


class Command(BaseCommand):
    help = "Sincroniza vendas D-1 do MySQL (vendas_servicos do dia anterior)."

    def add_arguments(self, parser):
        parser.add_argument('--date', type=str, default=None, help='YYYY-MM-DD (padrão: ontem)')

    def handle(self, *args, **opts):
        d = None
        if opts.get('date'):
            d = date.fromisoformat(opts['date'])
        stats = sync_d1(target_date=d)
        self.stdout.write(self.style.SUCCESS(
            f"Sync D-1 {stats['target_date']}: {stats['created']} importadas, "
            f"{stats['expired']} expiradas (fonte: {stats['total_in_source']})."
        ))
