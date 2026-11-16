"""Simulador: o coordenador vem da carteira de /simulator/admin/stores/ (16/11/2026).

A coluna COORDENAÇÃO da planilha de comissão guarda a coordenação antiga (ARIEL/LUIZ/THAYANDRA);
as carteiras dizem quem coordena cada loja hoje. Gerente e consultor passam a mostrar o
coordenador da carteira e a calcular a coordenação pelas lojas dela. Dados reais, só leitura,
dentro de uma transação desfeita no fim.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.db import transaction  # noqa: E402

from simulator import services as sv  # noqa: E402
from users.models import User  # noqa: E402

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
    fatores_g = sv.get_factor_set(sv.ROLE_GERENTE).data
    fatores_c = sv.get_factor_set(sv.ROLE_CONSULTOR).data
    real = getattr(sv, 'VIEW_REALIZADO', 'realizado')
    planilha = sv.load_dataframe(sv.ROLE_GERENTE, 'REALIZADO')

    print('== GERENTES: COORDENADOR DA CARTEIRA ==')
    vistos = 0
    for g in sv.get_all_gerentes()[:40]:
        if vistos >= 4:
            break
        if not g.sector_id:
            continue
        nome, lojas = sv.coordenacao_pela_carteira(sv.nome_da_loja(g.sector))
        if not nome:
            continue
        s = sv.compute_gerente_simulation(g, fatores_g, {}, view_mode=real, simulator_inputs={})
        if s.get('error'):
            continue
        vistos += 1
        movel = next(r for r in s['rows'] if r['key'] == 'movel')
        esperado = sv.metas_das_lojas(lojas, planilha)['movel']
        t(f'{g.get_full_name()} ({g.sector.name}): coordenador {nome}', s['coordinator'] == nome, s['coordinator'])
        t(f'  meta da coordenação = soma das {len(lojas)} lojas da carteira', abs((movel['coord_meta'] or 0) - esperado) < 0.01,
          (movel['coord_meta'], esperado))
    t('conferiu ao menos 2 gerentes', vistos >= 2, vistos)

    print('\n== CONSULTORES: COORDENADOR DA CARTEIRA ==')
    vistos = 0
    for c in User.objects.filter(is_active=True, sector__isnull=False).order_by('id'):
        if vistos >= 3:
            break
        if sv.get_user_role(c) != sv.ROLE_CONSULTOR:
            continue
        nome, lojas = sv.coordenacao_pela_carteira(sv.nome_da_loja(c.sector))
        if not nome:
            continue
        s = sv.compute_consultor_simulation(c, fatores_c, {}, view_mode=real, simulator_inputs={})
        if s.get('error') or not s.get('rows'):
            continue
        vistos += 1
        t(f'{c.get_full_name()} ({c.sector.name}): coordenador {nome}', s['coordinator'] == nome, s['coordinator'])
    t('conferiu ao menos 1 consultor', vistos >= 1, vistos)

    print('\n== RESERVA: LOJA SEM CARTEIRA ==')
    t('loja sem carteira não inventa coordenação', sv.coordenacao_pela_carteira('ZZ LOJA QUE NAO EXISTE') == ('', []))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
