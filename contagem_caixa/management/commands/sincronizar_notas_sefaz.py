"""Busca na SEFAZ (NF-e) e no Ambiente Nacional (NFS-e) as notas emitidas contra os CNPJs da empresa (PIS/Cofins).

Para o agendador (cron): de hora em hora é o ritmo que a SEFAZ aceita — o
próprio módulo pula o CNPJ que ainda está na espera de 1 hora.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from contagem_caixa import sefaz


class Command(BaseCommand):
    help = 'Busca na SEFAZ (NF-e Distribuição DFe) as notas emitidas contra os CNPJs da empresa.'

    def handle(self, *args, **opcoes):
        if not sefaz.configurado():
            self.stdout.write('SEFAZ não configurada (SEFAZ_CERTIFICADO, SEFAZ_CERTIFICADO_SENHA, SEFAZ_CNPJS).')
            return
        for sinc in sefaz.sincronizar_todos():
            if getattr(sinc, 'pulada', False):
                nfe = f'NF-e aguardando até {timezone.localtime(sinc.proxima_consulta):%d/%m %H:%M}'
            else:
                nfe = (f'NF-e: {sinc.notas_novas} nova(s) — {sinc.ultimo_cstat} {sinc.ultima_mensagem} '
                       f'(NSU {sinc.ult_nsu}/{sinc.max_nsu})')
            if getattr(sinc, 'pulada_nfse', False) and sinc.proxima_consulta_nfse:
                nfse = f'NFS-e aguardando até {timezone.localtime(sinc.proxima_consulta_nfse):%d/%m %H:%M}'
            else:
                nfse = f'NFS-e: {sinc.notas_novas_nfse} nova(s) — {sinc.ultimo_status_nfse} (NSU {sinc.ult_nsu_nfse})'
            self.stdout.write(f'{sinc.cnpj}: {nfe} | {nfse}')
