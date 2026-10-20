"""Simulador: gerente de SP com "Meta / Proj. / Ating. Coord." (20/10/2026).

Pedido: "Ela é de SP e não aparece Meta Coord., Proj Coord e Ating. Coord.,
ajuste para aparecer conforme a coordenação de SP".

As lojas de SP não têm linha na planilha de comissão, então a coluna
COORDENAÇÃO não existia para elas. Agora a coordenação vem da carteira do
coordenador em /simulator/admin/stores/ (a mesma do cálculo do coordenador):
meta = soma das metas das lojas (Power BI); projeção = vendas dessas lojas.

Power BI e MySQL são dublês; transação desfeita no fim.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sim-sp'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sim-sp2'}}
django.setup()

from django.db import transaction

from simulator import services as sv
from simulator.models import CoordinatorStoreAccess
from users.models import Sector, User

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


META_LOJA = {'movel': 10000.0, 'fixa': 70.0, 'smartphones': 100000.0, 'eletronicos': 15000.0,
             'essenciais': 9000.0, 'seguros': 1000.0, 'sva': 1000.0}
VENDAS = {'ZZ PAULISTA A': 4000.0, 'ZZ PAULISTA B': 6000.0}


def metas_falsas(store_name=None, **kw):
    return dict(META_LOJA) if str(store_name).startswith('ZZ PAULISTA') else {}


def vendas_falsas(pdv=None, pdvs=None, coord_name=None, **kw):
    lojas = pdvs or ([pdv] if pdv else [])
    movel = sum(VENDAS.get(sv.normalize_text(l), 0.0) for l in lojas)
    return {'movel': movel, 'fixa': 0.0, 'fixa_qty': 10.0 * len(lojas), 'smartphones': 0.0, 'eletronicos': 0.0,
            'essenciais': 0.0, 'seguros': 0.0, 'sva': 0.0}


marcador = transaction.atomic()
marcador.__enter__()
try:
    a = Sector.objects.create(name='Loja ZZ Paulista A')
    b = Sector.objects.create(name='Loja ZZ Paulista B')
    coord = User.objects.create_user(username='zz.sim.coordsp', email='zz.sim.coordsp@exemplo-teste.local',
                                     password='S3nha!teste', first_name='ZZCOORD', last_name='SP', hierarchy='ADMIN')
    acesso = CoordinatorStoreAccess.objects.create(coordinator=coord)
    acesso.sectors.add(a, b)
    gerente = User.objects.create_user(username='zz.sim.gersp', email='zz.sim.gersp@exemplo-teste.local',
                                       password='S3nha!teste', first_name='ZZ', last_name='Gerente SP', sector=a,
                                       job_title='GERENTE DE VENDAS')

    print('== A COORDENAÇÃO PELA CARTEIRA ==')
    nome, lojas = sv.coordenacao_pela_carteira('ZZ PAULISTA A')
    t('acha a carteira que tem a loja', nome == 'ZZCOORD' and lojas == ['ZZ PAULISTA A', 'ZZ PAULISTA B'], (nome, lojas))
    t('loja sem carteira não inventa coordenação', sv.coordenacao_pela_carteira('ZZ LOJA SEM CARTEIRA') == ('', []))

    print('\n== O GERENTE DE SP NO SIMULADOR ==')
    fs = sv.get_factor_set(sv.ROLE_GERENTE)
    with mock.patch.object(sv, 'get_metas_from_power_bi', side_effect=metas_falsas), \
            mock.patch.object(sv, 'get_realized_sales_from_mysql', side_effect=vendas_falsas), \
            mock.patch.object(sv, 'get_business_days_info', return_value=(10, 20)):
        sim = sv.compute_gerente_simulation(gerente, fs.data, {}, view_mode=sv.VIEW_PROJECAO)
        real = sv.compute_gerente_simulation(gerente, fs.data, {}, view_mode=sv.VIEW_REALIZADO)
    linhas = {r['key']: r for r in sim['rows']}
    t('a coordenação é a do coordenador da carteira', sim['coordinator'] == 'ZZCOORD', sim['coordinator'])
    t('Meta Coord. = soma das metas das duas lojas', linhas['movel']['coord_meta'] == 20000.0
      and linhas['fixa']['coord_meta'] == 140.0, (linhas['movel']['coord_meta'], linhas['fixa']['coord_meta']))
    t('Proj. Coord. = vendas das duas lojas projetadas', abs(linhas['movel']['coord_proj'] - 20000.0) < 0.01,
      linhas['movel']['coord_proj'])
    t('Ating. Coord. preenchido', abs(linhas['movel']['coord_attainment'] - 1.0) < 0.001, linhas['movel']['coord_attainment'])
    t('Fixa da coordenação em quantidade', abs(linhas['fixa']['coord_proj'] - 40.0) < 0.01, linhas['fixa']['coord_proj'])
    t('o individual da loja não muda', linhas['movel']['meta'] == 10000.0)
    rl = {r['key']: r for r in real['rows']}
    t('no Realizado também', rl['movel']['coord_meta'] == 20000.0 and rl['movel']['coord_proj'] == 10000.0,
      (rl['movel']['coord_meta'], rl['movel']['coord_proj']))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
