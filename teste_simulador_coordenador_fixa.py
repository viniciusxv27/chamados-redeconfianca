"""Simulador, visão de coordenador: a meta de Fixa é a soma das QUANTIDADES das lojas.

Pedido: "em /simulator/ (visão de coordenador): a meta de fixa está aparentemente
errada, lembrando que não deve ser a soma dos valores, e sim a soma das
quantidades das metas das lojas desse coordenador".

A coluna META_FIXA da planilha COORDENADOR é em reais por consultor; somada, a
coordenação do LUIZ tinha "meta" 34.210 — as metas de Fixa das 6 lojas dele somam
314. Consultor e gerente já usavam a soma das lojas (``meta_fixa_da_coordenacao``);
agora o coordenador também.

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
        with mock.patch.object(sv, 'meta_fixa_da_coordenacao', side_effect=soma_das_lojas):
            s = sv.compute_coordenador_simulation(alvo, fatores, {}, view_mode=realizado, simulator_inputs={})
        fixa = next(r for r in s['rows'] if r['key'] == 'fixa')
        t('o coordenador usa a mesma soma das lojas do consultor e do gerente',
          chamadas == [alvo.first_name] and fixa['meta'] == 314.0, (chamadas, fixa['meta']))
        with mock.patch.object(sv, 'meta_fixa_da_coordenacao', side_effect=soma_das_lojas):
            s = sv.compute_coordenador_simulation(alvo, fatores, {}, view_mode=sv.VIEW_SIMULADOR,
                                                  simulator_inputs={'fixa__meta': '500'})
        fixa = next(r for r in s['rows'] if r['key'] == 'fixa')
        t('a meta digitada no próprio simulador continua valendo por cima', fixa['meta'] == 500.0, fixa['meta'])

    print('\n== COM OS DADOS REAIS ==')
    planilha = sv.load_dataframe(sv.ROLE_COORDENADOR, 'REALIZADO')
    projecao = sv.load_dataframe(sv.ROLE_COORDENADOR, 'PROJEÇÃO')
    for coordenador in (luiz, thayandra):
        if coordenador is None:
            continue
        nome = coordenador.first_name
        lojas = sv.get_pdvs_of_coord(planilha, nome) or sv.get_pdvs_of_coord(projecao, nome)
        oficial = sum(float((sv.get_metas_from_power_bi(store_name=loja) or {}).get('fixa') or 0)
                      for loja in lojas or [])
        em_reais = sv.sumifs(planilha, 'META_FIXA', 'COORDENAÇÃO', nome)
        s = sv.compute_coordenador_simulation(coordenador, fatores, {}, view_mode=realizado, simulator_inputs={})
        fixa = next(r for r in s['rows'] if r['key'] == 'fixa')
        t(f'{nome}: meta de Fixa = soma das metas das {len(lojas or [])} lojas ({oficial:.0f}), '
          f'não a soma em reais ({em_reais:,.0f})',
          lojas and fixa['meta'] == oficial and 0 < oficial < 5000 and oficial != em_reais,
          (fixa['meta'], oficial, em_reais))
        t(f'{nome}: o atingimento de Fixa volta a fazer sentido (quantidade sobre quantidade)',
          fixa['attainment'] == (fixa['quantity'] / fixa['meta'] if fixa['meta'] else 0),
          (fixa['quantity'], fixa['meta'], fixa['attainment']))

    if luiz:
        c = Client()
        c.force_login(luiz)
        html = c.get('/simulator/', {'fragment': 'results', 'view': realizado}).content.decode()
        lojas = sv.get_pdvs_of_coord(planilha, luiz.first_name)
        oficial = sum(float((sv.get_metas_from_power_bi(store_name=loja) or {}).get('fixa') or 0) for loja in lojas)
        from simulator.templatetags.simulator_tags import number_br
        texto_oficial = number_br(oficial, 0)
        em_reais = number_br(sv.sumifs(planilha, 'META_FIXA', 'COORDENAÇÃO', luiz.first_name), 0)
        t(f'a tela do LUIZ mostra {texto_oficial} na Fixa (e não {em_reais})',
          f'>{texto_oficial}<' in html.replace(' ', '').replace('\n', '') and f'>{em_reais}<' not in html.replace(' ', ''),
          texto_oficial)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
