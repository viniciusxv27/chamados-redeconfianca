from django.core.management.base import BaseCommand

from fibras.services import sync_fibras


class Command(BaseCommand):
    help = "Sincroniza as vendas de Fibra do MySQL (vendas_servicos) para a tabela local."

    def add_arguments(self, parser):
        parser.add_argument('--year', type=int, default=None)
        parser.add_argument('--month', type=int, default=None)

    def handle(self, *args, **opts):
        stats = sync_fibras(year=opts.get('year'), month=opts.get('month'))
        self.stdout.write(self.style.SUCCESS(
            f"Sync concluído: {stats['created']} novas, {stats['updated']} atualizadas "
            f"(total na fonte: {stats['total_in_source']})."
        ))
