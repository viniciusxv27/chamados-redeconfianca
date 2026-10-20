"""/fibras — a divergência do Resultado (20/10/2026).

Três causas, cada uma com o seu teste:

1. a loja: a Base grava "MONTSERRAT" e o setor é "Loja Montserrat" — com o
   prefixo, 16 das 19 lojas ficavam sem ADABAS e a venda não casava;
2. o período: o sync trazia só o mês corrente, e a Esteira cobre meses
   (ex.: 01/08 a 05/10) — agosto e setembro viravam "venda não lançada";
3. a limpeza pós-importação apagava as vendas do Vivo GO que não casaram
   naquele cruzamento (jul–set sumiram assim).

O Vivo GO é um dublê; transação desfeita no fim (datas de 2031).
"""
import os
import sys
from datetime import date, datetime, timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fd'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fd2'}}
django.setup()

import pandas as pd
from django.db import transaction
from django.utils import timezone

from fibras import cruzamento as cz
from fibras import services
from fibras.models import Fibra

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


print('== 1. A LOJA ==')
lojas = pd.DataFrame([('ESD0267-004', 'Loja Montserrat'), ('ESD0267-017', 'Loja Glória'),
                      ('ESD0267-018', 'Loja Centro VIX'), ('ESD0267-007', 'Loja Marcilio De Noronha')],
                     columns=['ADABAS', 'PDV'])
m = cz.sugerir_mapeamento(['MONTSERRAT', 'GLÓRIA', 'CENTRO VIX', 'MARCILIO DE NORONHA', 'SEDE ADMINISTRATIVA'],
                          lojas, 'ADABAS', 'PDV')
mapa = dict(zip(m['FILIAL (Base de Vendas)'], m['ADABAS']))
t('"MONTSERRAT" acha "Loja Montserrat"', mapa['MONTSERRAT'] == 'ESD0267-004', mapa)
t('com acento ("GLÓRIA" = "Loja Glória")', mapa['GLÓRIA'] == 'ESD0267-017')
t('nome curto ("CENTRO VIX")', mapa['CENTRO VIX'] == 'ESD0267-018')
t('e o nome longo continua', mapa['MARCILIO DE NORONHA'] == 'ESD0267-007')
t('escritório segue sem loja', mapa['SEDE ADMINISTRATIVA'] == '')
t('pelo nome idêntico, não pelo "parecido"', set(m['Origem'][:4]) == {'Nome idêntico'}, list(m['Origem']))

print('\n== 2. O PERÍODO ==')
t('meses entre 01/08 e 05/10', services.meses_entre(date(2031, 8, 1), date(2031, 10, 5)) == [(2031, 8), (2031, 9), (2031, 10)])
t('virada de ano', services.meses_entre(date(2031, 12, 30), date(2032, 1, 2)) == [(2031, 12), (2032, 1)])

VIVOGO = {
    (2031, 8): [dict(numero_da_venda='ZZ-AGO-1', numero_protocolo='111', pdv='MONTSERRAT', vendedor='ZZ V',
                     data_da_venda=datetime(2031, 8, 3, 10, 0), pilar='FIXA', servico_tecnico='Alta Banda Larga',
                     venda_ativa='1', valor=99)],
    (2031, 9): [dict(numero_da_venda='ZZ-SET-1', numero_protocolo='222', pdv='GLÓRIA', vendedor='ZZ V',
                     data_da_venda=date(2031, 9, 4), pilar='FIXA', servico_tecnico='Alta Banda Larga',
                     venda_ativa='0', valor=120)],
}
pedidos = []


def vivogo_falso(*, year=None, month=None):
    pedidos.append((year, month))
    return [dict(r) for r in VIVOGO.get((year, month), [])]


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(services, 'fetch_fibras_from_mysql', vivogo_falso):
        st = services.sync_fibras_periodo(date(2031, 8, 1), date(2031, 9, 30))
        hoje = timezone.localdate()
        t('consulta todos os meses do período (e o corrente)',
          (2031, 8) in pedidos and (2031, 9) in pedidos and (hoje.year, hoje.month) in pedidos, pedidos)
        ago = Fibra.objects.filter(numero_da_venda='ZZ-AGO-1').first()
        t('a venda de agosto entra na Base', ago is not None and ago.pdv == 'MONTSERRAT' and ago.status == Fibra.STATUS_AGENDADO)
        t('data com hora vira data', ago and ago.data_da_venda == date(2031, 8, 3))
        st_ = Fibra.objects.get(numero_da_venda='ZZ-SET-1')
        t('cancelada no Vivo GO nasce cancelada', st_.status == Fibra.STATUS_CANCELADO)
        t('contagem', st['created'] == 2 and st['meses'] >= 2, st)

        # A ilha mexeu no status; o sync não pode desfazer, só cancelar.
        Fibra.objects.filter(pk=ago.pk).update(status=Fibra.STATUS_INSTALADO if hasattr(Fibra, 'STATUS_INSTALADO') else 'instalado',
                                               valor=1)
        VIVOGO[(2031, 8)][0]['venda_ativa'] = '1'
        services.sync_fibras(year=2031, month=8)
        ago.refresh_from_db()
        t('venda existente: atualiza os dados do Vivo GO', ago.valor == 99)
        t('e não mexe no status da ilha', ago.status != Fibra.STATUS_AGENDADO)
        VIVOGO[(2031, 8)][0]['venda_ativa'] = '0'
        services.sync_fibras(year=2031, month=8)
        ago.refresh_from_db()
        t('cancelada depois no Vivo GO: vira cancelada', ago.status == Fibra.STATUS_CANCELADO)

    print('\n== 3. A LIMPEZA NÃO APAGA VENDA REAL ==')
    antiga = timezone.now() - timedelta(days=30)
    Fibra.objects.filter(numero_da_venda__in=['ZZ-AGO-1', 'ZZ-SET-1']).update(last_planilha_at=antiga)
    so_planilha = Fibra.objects.create(numero_da_venda='ZZ-PLAN-1', pilar='', last_planilha_at=antiga)
    removidas = services._prune_stale_fibras(import_ts=timezone.now())
    t('venda do Vivo GO (FIXA) fica, mesmo sem casar nesta importação',
      Fibra.objects.filter(numero_da_venda__in=['ZZ-AGO-1', 'ZZ-SET-1']).count() == 2)
    t('o que nasceu só da planilha ainda sai', not Fibra.objects.filter(pk=so_planilha.pk).exists() and removidas == 1, removidas)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
