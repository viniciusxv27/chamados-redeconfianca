"""Histórico de comissão por cargo e loja (/users/commission/projecao/historico/).

Pedido (13/10/2026): "quero saber quais os valores recebidos do GG do
Montserrat desde que tivemos histórico no portal: filtrar cargo, setor e ter
o valor detalhado por mês".

Cobre: detalhamento que fecha com o valor recebido, filtros (cargo, loja,
pessoa, período), versão em rascunho só para superadmin, escopo do gerente
(só a própria loja) e a exportação. Dados sintéticos em 2099, transação
desfeita no fim.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ch'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ch2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
settings.ALLOWED_HOSTS.append('testserver')

from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client

from users import commission_historico as ch
from users.models import CommissionSpreadsheetVersion as V
from users.models import CommissionUserReferenceHistory as H
from users.models import Sector

U = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def linha_gerente(cargo, pdv, comissao, premio, bonus, acel, iq, desconto):
    base = comissao + premio + bonus
    com_acel = base + acel
    com_iq = com_acel + iq
    return {'NOME': 'x', 'CARGO': cargo, 'PDV': pdv, 'Total Comissão': comissao,
            'TOTAL PREMIAÇÃO ALTO DESEMPENHO': premio, 'TOTAL BONUS LOJA': bonus,
            'TOTAL COMISSÃO + PREMIAÇÃO': base, 'REMUNERAÇÃO TOTAL + ACELERADORES': com_acel,
            'REMUNERÇÃO COM IQ': com_iq, 'DESCONTO_TOTAL': desconto,
            ' REMUNERAÇÃO FINAL TOTAL': com_iq - desconto}


marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='Loja ZZ Histórico')
    outra = Sector.objects.create(name='Loja ZZ Outra')
    gerente = U.objects.create_user(username='zz.ch.ger', email='zz.ch.ger@exemplo-teste.local', password='x',
                                    first_name='Zz', last_name='Gerente', sector=loja)
    versoes = {}
    for mes, status in ((1, 'released'), (2, 'released'), (3, 'draft')):
        versoes[mes] = V.objects.create(year=2099, month=mes, contestacao_phase='pos', status=status,
                                        excel_comissao_url='https://x', excel_vendas_url='https://x',
                                        excel_base_pagamento_url='https://x', excel_base_exclusao_url='https://x')

    def h(mes, nome, papel, dados):
        H.objects.create(year=2099, month=mes, user_name=nome, role=papel, sheet_name=f'S{papel}',
                         source_version=versoes[mes], row_data=dados)
    h(1, 'ANA GG', 'gerente', linha_gerente('GG', 'ZZ HISTÓRICO', 1000, 500, 200, 100, -50, 30))
    h(2, 'ANA GG', 'gerente', linha_gerente('GG', 'ZZ HISTÓRICO', 800, 0, 0, 10, -5, 0))
    h(3, 'BETO GG', 'gerente', linha_gerente('GG', 'ZZ HISTÓRICO', 900, 0, 0, 0, 0, 0))
    h(1, 'CARLA GO', 'gerente', linha_gerente('GO', 'ZZ HISTÓRICO', 300, 0, 0, 0, 0, 0))
    h(1, 'DIEGO GG', 'gerente', linha_gerente('GG', 'ZZ OUTRA', 700, 0, 0, 0, 0, 0))
    h(2, 'ELIS CN', 'cn', {'CARGO': 'CN', 'PDV': 'ZZ HISTÓRICO', 'Total Comissão': 400, ' REMUNERAÇÃO_FINAL_TOTAL': 400})

    print('== O DETALHAMENTO ==')
    original = ch.linhas_do_historico
    so_2099 = lambda rascunho=False: [l for l in original(ver_rascunho=rascunho) if l['ano'] == 2099]
    linhas = so_2099(True)
    ana = next(l for l in linhas if l['pessoa'] == 'ANA GG' and l['mes'] == 1)
    t('recebido = remuneração final da planilha', round(ana['recebido'], 2) == 1720.0, ana['recebido'])
    soma = ana['comissao'] + ana['premiacao'] + ana['bonus_loja'] + ana['aceleradores'] + ana['ajuste_iq'] - ana['descontos']
    t('as partes fecham com o recebido', round(soma, 2) == round(ana['recebido'], 2), (soma, ana))
    t('aceleradores e ajuste do IQ separados', ana['aceleradores'] == 100 and ana['ajuste_iq'] == -50 and ana['descontos'] == 30)
    t('cargo e loja lidos da linha (loja sem acento, casa com o setor)', ana['cargo'] == 'GG' and ana['loja'] == 'ZZ HISTORICO'
      and ch.normalizar(loja.name) == 'ZZ HISTORICO')
    t('CN usa a coluna REMUNERAÇÃO_FINAL_TOTAL', next(l for l in linhas if l['pessoa'] == 'ELIS CN')['recebido'] == 400)
    t('versão em rascunho fica de fora para quem não é superadmin', not any(l['mes'] == 3 for l in so_2099(False)))

    print('\n== A TELA (SUPERADMIN) ==')
    su = U.objects.filter(hierarchy='SUPERADMIN', is_active=True).first()
    c = Client(raise_request_exception=True)
    c.force_login(su)
    with mock.patch.object(ch, 'linhas_do_historico', lambda ver_rascunho=False: so_2099(ver_rascunho)):
        r = c.get('/users/commission/projecao/historico/?cargo=GG&setor=ZZ%20HISTORICO')
        meses = r.context['meses']
        t('GG da loja: um mês por linha, em ordem', [m['rotulo'] for m in meses] == ['Jan/2099', 'Fev/2099', 'Mar/2099'],
          [m['rotulo'] for m in meses])
        t('mostra quem estava no cargo em cada mês', [m['linhas'][0]['pessoa'] for m in meses] == ['ANA GG', 'ANA GG', 'BETO GG'])
        t('o GO e a outra loja ficam de fora', all(l['cargo'] == 'GG' and l['loja'] == 'ZZ HISTORICO' for m in meses for l in m['linhas']))
        t('total do período', round(r.context['total']['recebido'], 2) == round(1720 + 805 + 900, 2), r.context['total']['recebido'])
        t('rascunho marcado na tela', meses[2]['rascunho'] and 'rascunho' in r.content.decode())
        r = c.get('/users/commission/projecao/historico/?cargo=GG&setor=ZZ%20HISTORICO&de=2099-02&ate=2099-02')
        t('filtro de período', [m['rotulo'] for m in r.context['meses']] == ['Fev/2099'])
        r = c.get('/users/commission/projecao/historico/?pessoa=ANA%20GG')
        t('filtro por pessoa', {l['pessoa'] for m in r.context['meses'] for l in m['linhas']} == {'ANA GG'})
        r = c.get('/users/commission/projecao/historico/?setor=ZZ%20HISTORICO')
        t('vários cargos: grade pessoa × mês', len(r.context['grade']) == 4 and 'Pessoa × mês' in r.content.decode())
        r = c.get('/users/commission/projecao/historico/?cargo=GG&setor=ZZ%20HISTORICO&exportar=xlsx')
        t('exporta em Excel', r.status_code == 200 and r['Content-Type'].startswith('application/vnd.openxml'))

    print('\n== ESCOPO ==')
    vistas = ch._escopo(gerente, 'gerente', so_2099())
    t('gerente vê só a própria loja', vistas and all(l['loja'] == 'ZZ HISTORICO' for l in vistas)
      and not any(l['pessoa'] == 'DIEGO GG' for l in vistas))
    vistas = ch._escopo(gerente, 'cn', so_2099())
    t('quem não é gerente nem coordenador não vê os outros', vistas == [])
finally:
    marcador.__exit__(Exception, Exception('rollback'), None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
