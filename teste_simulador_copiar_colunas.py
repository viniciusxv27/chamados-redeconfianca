"""Simulador: botões "Copiar Projeção" / "Copiar Realizado" no cabeçalho de Meta,
Real Individual e Real Coordenação/PDV (12/11/2026).

Power BI e MySQL são dublês; roda no Postgres de dev dentro de uma transação
desfeita no fim (padrão dos testes do simulador).
"""
import contextlib
import json
import os
import sys
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sim-cp'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sim-cp2'}}
django.setup()
if 'testserver' not in settings.ALLOWED_HOSTS and '*' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

from django.db import transaction
from django.test import Client

from simulator import services as sv
from simulator import views as sim_views
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
VENDAS_LOJA = {'ZZ PAULISTA A': 4000.0, 'ZZ PAULISTA B': 6000.0}
# Realizado individual de qualquer vendedor (consultor) nos dublês.
VENDAS_IND = {'movel': 1500.0, 'fixa': 900.0, 'fixa_qty': 3.0, 'smartphones': 2500.5, 'eletronicos': 0.0,
              'essenciais': 0.0, 'seguros': 120.0, 'sva': 0.0}


# Consultor com meta de Fixa fracionada (65 das 74 metas de Fixa do METAS_CN_REAL
# têm casas decimais): 6 vendas ÷ 8,63 = 69,52%, logo acima dos 69,5% que liberam
# as premiações. Copiar a meta como 9 derrubava para 66,67% (achado da revisão).
META_DECIMAL = {'movel': 1000.0, 'fixa': 8.63, 'smartphones': 2000.0, 'eletronicos': 1000.0,
                'essenciais': 1000.0, 'seguros': 100.0, 'sva': 100.0}
VENDAS_DECIMAL = {'movel': 1500.0, 'fixa': 600.0, 'fixa_qty': 6.0, 'smartphones': 3000.0, 'eletronicos': 1500.0,
                  'essenciais': 1500.0, 'seguros': 150.0, 'sva': 150.0}


def metas_falsas(store_name=None, user_name=None, **kw):
    if 'decimal' in str(user_name or '').lower():
        return dict(META_DECIMAL)
    return dict(META_LOJA) if str(store_name).startswith('ZZ PAULISTA') else {}


def vendas_falsas(vendor=None, pdv=None, pdvs=None, coord_name=None, **kw):
    if vendor and 'decimal' in str(vendor).lower():
        return dict(VENDAS_DECIMAL)
    if vendor:
        return dict(VENDAS_IND)
    lojas = pdvs or ([pdv] if pdv else [])
    movel = sum(VENDAS_LOJA.get(sv.normalize_text(loja), 0.0) for loja in lojas)
    return {'movel': movel, 'fixa': 0.0, 'fixa_qty': 10.0 * len(lojas), 'smartphones': 0.0, 'eletronicos': 0.0,
            'essenciais': 0.0, 'seguros': 0.0, 'sva': 0.0}


PAPEIS = {}


def papel_falso(user):
    if user.is_superuser:
        return sv.ROLE_SUPERADMIN
    return PAPEIS.get(user.id, sv.ROLE_CONSULTOR)


def json_de(resp):
    try:
        return json.loads(resp.content.decode('utf-8'))
    except ValueError:
        return {}


