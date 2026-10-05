"""Simulador, visão de coordenador: a meta de Fixa é a soma das QUANTIDADES das lojas.

Pedido: "em /simulator/ (visão de coordenador): a meta de fixa está aparentemente
errada, lembrando que não deve ser a soma dos valores, e sim a soma das
quantidades das metas das lojas desse coordenador".

A coluna META_FIXA da planilha COORDENADOR é em reais por consultor; somada, a
coordenação do LUIZ tinha "meta" 34.210 — as metas de Fixa das 6 lojas dele somam
314. Consultor e gerente já usavam a soma das lojas (``meta_fixa_da_coordenacao``);
agora o coordenador também.

Desde 05/10/2026 as lojas do coordenador saem da carteira de
/simulator/admin/stores/ (``pdvs_da_carteira``), não mais do nome na coluna
COORDENAÇÃO da planilha: o Pedro (coordenador novo) saía zerado e o Luiz, que
trocou de lojas, era calculado com as antigas. Sem carteira, vale a planilha.

Dados reais, só leitura (o simulador lê a planilha e o banco de vendas; nada é
gravado — a transação é desfeita no fim).
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.conf import settings

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.db import transaction
from django.test import Client

from simulator import services as sv
from users.models import User

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
    print('== A CONTA ==')
    chamadas = []

    def soma_das_lojas(realized, projection, coord_name):
        chamadas.append(coord_name)
        return 314.0

    realizado = getattr(sv, 'VIEW_REALIZADO', 'realizado')
    fatores = sv.get_factor_set(sv.ROLE_COORDENADOR).data
    luiz = User.objects.filter(pk=135, is_active=True).first()
    thayandra = User.objects.filter(pk=181, is_active=True).first()
    alvo = luiz or thayandra
    if alvo is None:
        print('  (coordenadores 135 e 181 inativos: conferência com dados reais pulada)')
    else:
        # Sem carteira: o caminho antigo, pela planilha.
        with mock.patch.object(sv, 'meta_fixa_da_coordenacao', side_effect=soma_das_lojas), \
                mock.patch.object(sv, 'pdvs_da_carteira', return_value=[]):
            s = sv.compute_coordenador_simulation(alvo, fatores, {}, view_mode=realizado, simulator_inputs={})
        fixa = next(r for r in s['rows'] if r['key'] == 'fixa')
        t('sem carteira, o coordenador usa a mesma soma das lojas do consultor e do gerente',
          chamadas == [alvo.first_name] and fixa['meta'] == 314.0, (chamadas, fixa['meta']))
        with mock.patch.object(sv, 'meta_fixa_da_coordenacao', side_effect=soma_das_lojas):
            s = sv.compute_coordenador_simulation(alvo, fatores, {}, view_mode=sv.VIEW_SIMULADOR,
                                                  simulator_inputs={'fixa__meta': '500'})
        fixa = next(r for r in s['rows'] if r['key'] == 'fixa')
        t('a meta digitada no próprio simulador continua valendo por cima', fixa['meta'] == 500.0, fixa['meta'])

    print('\n== COM OS DADOS REAIS (lojas da carteira) ==')
    planilha = sv.load_dataframe(sv.ROLE_COORDENADOR, 'REALIZADO')
    projecao = sv.load_dataframe(sv.ROLE_COORDENADOR, 'PROJEÇÃO')
    # Quem tem carteira com meta de Fixa (o interior não tem: lá a Fixa não conta).
    com_fixa = [u for u in User.objects.filter(coordinator_store_access__isnull=False, is_active=True)
                .order_by('id')
                if not sv.is_sniper_user(u)
                and sum(float((sv.get_metas_from_power_bi(store_name=l) or {}).get('fixa') or 0)
                        for l in sv.pdvs_da_carteira(u)) > 0][:2]
    for coordenador in com_fixa:
        nome = coordenador.first_name
        lojas = sv.pdvs_da_carteira(coordenador)
        oficial = sum(float((sv.get_metas_from_power_bi(store_name=loja) or {}).get('fixa') or 0)
                      for loja in lojas or [])
        em_reais = sum(sv.sumifs(planilha, 'META_FIXA', 'PDV', l) for l in lojas)
        s = sv.compute_coordenador_simulation(coordenador, fatores, {}, view_mode=realizado, simulator_inputs={})
        fixa = next(r for r in s['rows'] if r['key'] == 'fixa')
        t(f'{nome}: meta de Fixa = soma das metas das {len(lojas or [])} lojas ({oficial:.0f}), '
          f'não a soma em reais ({em_reais:,.0f})',
          lojas and fixa['meta'] == oficial and 0 < oficial < 5000 and oficial != em_reais,
          (fixa['meta'], oficial, em_reais))
        t(f'{nome}: o atingimento de Fixa volta a fazer sentido (quantidade sobre quantidade)',
          fixa['attainment'] == (fixa['quantity'] / fixa['meta'] if fixa['meta'] else 0),
          (fixa['quantity'], fixa['meta'], fixa['attainment']))

    if com_fixa:
        quem = com_fixa[0]
        c = Client()
        c.force_login(quem)
        html = c.get('/simulator/', {'fragment': 'results', 'view': realizado}).content.decode()
        lojas = sv.pdvs_da_carteira(quem)
        oficial = sum(float((sv.get_metas_from_power_bi(store_name=loja) or {}).get('fixa') or 0) for loja in lojas)
        from simulator.templatetags.simulator_tags import number_br
        texto_oficial = number_br(oficial, 0)
        em_reais = number_br(sum(sv.sumifs(planilha, 'META_FIXA', 'PDV', l) for l in lojas), 0)
        t(f'a tela do {quem.first_name} mostra {texto_oficial} na Fixa (e não {em_reais})',
          f'>{texto_oficial}<' in html.replace(' ', '').replace('\n', '') and f'>{em_reais}<' not in html.replace(' ', ''),
          texto_oficial)
    print('\n== A CARTEIRA MANDA ==')
    from simulator.models import CoordinatorStoreAccess
    from users.models import Sector
    novo = User.objects.create_user(username='zz.coord.carteira', email='zz.coord@exemplo-teste.local',
                                    password='x', first_name='ZZNINGUEM', last_name='Teste')
    t('nome fora da planilha e sem carteira: continua zerado (nada a somar)',
      sv.compute_coordenador_simulation(novo, fatores, {}, view_mode=sv.VIEW_PROJECAO)['coord_pdvs'] == [])
    acesso = CoordinatorStoreAccess.objects.create(coordinator=novo)
    lojas_gv = list(Sector.objects.filter(name__in=['Loja Glória', 'Loja Centro VIX']))
    acesso.sectors.set(lojas_gv)
    t('a carteira vira a lista de lojas do cálculo',
      sorted(sv.pdvs_da_carteira(novo)) == ['CENTRO VIX', 'GLÓRIA'], sv.pdvs_da_carteira(novo))

    pedidos = []

    def vendas(**kw):
        pedidos.append(kw)
        return {'movel': 1000.0, 'fixa': 500.0, 'smartphones': 2000.0, 'eletronicos': 300.0,
                'essenciais': 200.0, 'seguros': 50.0, 'sva': 40.0, 'fixa_qty': 10.0}

    with mock.patch.object(sv, 'get_realized_sales_from_mysql', side_effect=vendas):
        s = sv.compute_coordenador_simulation(novo, fatores, {}, view_mode=sv.VIEW_PROJECAO)
    t('as vendas são buscadas pelas lojas da carteira, não pelo nome',
      pedidos and sorted(pedidos[0].get('pdvs') or []) == ['CENTRO VIX', 'GLÓRIA'], pedidos)
    metas = {r['key']: r['meta'] for r in s['rows']}
    esperado = sv.metas_das_lojas(['CENTRO VIX', 'GLÓRIA'], planilha)
    t('meta da coordenação = soma das metas das lojas da carteira',
      metas.get('movel') == esperado['movel'] and metas.get('fixa') == esperado['fixa']
      and esperado['movel'] > 0, (metas, esperado))
    t('o coordenador novo deixa de sair zerado', s['totals']['ganho_total'] > 0, s['totals'])

    # Interior: nenhuma loja com meta de Fixa — a Fixa não pode travar o bônus 6/7.
    interior = list(Sector.objects.filter(name__in=['Loja Piuma', 'Loja Anchieta']))
    acesso.sectors.set(interior)
    esperado = sv.metas_das_lojas(sv.pdvs_da_carteira(novo), planilha)
    t('carteira do interior não tem meta de Fixa', esperado['fixa'] == 0, esperado['fixa'])
    tudo_100 = {k: 1.0 for k in ('movel', 'smartphones', 'eletronicos', 'essenciais', 'seguros', 'sva')}
    t('sem meta de Fixa, o bônus não exige Fixa (era a regra "ARIEL", agora pelas lojas)',
      sv.bonus_6_7_ok(dict(tudo_100, fixa=0.0), 'ZZNINGUEM', exige_fixa=False))
    t('com meta de Fixa, Fixa abaixo de 100% segura o bônus',
      not sv.bonus_6_7_ok(dict(tudo_100, fixa=0.5), 'ZZNINGUEM', exige_fixa=True))
    t('sem carteira, a regra antiga pelo nome continua',
      sv.bonus_6_7_ok(dict(tudo_100, fixa=0.0), 'ARIEL')
      and not sv.bonus_6_7_ok(dict(tudo_100, fixa=0.0), 'LUIZ'))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
