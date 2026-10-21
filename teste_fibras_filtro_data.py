"""/fibras: filtro de período pela data da venda (?de=AAAA-MM-DD&ate=AAAA-MM-DD).

Vale para o kanban (todas as colunas, inclusive "Não localizado"), a lista, o
relatório e as exportações (Excel e CSV de canceladas). Data inválida é
ignorada, sem erro. Tudo roda numa transação desfeita no fim; as datas são de
2031 para não cruzar com vendas reais.
"""
import csv
import io
import os
import sys
from datetime import date

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import openpyxl
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()  # deixa a resposta com .context (e e-mail em memória)

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


marcador = transaction.atomic()
marcador.__enter__()
try:
    executor = MigrationExecutor(connection)
    ultima = executor.loader.graph.leaf_nodes('fibras')
    if executor.migration_plan(ultima):
        executor.migrate(ultima)

    from fibras.models import Fibra

    ilha = User.objects.create_user(username='zzfdt.ilha', email='zzfdt.ilha@exemplo-teste.local', password='S3nha!teste',
                                    first_name='ZZ', last_name='IlhaData', hierarchy='SUPERVISOR')
    PDV = 'ZZ LOJA DATAFILTRO'

    def fibra(venda, dia, status=Fibra.STATUS_PENDENTE):
        return Fibra.objects.create(numero_da_venda=venda, numero_protocolo=f'P{venda}', pdv=PDV, data_da_venda=dia,
                                    cpf='12345678909', cliente=f'Cliente {venda}', vendedor='ZZ VENDEDOR DATA',
                                    pilar='FIXA', servico_tecnico='Alta Banda Larga', valor=100, status=status)

    fibra('ZZDTA', date(2031, 3, 1))                                 # pendente → "Não localizado"
    fibra('ZZDTB', date(2031, 3, 15), Fibra.STATUS_CANCELADO)
    fibra('ZZDTC', date(2031, 4, 2), Fibra.STATUS_INSTALADO)
    fibra('ZZDTD', date(2031, 3, 31))                                # pendente → "Não localizado" (limite do período)
    fibra('ZZDTE', date(2031, 5, 10))                                # pendente → "Não localizado", fora de março
    TODAS = {'ZZDTA', 'ZZDTB', 'ZZDTC', 'ZZDTD', 'ZZDTE'}
    MARCO = {'ZZDTA', 'ZZDTB', 'ZZDTD'}

    c = Client()
    c.force_login(ilha)

    def kanban(params):
        r = c.get('/fibras/', {'pdv': PDV, **params})
        cols = {col['key']: {f.numero_da_venda for f in col['items']} for col in r.context['colunas']}
        return r, cols

    def todas(cols):
        s = set()
        for v in cols.values():
            s |= v
        return s

    print('== KANBAN ==')
    r, cols = kanban({})
    t('sem período abre no mês corrente (nada de 2031)', r.status_code == 200 and not todas(cols) & TODAS, todas(cols))
    r, cols = kanban({'periodo': 'todos'})
    t('?periodo=todos mostra tudo', r.status_code == 200 and todas(cols) == TODAS, todas(cols))
    r, cols = kanban({'de': '2031-03-01', 'ate': '2031-03-31'})
    t('março: só as vendas de março (limites inclusivos)', todas(cols) == MARCO, todas(cols))
    t('"Não localizado" também respeita o período', cols['nao_localizado'] == {'ZZDTA', 'ZZDTD'}, cols['nao_localizado'])
    t('cancelada de março na coluna dela', cols[Fibra.STATUS_CANCELADO] == {'ZZDTB'}, cols[Fibra.STATUS_CANCELADO])
    nao = next(col for col in r.context['colunas'] if col['key'] == 'nao_localizado')
    t('contagem da coluna bate com o período', nao['count'] == 2, nao['count'])
    html = r.content.decode()
    t('campos de data preenchidos', 'name="de" value="2031-03-01"' in html and 'name="ate" value="2031-03-31"' in html)
    t('atalhos de período no painel', 'data-fb-periodo="hoje"' in html and 'data-fb-periodo="mes_passado"' in html)
    t('chip do período ativo (mês inteiro pelo nome)', 'Período: <b>março de 2031</b>' in html)
    t('o "x" do chip mostra todo o período (mantém o PDV)',
      'href="/fibras/?pdv=ZZ+LOJA+DATAFILTRO&amp;periodo=todos" class="fb-periodo-x"' in html)
    t('a navegação leva o período', 'href="/fibras/lista/?de=2031-03-01&amp;ate=2031-03-31"' in html)
    t('o CSV de canceladas leva o período', '/fibras/export/canceladas-csv/?de=2031-03-01&amp;ate=2031-03-31' in html)

    r, cols = kanban({'de': '2031-03-16'})
    t('só "de": a partir da data', todas(cols) == {'ZZDTC', 'ZZDTD', 'ZZDTE'}, todas(cols))
    t('rótulo "a partir de"', 'a partir de 16/03/2031' in r.content.decode())
    r, cols = kanban({'ate': '2031-03-15'})
    t('só "até": até a data', todas(cols) == {'ZZDTA', 'ZZDTB'}, todas(cols))
    r, cols = kanban({'de': '2031-03-31', 'ate': '2031-03-01'})
    t('período invertido é desinvertido', todas(cols) == MARCO, todas(cols))

    print('\n== DATA INVÁLIDA (ignorada) ==')
    for ruim in ('2031-13-45', 'ontem', '31/03/2031', "2031-03-01' OR 1=1"):
        r, cols = kanban({'de': ruim, 'ate': '2031-03-31'})
        t(f'kanban de={ruim!r}: 200 e só o "até" vale', r.status_code == 200 and todas(cols) == MARCO, todas(cols))
    r = c.get('/fibras/', {'pdv': PDV, 'de': 'xx', 'ate': 'yy'})
    t('ambas inválidas: sem filtro e sem chip', r.status_code == 200
      and {f.numero_da_venda for col in r.context['colunas'] for f in col['items']} == TODAS
      and 'class="fb-periodo-chip"' not in r.content.decode())
    t('lista com data inválida não quebra', c.get('/fibras/lista/', {'de': '2031-02-30'}).status_code == 200)
    t('relatório com data inválida não quebra', c.get('/fibras/relatorio/', {'ate': 'abc'}).status_code == 200)
    t('export com data inválida não quebra', c.get('/fibras/export/excel/', {'de': '2031-99-01'}).status_code == 200)

    print('\n== LISTA ==')
    r = c.get('/fibras/lista/', {'pdv': PDV, 'de': '2031-03-01', 'ate': '2031-03-31'})
    t('lista: só março', {f.numero_da_venda for f in r.context['fibras']} == MARCO)
    r = c.get('/fibras/lista/', {'pdv': PDV, 'status': Fibra.STATUS_PENDENTE, 'de': '2031-03-01', 'ate': '2031-03-31'})
    t('lista: período + status juntos', {f.numero_da_venda for f in r.context['fibras']} == {'ZZDTA', 'ZZDTD'})
    t('lista: chip mantém status e PDV no "x"',
      'href="/fibras/lista/?pdv=ZZ+LOJA+DATAFILTRO&amp;status=pendente" class="fb-periodo-x"' in r.content.decode())

    print('\n== RELATÓRIO ==')
    r = c.get('/fibras/relatorio/', {'de': '2031-03-01', 'ate': '2031-03-31'})
    ctx = r.context
    t('relatório: total do período', r.status_code == 200 and ctx['total'] == 3, ctx['total'])
    t('relatório: canceladas e não localizadas do período', ctx['qtd_cancel'] == 1 and ctx['qtd_nao_loc'] == 2,
      (ctx['qtd_cancel'], ctx['qtd_nao_loc']))
    t('relatório: receita do período', float(ctx['receita_total']) == 300.0, ctx['receita_total'])
    html = r.content.decode()
    t('relatório: Excel leva o período', 'href="/fibras/export/excel/?de=2031-03-01&amp;ate=2031-03-31"' in html)
    t('relatório: Excel de canceladas leva status + período',
      'href="/fibras/export/excel/?status=cancelado&amp;de=2031-03-01&amp;ate=2031-03-31"' in html)
    t('relatório: atalhos para a lista levam o período', '/fibras/lista/?status=instalado&amp;de=2031-03-01&amp;ate=2031-03-31' in html)
    t('relatório: painel de período', 'name="de" value="2031-03-01"' in html and 'class="fb-periodo-chip"' in html)

    print('\n== EXPORTAÇÕES ==')

    def vendas_excel(params):
        r = c.get('/fibras/export/excel/', params)
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).active
        return {row[0] for row in ws.iter_rows(min_row=2, values_only=True) if str(row[0]).startswith('ZZDT')}

    t('Excel: só março', vendas_excel({'de': '2031-03-01', 'ate': '2031-03-31'}) == MARCO)
    t('Excel: status + período', vendas_excel({'status': 'cancelado', 'de': '2031-03-01', 'ate': '2031-03-31'}) == {'ZZDTB'})
    t('Excel: abril', vendas_excel({'de': '2031-04-01', 'ate': '2031-04-30'}) == {'ZZDTC'})

    def protocolos_csv(params):
        r = c.get('/fibras/export/canceladas-csv/', params)
        texto = r.content.decode('utf-8-sig')
        return {linha[9] for linha in csv.reader(io.StringIO(texto), delimiter=';') if len(linha) > 9 and linha[9].startswith('PZZDT')}

    t('CSV de canceladas: período com a cancelada', protocolos_csv({'de': '2031-03-01', 'ate': '2031-03-31'}) == {'PZZDTB'})
    t('CSV de canceladas: período sem ela', protocolos_csv({'de': '2031-04-01', 'ate': '2031-04-30'}) == set())
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
