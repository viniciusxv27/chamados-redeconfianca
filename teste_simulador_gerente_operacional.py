"""Simulador: gerente operacional com 75% do gerente de vendas, e Meta Coord. da Fixa em quantidade.

Pedidos:
- "Usuários com o cargo que contém GERENTE OPERACIONAL devem ter a comissão 75%
  em cima do usuário com o cargo que contém GERENTE DE VENDAS do mesmo setor."
- "A Fixa Meta Coord para o Gerente está puxando errado": o realizado e a
  projeção da coordenação são quantidade, mas a meta vinha da planilha em reais
  (THAYANDRA: 36.850), e o atingimento saía perto de 0%.

A primeira parte usa dublês (o cálculo base do gerente é trocado por números
fixos). A segunda lê os dados reais do simulador — só leitura (planilha, Power BI
e SELECT no MySQL). Tudo que é gravado roda numa transação desfeita no fim.
"""
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
django.setup()

from django.db import transaction

from communications.models import CommunicationGroup
from simulator import services as sv
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


def base_falsa_factory(chamados):
    def base_falsa(user, factor_data, hunter_levels=None, view_mode=None, simulator_inputs=None):
        chamados.append(user)
        return {
            'rows': [{'key': 'movel', 'meta': 1000.0, 'proj': 800.0, 'attainment': 0.8, 'coord_meta': 5000.0,
                      'commission_value': 100.0, 'premium_value': 40.0, 'total_individual': 140.0,
                      'pdv_premium_value': 20.0, 'total_with_pdv': 160.0, 'hunter2_value': 8.0, 'hunter3_value': 0.0}],
            'totals': {'total_with_pdv': 160.0, 'hunter2': 8.0, 'hunter3': 0.0, 'bonus_6_7': 16.0, 'ganho_total': 184.0},
        }
    return base_falsa