marcador = transaction.atomic()
marcador.__enter__()
try:
    a = Sector.objects.create(name='Loja ZZ Paulista A')
    b = Sector.objects.create(name='Loja ZZ Paulista B')
    outra = Sector.objects.create(name='Loja ZZ Outra Praça')
    coord = User.objects.create_user(username='zz.sim.coordcp', email='zz.sim.coordcp@exemplo-teste.local',
                                     password='S3nha!teste', first_name='ZZCOORDCP', last_name='SP', hierarchy='ADMIN')
    acesso = CoordinatorStoreAccess.objects.create(coordinator=coord)
    acesso.sectors.add(a, b)
    gerente = User.objects.create_user(username='zz.sim.gercp', email='zz.sim.gercp@exemplo-teste.local',
                                       password='S3nha!teste', first_name='ZZ', last_name='Gerente CP', sector=a,
                                       job_title='GERENTE DE VENDAS')
    consultor = User.objects.create_user(username='zz.sim.conscp', email='zz.sim.conscp@exemplo-teste.local',
                                         password='S3nha!teste', first_name='ZZ', last_name='Consultor CP',
                                         sector=a, hierarchy='PADRAO')
    estranho = User.objects.create_user(username='zz.sim.estcp', email='zz.sim.estcp@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name='Outra Loja',
                                        sector=outra, hierarchy='PADRAO')
    cons_dec = User.objects.create_user(username='zz.sim.consdec', email='zz.sim.consdec@exemplo-teste.local',
                                        password='S3nha!teste', first_name='ZZ', last_name='Decimal CP',
                                        sector=a, hierarchy='PADRAO')
    admin = User.objects.create_user(username='zz.sim.admcp', email='zz.sim.admcp@exemplo-teste.local',
                                     password='S3nha!teste', first_name='ZZ', last_name='Admin CP',
                                     is_superuser=True, is_staff=True)
    PAPEIS.update({gerente.id: sv.ROLE_GERENTE, consultor.id: sv.ROLE_CONSULTOR,
                   coord.id: sv.ROLE_COORDENADOR, estranho.id: sv.ROLE_CONSULTOR})

    patches = [
        mock.patch.object(sv, 'get_metas_from_power_bi', side_effect=metas_falsas),
        mock.patch.object(sv, 'get_realized_sales_from_mysql', side_effect=vendas_falsas),
        mock.patch.object(sv, 'get_business_days_info', return_value=(10, 20)),
        mock.patch.object(sim_views, 'realized_prefetch', side_effect=lambda *a, **k: contextlib.nullcontext()),
        mock.patch.object(sim_views, 'get_user_role', side_effect=papel_falso),
        mock.patch.object(sim_views, 'is_sniper_user', return_value=False),
    ]
    with contextlib.ExitStack() as pilha:
        for p in patches:
            pilha.enter_context(p)

        fs_g = sv.get_factor_set(sv.ROLE_GERENTE).data
        g_proj = sv.compute_gerente_simulation(gerente, fs_g, {}, view_mode=sv.VIEW_PROJECAO)
        g_real = sv.compute_gerente_simulation(gerente, fs_g, {}, view_mode=sv.VIEW_REALIZADO)
        vp = sv.build_simulador_valores(g_proj, sv.ROLE_GERENTE)
        vr = sv.build_simulador_valores(g_real, sv.ROLE_GERENTE)
        prefill = sv.build_simulador_prefill(g_real, sv.ROLE_GERENTE)

        print('== GERENTE: VALORES POR VISÃO ==')
        t('três colunas', set(vp) == {'meta', 'real', 'realpdv'})
        t('Real Coordenação na Projeção = coordenação projetada', vp['realpdv'].get('movel__realpdv') == '20000',
          vp['realpdv'].get('movel__realpdv'))
        t('Real Coordenação no Realizado = realizado da coordenação', vr['realpdv'].get('movel__realpdv') == '10000',
          vr['realpdv'].get('movel__realpdv'))
        t('Fixa da coordenação em quantidade (40 projetada / 20 realizada)',
          vp['realpdv'].get('fixa__qtypdv') == '40' and vr['realpdv'].get('fixa__qtypdv') == '20',
          (vp['realpdv'].get('fixa__qtypdv'), vr['realpdv'].get('fixa__qtypdv')))
        t('Real Individual: Projeção 8000 / Realizado 4000',
          vp['real'].get('movel__real') == '8000' and vr['real'].get('movel__real') == '4000',
          (vp['real'].get('movel__real'), vr['real'].get('movel__real')))
        t('Fixa individual: quantidade inteira e receita', vr['real'].get('fixa__qty') == '10'
          and 'fixa__receita' in vr['real'] and vp['real'].get('fixa__qty') == '20',
          (vr['real'].get('fixa__qty'), vp['real'].get('fixa__qty')))
        t('Meta: a mesma nas duas visões', vp['meta'] == vr['meta'] and vp['meta'].get('movel__meta') == '10000'
          and vp['meta'].get('fixa__meta') == '70' and vp['meta'].get('eletronicos_a__meta') == '15000',
          vp['meta'])
        chaves_esperadas = {'movel__real', 'fixa__qty', 'fixa__receita', 'smartphones__real', 'eletronicos_a__real',
                            'essenciais_a__real', 'seguros__real', 'sva__real'}
        t('coluna inteira: todas as chaves, zero vira vazio', set(vr['real']) == chaves_esperadas
          and vr['real']['sva__real'] == '', sorted(vr['real']))

        print('\n== PRÉ-PREENCHIMENTO CONTINUA IGUAL ==')
        t('Real Coordenação vem com a projeção', prefill.get('movel__realpdv') == '20000', prefill.get('movel__realpdv'))
        t('Fixa da coordenação projetada', prefill.get('fixa__qtypdv') == '40', prefill.get('fixa__qtypdv'))
        t('Real Individual com o realizado', prefill.get('movel__real') == '4000', prefill.get('movel__real'))
        t('meta fica de fora e vazio não entra', not any(k.endswith('__meta') for k in prefill)
          and 'sva__realpdv' not in prefill and 'sva__real' not in prefill, sorted(prefill))
        t('consultor sem Fixa do PDV no pré-preenchimento',
          'fixa__qtypdv' not in sv.build_simulador_prefill(g_real, sv.ROLE_CONSULTOR))

        print('\n== CONSULTOR (REAL PDV) ==')
        fs_c = sv.get_factor_set(sv.ROLE_CONSULTOR).data
        c_proj = sv.compute_consultor_simulation(consultor, fs_c, {}, view_mode=sv.VIEW_PROJECAO)
        c_real = sv.compute_consultor_simulation(consultor, fs_c, {}, view_mode=sv.VIEW_REALIZADO)
        cp = sv.build_simulador_valores(c_proj, sv.ROLE_CONSULTOR)
        cr = sv.build_simulador_valores(c_real, sv.ROLE_CONSULTOR)
        t('Real PDV = loja (realizado 4000 / projeção 8000)',
          cr['realpdv'].get('movel__realpdv') == '4000' and cp['realpdv'].get('movel__realpdv') == '8000',
          (cr['realpdv'].get('movel__realpdv'), cp['realpdv'].get('movel__realpdv')))
        t('Fixa do PDV em quantidade', cr['realpdv'].get('fixa__qtypdv') == '10'
          and cp['realpdv'].get('fixa__qtypdv') == '20', (cr['realpdv'], cp['realpdv'].get('fixa__qtypdv')))
        t('Real Individual do consultor (centavos preservados)', cr['real'].get('smartphones__real') == '2500.50'
          and cp['real'].get('smartphones__real') == '5001', (cr['real'].get('smartphones__real'),
                                                             cp['real'].get('smartphones__real')))
        t('Fixa projetada inteira continua inteira', cp['real'].get('fixa__qty') == '6',
          cp['real'].get('fixa__qty'))

        print('\n== FIXA COM CASAS DECIMAIS (achado da revisão) ==')
        t('quantidade não é arredondada', sv._prefill_qtd(8.63) == '8.63' and sv._prefill_qtd(13.333333333) == '13.333333'
          and sv._prefill_qtd(40.0) == '40' and sv._prefill_qtd(0) == '' and sv._prefill_qtd('x') == '',
          (sv._prefill_qtd(8.63), sv._prefill_qtd(13.333333333)))
        d_real = sv.compute_consultor_simulation(cons_dec, fs_c, {}, view_mode=sv.VIEW_REALIZADO)
        d_val = sv.build_simulador_valores(d_real, sv.ROLE_CONSULTOR)
        t('meta da Fixa copiada com as casas', d_val['meta'].get('fixa__meta') == '8.63', d_val['meta'])
        tela = sv.build_simulador_prefill(d_real, sv.ROLE_CONSULTOR)
        vazio = sv.compute_consultor_simulation(cons_dec, fs_c, {}, simulator_inputs=dict(tela), view_mode=sv.VIEW_SIMULADOR)
        copiada = sv.compute_consultor_simulation(cons_dec, fs_c, {}, simulator_inputs=dict(tela, **d_val['meta']),
                                                  view_mode=sv.VIEW_SIMULADOR)
        antiga = sv.compute_consultor_simulation(cons_dec, fs_c, {}, simulator_inputs=dict(tela, **dict(d_val['meta'], fixa__meta='9')),
                                                 view_mode=sv.VIEW_SIMULADOR)
        g = lambda sim: round(float((sim.get('totals') or {}).get('ganho_total') or 0), 2)
        t('copiar a meta = deixar o campo vazio (mesmo ganho)', g(vazio) > 0 and g(copiada) == g(vazio),
          (g(vazio), g(copiada)))
        t('o cenário é sensível: meta 9 (arredondada) dá outro ganho', g(antiga) != g(vazio), (g(antiga), g(vazio)))
        with mock.patch.object(sv, 'get_business_days_info', return_value=(7, 20)):
            d_proj = sv.compute_consultor_simulation(cons_dec, fs_c, {}, view_mode=sv.VIEW_PROJECAO)
            dp = sv.build_simulador_valores(d_proj, sv.ROLE_CONSULTOR)
            entradas = {}
            for col in sv.SIMULADOR_COLUNAS:
                entradas.update(dp[col])
            refeita = sv.compute_consultor_simulation(cons_dec, fs_c, {}, simulator_inputs=entradas, view_mode=sv.VIEW_SIMULADOR)
        t('Fixa projetada fracionada copiada sem arredondar (6 ÷ 7 × 20)', dp['real'].get('fixa__qty') == '17.142857',
          dp['real'].get('fixa__qty'))
        t('Copiar Projeção nas 3 colunas + Simular = aba Projeção', g(d_proj) > 0 and g(refeita) == g(d_proj),
          (g(d_proj), g(refeita)))

        print('\n== COORDENADOR (SEM 3ª COLUNA) ==')
        fs_k = sv.get_factor_set(sv.ROLE_COORDENADOR).data
        k_real = sv.compute_coordenador_simulation(coord, fs_k, {}, view_mode=sv.VIEW_REALIZADO)
        kr = sv.build_simulador_valores(k_real, sv.ROLE_COORDENADOR)
        t('coordenador calcula sem erro', not k_real.get('error'), k_real.get('error'))
        t('coordenador: coluna Real Coordenação/PDV vazia', kr['realpdv'] == {}, kr['realpdv'])
        t('coordenador: meta e real com todas as chaves', len(kr['meta']) == 7 and len(kr['real']) == 8,
          (len(kr['meta']), len(kr['real'])))

        print('\n== ENDPOINT ?fragment=valores ==')
        cli = Client()
        cli.force_login(admin)
        base = '/simulator/?view=simulador&fragment=valores'
        r = cli.get(f'{base}&user_id={gerente.id}&visao=projecao')
        d = json_de(r)
        t('gerente/projeção: 200 + ok', r.status_code == 200 and d.get('ok') is True, (r.status_code, d))
        t('gerente/projeção: Real Coordenação = 20000',
          (d.get('valores') or {}).get('realpdv', {}).get('movel__realpdv') == '20000')
        t('devolve metas para a meta-diária', (d.get('metas') or {}).get('movel') == 10000.0, d.get('metas'))
        r = cli.get(f'{base}&user_id={gerente.id}&visao=realizado')
        d = json_de(r)
        t('gerente/realizado: Real Coordenação = 10000',
          r.status_code == 200 and (d.get('valores') or {}).get('realpdv', {}).get('movel__realpdv') == '10000', d)
        r = cli.get(f'{base}&user_id={consultor.id}&visao=realizado', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        d = json_de(r)
        t('consultor (com cabeçalho XHR): JSON, não o fragmento HTML',
          r['Content-Type'].startswith('application/json') and d.get('ok') is True, r['Content-Type'])
        t('consultor: Real PDV = 4000', (d.get('valores') or {}).get('realpdv', {}).get('movel__realpdv') == '4000')
        r = cli.get(f'{base}&user_id={coord.id}&visao=projecao')
        d = json_de(r)
        t('coordenador: ok e sem 3ª coluna', d.get('ok') is True and d['valores']['realpdv'] == {}, d)
        r = cli.get(f'{base}&user_id={gerente.id}&visao=qualquer')
        t('visão inválida: 400 com mensagem', r.status_code == 400 and json_de(r).get('erro'), r.status_code)
        r = cli.get(f'{base}&visao=projecao')
        t('superadmin sem pessoa: 400', r.status_code == 400 and 'Selecione' in json_de(r).get('erro', ''))
        with mock.patch.object(sim_views, 'compute_gerente_simulation', side_effect=RuntimeError('mysql caiu')):
            r = cli.get(f'{base}&user_id={gerente.id}&visao=projecao')
        d = json_de(r)
        t('falha no cálculo: 503 com mensagem amigável', r.status_code == 503 and d.get('ok') is False
          and 'Tente de novo' in d.get('erro', ''), (r.status_code, d))

        cli_g = Client()
        cli_g.force_login(gerente)
        r = cli_g.get(f'{base}&visao=projecao')
        t('gerente vê o próprio sem user_id', r.status_code == 200 and json_de(r).get('ok') is True, r.status_code)
        r = cli_g.get(f'{base}&user_id={estranho.id}&visao=projecao')
        t('gerente não copia pessoa de outra loja (redireciona)', r.status_code == 302, r.status_code)

        print('\n== TELA ==')
        r = cli.get(f'/simulator/?view=simulador&user_id={gerente.id}')
        html = r.content.decode('utf-8')
        t('gerente: botões nas 3 colunas', r.status_code == 200 and html.count('data-copiar-grupo=') == 3
          and html.count('Copiar Projeção</span>') == 3 and html.count('Copiar Realizado</span>') == 3,
          (r.status_code, html.count('data-copiar-grupo=')))
        t('botões não enviam o formulário', html.count('type="button" data-copiar-coluna=') == 6)
        t('URL de cópia mantém a pessoa', f'?user_id={gerente.id}&amp;view=simulador&amp;fragment=valores' in html)
        t('área de status/erro presente', 'id="sim-copiar-status"' in html)
        t('comentário de template não vaza', 'Recebe: coluna' not in html and '{#' not in html)
        r = cli.get(f'/simulator/?view=simulador&user_id={coord.id}')
        html = r.content.decode('utf-8')
        t('coordenador: botões só em Meta e Real Individual', html.count('data-copiar-grupo=') == 2
          and 'data-copiar-grupo="realpdv"' not in html, html.count('data-copiar-grupo='))
        r = cli.get(f'/simulator/?view=simulador&user_id={consultor.id}')
        html = r.content.decode('utf-8')
        t('consultor: botões também no Real PDV', 'data-copiar-grupo="realpdv"' in html and 'Real PDV' in html)
        t('campos de quantidade da Fixa aceitam casas decimais', 'step="1"' not in html
          and all(('step="any" min="0" name="sim__fixa__' + c + '"') in html for c in ('meta', 'qty', 'qtypdv')))
        t('meta-diária lê o campo numérico sem tratar o ponto como milhar', "if (el.type === 'number')" in html)
        r = cli.get(f'/simulator/?view=projecao&user_id={gerente.id}')
        t('fora da aba Simulador não aparece', 'data-copiar-grupo=' not in r.content.decode('utf-8'))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
