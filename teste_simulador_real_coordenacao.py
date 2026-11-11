"""Simulador do gerente: "Real Coordenação" vem com a Projeção atual da coordenação e vale no cálculo (11/11/2026).

Power BI e MySQL são dublês; transação desfeita no fim.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sim-rc'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sim-rc2'}}
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
    coord = User.objects.create_user(username='zz.sim.coordrc', email='zz.sim.coordrc@exemplo-teste.local',
                                     password='S3nha!teste', first_name='ZZCOORD', last_name='SP', hierarchy='ADMIN')
    acesso = CoordinatorStoreAccess.objects.create(coordinator=coord)
    acesso.sectors.add(a, b)
    gerente = User.objects.create_user(username='zz.sim.gerrc', email='zz.sim.gerrc@exemplo-teste.local',
                                       password='S3nha!teste', first_name='ZZ', last_name='Gerente SP', sector=a,
                                       job_title='GERENTE DE VENDAS')

    fs = sv.get_factor_set(sv.ROLE_GERENTE)
    with mock.patch.object(sv, 'get_metas_from_power_bi', side_effect=metas_falsas), \
            mock.patch.object(sv, 'get_realized_sales_from_mysql', side_effect=vendas_falsas), \
            mock.patch.object(sv, 'get_business_days_info', return_value=(10, 20)):
        proj = sv.compute_gerente_simulation(gerente, fs.data, {}, view_mode=sv.VIEW_PROJECAO)
        real = sv.compute_gerente_simulation(gerente, fs.data, {}, view_mode=sv.VIEW_REALIZADO)
        prefill = sv.build_simulador_prefill(real, sv.ROLE_GERENTE)
        sim_vazio = sv.compute_gerente_simulation(gerente, fs.data, {}, view_mode=sv.VIEW_SIMULADOR,
                                                  simulator_inputs={'movel__real': '4000'})
        sim_dig = sv.compute_gerente_simulation(gerente, fs.data, {}, view_mode=sv.VIEW_SIMULADOR,
                                                simulator_inputs={'movel__real': '4000', 'movel__realpdv': '5000',
                                                                  'fixa__qtypdv': '7', 'sva__realpdv': ''})
        prefill_cons = sv.build_simulador_prefill(real, sv.ROLE_CONSULTOR)
    lp = {r['key']: r for r in proj['rows']}
    print('== PRÉ-PREENCHIMENTO (1ª ENTRADA NO SIMULADOR) ==')
    t('Real Coordenação (Móvel) = Projeção da coordenação', prefill.get('movel__realpdv') == '20000'
      and abs(lp['movel']['coord_proj'] - 20000.0) < 0.01, (prefill.get('movel__realpdv'), lp['movel']['coord_proj']))
    t('Fixa da coordenação: quantidade projetada inteira', prefill.get('fixa__qtypdv') == '40', prefill.get('fixa__qtypdv'))
    t('Real Individual continua com o realizado', prefill.get('movel__real') == '4000', prefill.get('movel__real'))
    t('pilar sem venda fica em branco', 'sva__realpdv' not in prefill)
    t('consultor não ganha campo de coordenação', 'fixa__qtypdv' not in prefill_cons)
    print('\n== O CÁLCULO USA O REAL COORDENAÇÃO ==')
    lv = {r['key']: r for r in sim_vazio['rows']}
    t('em branco: vale a projeção (igual à visão Projeção)', abs(lv['movel']['coord_proj'] - 20000.0) < 0.01
      and abs(lv['fixa']['coord_proj'] - 40.0) < 0.01)
    ld = {r['key']: r for r in sim_dig['rows']}
    t('digitado: Móvel da coordenação = 5000', ld['movel']['coord_proj'] == 5000.0
      and abs(ld['movel']['coord_attainment'] - 0.25) < 0.001, (ld['movel']['coord_proj'], ld['movel']['coord_attainment']))
    t('digitado: Fixa da coordenação = 7', ld['fixa']['coord_proj'] == 7.0, ld['fixa']['coord_proj'])
    t('o individual não muda', ld['movel']['proj'] == lv['movel']['proj'] == 4000.0)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
