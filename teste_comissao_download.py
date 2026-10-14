"""Comissionamento: a planilha baixada do OneDrive (/users/commission/, visão de CN).

Pedido (14/10/2026): "na visão de CN está dando erro ao ler planilha".

A planilha de comissão passou de 5 MB. O arquivo baixado ia para o Redis
compartilhado, que tem 3 s de limite por operação: a gravação estourava, nada
ficava guardado e cada tela baixava tudo de novo do OneDrive — até ele
responder com uma página HTML (limite de acessos), que o código aceitava como
planilha (bastava ter mais de 1.000 bytes) e o pandas não conseguia ler.

Cobre: página HTML recusada, planilha válida guardada na memória do processo
(e não no Redis), reaproveitada sem ir ao OneDrive, cópia de reserva quando o
OneDrive falha, "Limpar cache" fazendo baixar de novo e a busca por nome que
não casa com linha vazia. Sem rede: o OneDrive é um dublê.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cd'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cd2'}}
django.setup()

from io import BytesIO
from types import SimpleNamespace
from unittest import mock

import openpyxl
from django.core.cache import cache, caches

from users import commission_views as cv

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def planilha(linhas):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = cv.SHEET_CN
    for linha in linhas:
        ws.append(linha)
    saida = BytesIO()
    wb.save(saida)
    return saida.getvalue()


HTML = b'<!DOCTYPE html><html><head><title>OneDrive</title></head><body>' + b'x' * 3000 + b'</body></html>'
XLSX = planilha([['NOME', 'CARGO', ' REMUNERAÇÃO_FINAL_TOTAL'], [None, None, None],
                 ['ANA SOUZA', 'CN', 1500.5], ['BRUNO LIMA', 'CN', 900]])
URL = 'https://onedrive.live.com/zz-planilha-de-teste'


class OneDrive:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = 0

    def __call__(self, url, **kwargs):
        self.chamadas += 1
        corpo = self.respostas.pop(0) if self.respostas else HTML
        tipo = 'text/html' if corpo.startswith(b'<') else 'application/octet-stream'
        return SimpleNamespace(status_code=200, content=corpo, headers={'Content-Type': tipo})


def baixar(onedrive):
    with mock.patch.object(cv.requests, 'get', onedrive):
        return cv.download_excel_file(URL, 'comissao_teste')


print('== O QUE O ONEDRIVE DEVOLVE ==')
od = OneDrive(HTML, HTML, HTML)
arquivo, erro = baixar(od)
t('página HTML não é aceita como planilha', arquivo is None and 'página' in (erro or ''), erro)
t('e nada fica guardado', caches['local'].get(f'planilha_{cv.hashlib.md5(URL.encode()).hexdigest()[:10]}_v0') is None)

od = OneDrive(XLSX)
arquivo, erro = baixar(od)
t('planilha de verdade é aceita', arquivo is not None and erro is None, erro)
t('o arquivo não vai para o Redis compartilhado', not any(isinstance(v, bytes) and len(v) > 1000
                                                          for v in cache._cache.values()))

od = OneDrive()
arquivo, erro = baixar(od)
t('a segunda leitura sai da memória, sem ir ao OneDrive', arquivo is not None and od.chamadas == 0, od.chamadas)

print('\n== QUANDO O ONEDRIVE FALHA ==')
cv.esquecer_planilhas_baixadas()                    # o mesmo que o "Limpar cache"
od = OneDrive(HTML, HTML, HTML)
arquivo, erro = baixar(od)
t('depois de limpar o cache, tenta baixar de novo', od.chamadas >= 1, od.chamadas)
t('e com o OneDrive fora, usa a última cópia boa', arquivo is not None and erro is None, erro)

print('\n== A ABA DE CN ==')
cv.esquecer_planilhas_baixadas()
caches['local'].clear()
with mock.patch.object(cv.requests, 'get', OneDrive(XLSX, XLSX, XLSX, XLSX)):
    dados = cv.fetch_excel_data(cv.SHEET_CN, 'Bruno Lima', excel_url=URL)
t('acha a pessoa certa mesmo com linha vazia antes dela', dados.get('success') and dados['data'].get('NOME') == 'BRUNO LIMA',
  dados)
cache.clear()
caches['local'].clear()
cv.esquecer_planilhas_baixadas()
with mock.patch.object(cv.requests, 'get', OneDrive(HTML, HTML, HTML)):
    dados = cv.fetch_excel_data(cv.SHEET_CN, 'Ana Souza', excel_url=URL)
t('OneDrive fora e sem cópia: avisa que não baixou (não "erro ao ler")', 'Erro ao baixar planilha' in (dados.get('error') or ''),
  dados)

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
