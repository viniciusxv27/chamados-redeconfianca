"""Busca na SEFAZ as NF-e emitidas contra os CNPJs da empresa (PIS/Cofins).

Para o agendador (cron): de hora em hora é o ritmo que a SEFAZ aceita — o
próprio módulo pula o CNPJ que ainda está na espera de 1 hora.
"""
from django.core.management.base import BaseCommand

from contagem_caixa import sefaz


class Command(BaseCommand):
    help = 'Busca na SEFAZ (NF-e Distribuição DFe) as notas emitidas contra os CNPJs da empresa.'

    def handle(self, *args, **opcoes):
        if not sefaz.configurado():
            self.stdout.write('SEFAZ não configurada (SEFAZ_CERTIFICADO, SEFAZ_CERTIFICADO_SENHA, SEFAZ_CNPJS).')
            return
        for sinc in sefaz.sincronizar_todos():
            if getattr(sinc, 'pulada', False):
                self.stdout.write(f'{sinc.cnpj}: aguardando até {sinc.proxima_consulta:%d/%m %H:%M}')
            else:
                self.stdout.write(f'{sinc.cnpj}: {sinc.notas_novas} nota(s) nova(s) — '
                                  f'{sinc.ultimo_cstat} {sinc.ultima_mensagem} (NSU {sinc.ult_nsu}/{sinc.max_nsu})')