marcador = transaction.atomic()
marcador.__enter__()
try:
    setor = Sector.objects.create(name='ZZ Loja Simulador Operacional')
    sem_gv = Sector.objects.create(name='ZZ Loja Sem Gerente de Vendas')

    def novo(apelido, cargo, setor_do_usuario):
        return User.objects.create_user(
            username=f'zzsim.{apelido}', email=f'zzsim.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name='ZZSim', last_name=apelido.title(), sector=setor_do_usuario, job_title=cargo, hierarchy='PADRAO')

    vendas = novo('vendas', 'GERENTE DE VENDAS', setor)
    vendas_ii = novo('vendasii', 'GERENTE DE VENDAS II', setor)
    operacional = novo('operacional', 'Gerente  Operacional I', setor)
    orfao = novo('orfao', 'GERENTE OPERACIONAL', sem_gv)
    grupo = CommunicationGroup.objects.filter(name__icontains='GERENTES').exclude(name__icontains='COORDENADORES').first()
    assert grupo is not None, 'o grupo GERENTES não existe no banco'

    print('== QUEM É GERENTE OPERACIONAL ==')
    t('o cargo vale em qualquer nível e caixa', sv.is_gerente_operacional(operacional) and sv.is_gerente_operacional(orfao)
      and not sv.is_gerente_operacional(vendas))
    t('usa o cálculo de gerente mesmo fora do grupo GERENTES', sv.get_user_role(operacional) == sv.ROLE_GERENTE,
      sv.get_user_role(operacional))
    t('e aparece no seletor de gerentes do simulador', operacional.pk in {u.pk for u in sv.get_all_gerentes()})

    print('\n== QUEM É A BASE ==')
    t('sem ninguém no grupo: o cargo sem nível ganha do "II"', sv.get_gerente_de_vendas_do_setor(operacional) == vendas)
    grupo.members.add(vendas_ii)
    t('quem está no grupo GERENTES tem prioridade', sv.get_gerente_de_vendas_do_setor(operacional) == vendas_ii)
    grupo.members.remove(vendas_ii)
    t('loja sem gerente de vendas não tem base', sv.get_gerente_de_vendas_do_setor(orfao) is None)

    print('\n== OS 75% ==')
    chamados = []
    with mock.patch.object(sv, '_compute_gerente_simulation_base', side_effect=base_falsa_factory(chamados)):
        sim = sv.compute_gerente_simulation(operacional, {'meta': {}}, {}, view_mode='realizado', simulator_inputs={})
    linha = sim['rows'][0]
    t('o cálculo sai do gerente de vendas da loja', chamados == [vendas], chamados)
    t('todo valor em reais leva 75%', linha['commission_value'] == 75.0 and linha['premium_value'] == 30.0
      and linha['total_individual'] == 105.0 and linha['pdv_premium_value'] == 15.0 and linha['total_with_pdv'] == 120.0
      and linha['hunter2_value'] == 6.0, linha)
    t('e os totais também (ganho 184 → 138)', sim['totals']['ganho_total'] == 138.0 and sim['totals']['bonus_6_7'] == 12.0,
      sim['totals'])
    t('metas, realizado e atingimento continuam os da loja', linha['meta'] == 1000.0 and linha['proj'] == 800.0
      and linha['attainment'] == 0.8 and linha['coord_meta'] == 5000.0)
    t('a tela sabe quem é a base e qual o percentual', sim['is_gerente_operacional'] and sim['percentual_operacional_pct'] == 75
      and 'GERENTE DE VENDAS' in sim['gerente_de_vendas_base'] and sim['ganho_total_base'] == 184.0, sim.get('gerente_de_vendas_base'))

    chamados.clear()
    with mock.patch.object(sv, '_compute_gerente_simulation_base', side_effect=base_falsa_factory(chamados)):
        sim = sv.compute_gerente_simulation(operacional, {'meta': {'operacional_rate': 0.5}}, {}, view_mode='realizado')
    t('o percentual vem da tela de fatores quando configurado', sim['totals']['ganho_total'] == 92.0, sim['totals'])

    chamados.clear()
    with mock.patch.object(sv, '_compute_gerente_simulation_base', side_effect=base_falsa_factory(chamados)):
        sim = sv.compute_gerente_simulation(vendas, {'meta': {}}, {}, view_mode='realizado')
    t('o gerente de vendas continua com 100%', chamados == [vendas] and sim['totals']['ganho_total'] == 184.0
      and not sim.get('is_gerente_operacional'))

    chamados.clear()
    with mock.patch.object(sv, '_compute_gerente_simulation_base', side_effect=base_falsa_factory(chamados)):
        sim = sv.compute_gerente_simulation(orfao, {'meta': {}}, {}, view_mode='realizado')
    t('sem gerente de vendas no setor: erro claro, sem número inventado',
      not chamados and 'ZZ Loja Sem Gerente de Vendas' in sim.get('error', ''), sim.get('error'))

    print('\n== META COORD. DA FIXA ==')
    metas = {'LOJA A': {'fixa': 72.0, 'movel': 90000.0}, 'LOJA B': {'movel': 50000.0}, 'LOJA C': {'fixa': 54.0}}
    with mock.patch.object(sv, 'get_pdvs_of_coord', return_value=list(metas)), \
            mock.patch.object(sv, 'get_metas_from_power_bi', side_effect=lambda store_name: metas.get(store_name)):
        total = sv.meta_fixa_da_coordenacao(None, None, 'ZZ COORD')
    t('soma as metas oficiais de Fixa das lojas (quantidade); loja sem meta não soma', total == 126.0, total)

    print('\n== COM OS DADOS REAIS (SÓ LEITURA) ==')
    fatores = sv.get_factor_set(sv.ROLE_GERENTE).data
    realizado = getattr(sv, 'VIEW_REALIZADO', 'realizado')
    ezequiel = User.objects.filter(pk=517, is_active=True).first()
    if ezequiel and sv.is_gerente_operacional(ezequiel):
        base = sv.get_gerente_de_vendas_do_setor(ezequiel)
        s_op = sv.compute_gerente_simulation(ezequiel, fatores, {}, view_mode=realizado, simulator_inputs={})
        s_base = sv.compute_gerente_simulation(base, fatores, {}, view_mode=realizado, simulator_inputs={})
        esperado = 0.75 * s_base['totals']['ganho_total']
        t(f'Ezequiel (Montserrat) recebe 75% de {base.get_full_name()}',
          abs(s_op['totals']['ganho_total'] - esperado) < 0.01, (s_op['totals']['ganho_total'], esperado))
    else:
        print('  (usuário 517 não é mais gerente operacional ativo: conferência real pulada)')
    bruna = User.objects.filter(pk=61, is_active=True).first()
    if bruna:
        s = sv.compute_gerente_simulation(bruna, fatores, {}, view_mode=realizado, simulator_inputs={})
        fixa = next(r for r in s['rows'] if r['key'] == 'fixa')
        planilha = sv.load_dataframe(sv.ROLE_GERENTE, 'REALIZADO')
        projecao = sv.load_dataframe(sv.ROLE_GERENTE, 'PROJEÇÃO')
        oficial = sv.meta_fixa_da_coordenacao(planilha, projecao, s['coordinator'])
        t('Bruna: a Meta Coord. da Fixa é a soma oficial em quantidade (não os 36.850 em reais)',
          fixa['coord_meta'] == oficial and 0 < oficial < 5000, (fixa['coord_meta'], oficial))
        t('e o atingimento de coordenação volta a fazer sentido', fixa['coord_attainment'] > 0.05,
          f"{fixa['coord_proj']} / {fixa['coord_meta']} = {fixa['coord_attainment']:.3f}")
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
