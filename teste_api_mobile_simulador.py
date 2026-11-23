"""Módulo Simulador no app (/api/mobile/v1/m/simulador/): acesso, recorte e números.

- Usuários de teste (@exemplo-teste.local) para o recorte, pelo login do app; o
  motor é dublado nessas chamadas (a regra de quem vê quem não depende dele).
- Números: a API e o fragmento de resultados da web (simulator_dashboard
  ?fragment=results) para as MESMAS pessoas reais, sem sessão nem token para
  elas (APIRequestFactory + force_authenticate; a web por RequestFactory com o
  render trocado por quem guarda o contexto). Motor de verdade (MySQL + planilha).
- Cache em memória (LocMem): nada vai para o Redis compartilhado.
- Tudo dentro de uma transação desfeita no fim.
"""
import os
import sys
import time
import warnings
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')

import redeconfianca.settings as _cfg  # noqa: E402

# Nunca o Redis compartilhado: o clear() do django-redis é FLUSHDB no banco inteiro.
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-simulador-{n}'}
               for n in _cfg.CACHES}

import django  # noqa: E402

django.setup()
warnings.filterwarnings('ignore', message='Cache key contains characters')

from django.conf import settings  # noqa: E402
from django.core.cache import caches  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.messages.storage.cookie import CookieStorage  # noqa: E402
from django.db import transaction  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.test import Client, RequestFactory  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from api_mobile.modulos import simulador as mod  # noqa: E402
from api_mobile.permissoes import bloqueio_do_usuario  # noqa: E402
from communications.models import CommunicationGroup  # noqa: E402
from simulator import services as sv  # noqa: E402
from simulator import views as sim_views  # noqa: E402
from simulator.models import CoordinatorStoreAccess  # noqa: E402
from users.models import Sector  # noqa: E402

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/simulador'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def silencioso(fn, *a, **kw):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


SIM_FALSA = {
    'user_name': 'ZZ', 'first_name': 'ZZ', 'pdv': 'ZZ LOJA', 'coordinator': 'ZZ COORD', 'view_mode': 'projecao',
    'rows': [
        {'key': 'movel', 'label': 'Móvel', 'meta': 10000.0, 'proj': 6000.0, 'attainment': 0.6,
         'commission_rate': 0.02, 'commission_value': 120.0, 'premium_value': 0.0, 'total_individual': 120.0,
         'pdv_meta': 50000.0, 'pdv_proj': 40000.0, 'pdv_attainment': 0.8, 'pdv_premium_value': 0.0,
         'total_with_pdv': 120.0, 'hunter2_value': 0.0, 'hunter3_value': 0.0, 'quantity': None, 'revenue': None},
        {'key': 'fixa', 'label': 'Fixa', 'meta': 10.0, 'proj': 999.0, 'attainment': 1.2, 'commission_rate': 0.0,
         'commission_value': 0.0, 'total_with_pdv': 50.0, 'quantity': 12.0, 'revenue': 800.0,
         'pdv_meta': None, 'pdv_proj': None, 'pdv_attainment': None},
        {'key': 'eletronicos', 'label': 'Eletrônicos', 'meta': 100.0, 'proj': 50.0, 'attainment': 0.5,
         'commission_value': 30.0, 'total_with_pdv': 0.0, 'total_individual': 0.0},
    ],
    'totals': {'total_with_pdv': 170.0, 'hunter2': 0.0, 'hunter3': 0.0, 'bonus_6_7': 0.0,
               'ganho_total_sem_acelerador': 170.0, 'acelerador_microindicadores': 0.0, 'ganho_total': 170.0},
}


for nome in settings.CACHES:
    assert 'locmem' in settings.CACHES[nome]['BACKEND'].lower(), 'cache do teste precisa ser LocMem'
    caches[nome].clear()          # só LocMem (conferido acima)

marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='ZZ Loja Simulador App')
    outra = Sector.objects.create(name='ZZ Outra Loja Simulador App')

    def novo(apelido, hierarquia='PADRAO', setor=loja):
        return User.objects.create_user(
            username=f'zzsim.{apelido}', email=f'zzsim.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZSim{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia)

    cons = novo('cons')
    colega = novo('colega')
    fora = novo('fora', setor=outra)
    gerente = novo('gerente')
    coord = novo('coord', setor=outra)
    aparte = novo('aparte')
    chefe = novo('chefe', 'SUPERADMIN', outra)
    CommunicationGroup.objects.filter(name__icontains='GERENTES').first().members.add(gerente)
    CommunicationGroup.objects.filter(name__icontains='COORDENADORES').first().members.add(coord)
    grupo_aparte = sv.get_aparte_group()
    if grupo_aparte:
        grupo_aparte.members.add(aparte)
    carteira = CoordinatorStoreAccess.objects.create(coordinator=coord)
    carteira.sectors.add(loja)

    def entrar(u):
        r = Client().post(f'{BASE}/auth/login/', {'email': u.email, 'senha': 'S3nha!teste', 'plataforma': 'ios',
                                                   'dispositivo': 'ZZ'}, content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")

    c_cons, c_ger, c_coord, c_aparte, c_chefe = (entrar(u) for u in (cons, gerente, coord, aparte, chefe))

    print('== ACESSO ==')
    t('todo mundo logado vê o simulador (como o menu do portal)',
      all('simulador' in {m['chave'] for m in c.get(f'{BASE}/modulos/').json()} for c in (c_cons, c_ger, c_chefe)))
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))
    t('papéis resolvidos pelo motor', (sv.get_user_role(cons), sv.get_user_role(gerente), sv.get_user_role(coord))
      == (sv.ROLE_CONSULTOR, sv.ROLE_GERENTE, sv.ROLE_COORDENADOR))

    chamadas = []

    def calcular_falso(alvo, papel_alvo, visao, hunter, acelerador, entradas=None):
        chamadas.append((alvo.id, papel_alvo, visao, hunter, acelerador, entradas or {}))
        return dict(SIM_FALSA, rows=[dict(r) for r in SIM_FALSA['rows']])

    with mock.patch.object(mod, 'calcular', side_effect=calcular_falso):
        print('\n== RECORTE ==')
        r = c_cons.get(f'{M}/')
        j = r.json()
        t('consultor abre o próprio simulador', r.status_code == 200 and j['alvo']['id'] == cons.id
          and j['vendo_outro'] is False and j['simulacao'] is not None, r.content[:300])
        t('consultor vê o colega da loja (a web deixa: views.py:207-219)',
          c_cons.get(f'{M}/', {'usuario': colega.id}).status_code == 200)
        t('e é calculado como consultor', chamadas[-1][1] == sv.ROLE_CONSULTOR)
        t('consultor de outra loja: 404', c_cons.get(f'{M}/', {'usuario': fora.id}).status_code == 404)
        t('id que não existe: 404', c_cons.get(f'{M}/', {'usuario': 999999999}).status_code == 404)
        ids = {p['id'] for p in c_cons.get(f'{M}/pessoas/').json()['resultados']}
        t('seletor do consultor: os consultores da loja', {cons.id, colega.id} <= ids and fora.id not in ids, ids)

        r = c_ger.get(f'{M}/')
        t('gerente abre o próprio como gerente', r.json()['alvo']['id'] == gerente.id and chamadas[-1][1] == sv.ROLE_GERENTE)
        t('gerente abre o consultor da loja', c_ger.get(f'{M}/', {'usuario': cons.id}).status_code == 200)
        t('gerente não abre outra loja: 404', c_ger.get(f'{M}/', {'usuario': fora.id}).status_code == 404)

        r = c_coord.get(f'{M}/')
        t('coordenador abre o próprio', r.json()['alvo']['id'] == coord.id and chamadas[-1][1] == sv.ROLE_COORDENADOR)
        t('coordenador abre loja da carteira', c_coord.get(f'{M}/', {'usuario': cons.id}).status_code == 200)
        t('coordenador não abre loja fora da carteira: 404', c_coord.get(f'{M}/', {'usuario': fora.id}).status_code == 404)
        ids = [p['id'] for p in c_coord.get(f'{M}/pessoas/').json()['resultados']]
        t('seletor do coordenador: ele primeiro e a carteira', ids and ids[0] == coord.id
          and {cons.id, gerente.id} <= set(ids) and fora.id not in ids)

        if grupo_aparte:
            r = c_aparte.get(f'{M}/')
            t('"A parte" abre o próprio', r.status_code == 200 and chamadas[-1][1] == sv.ROLE_APART)
            t('"A parte" não abre outra pessoa: 404', c_aparte.get(f'{M}/', {'usuario': colega.id}).status_code == 404)

        r = c_chefe.get(f'{M}/')
        t('superadmin sem escolher: pede para escolher, sem calcular',
          r.json()['precisa_escolher'] is True and r.json()['simulacao'] is None)
        t('superadmin abre qualquer um', c_chefe.get(f'{M}/', {'usuario': fora.id}).status_code == 200)

        print('\n== PARÂMETROS E FORMATO ==')
        r = c_cons.get(f'{M}/', {'visao': 'realizado', 'hunter_movel': '2', 'acelerador': '1'})
        t('visão, Hunter e acelerador chegam ao motor', chamadas[-1][2] == sv.VIEW_REALIZADO
          and chamadas[-1][3]['movel'] == 2 and chamadas[-1][4] is True, chamadas[-1])
        t('visão inválida vira projeção', c_cons.get(f'{M}/', {'visao': 'xyz'}).json()['visao'] == sv.VIEW_PROJECAO)
        t('as três visões da web (Projeção, Realizado, Simulador)',
          [v['valor'] for v in c_cons.get(f'{M}/').json()['visoes']] == [sv.VIEW_PROJECAO, sv.VIEW_REALIZADO, sv.VIEW_SIMULADOR])
        j = c_cons.get(f'{M}/', {'hunter_fixa': '3'}).json()
        t('níveis Hunter: os pilares da web e o nível escolhido de volta', [p['chave'] for p in j['pilares_hunter']]
          == [k for k, _ in sv.HUNTER_PILLARS] and j['hunter']['fixa'] == 3 and j['hunter']['movel'] == 0, j.get('hunter'))

        print('\n== VISÃO SIMULADOR ==')
        j = c_cons.get(f'{M}/', {'visao': 'simulador'}).json()
        f = j['simulador'] or {}
        t('1ª entrada: calcula o REALIZADO, sem valores (como o fragmento da web)', chamadas[-1][2] == sv.VIEW_REALIZADO
          and chamadas[-1][5] == {} and f.get('primeira') is True, chamadas[-1])
        t('campos pré-preenchidos com o realizado (build_simulador_prefill)', f.get('valores', {}).get('movel__real') == '6000'
          and f['valores'].get('movel__realpdv') == '40000' and f['valores'].get('fixa__qty') == '12', f.get('valores'))
        t('consultor: Meta, Real Individual e Real PDV', [c['rotulo'] for c in f['colunas']] == ['Meta (opcional)', 'Real Individual', 'Real PDV'])
        fixa = next(p for p in f['pilares'] if p['chave'] == 'fixa')
        t('Fixa: quantidade + receita no real, quantidade no PDV', [c['chave'] for c in fixa['campos']['real']] == ['fixa__qty', 'fixa__receita']
          and [c['chave'] for c in fixa['campos']['realpdv']] == ['fixa__qtypdv'] and fixa['campo_diario'] == 'fixa__qty')
        t('metas para a meta-diária', f['metas'].get('movel') == 10000.0, f.get('metas'))
        j = c_cons.get(f'{M}/', {'visao': 'simulador', 'sim__movel__real': '7000', 'sim__fixa__qty': '3', 'outro': 'x'}).json()
        t('com valores: calcula a visão Simulador com eles', chamadas[-1][2] == sv.VIEW_SIMULADOR
          and chamadas[-1][5] == {'movel__real': '7000', 'fixa__qty': '3'} and j['simulador']['primeira'] is False
          and j['simulador']['valores'] == {'movel__real': '7000', 'fixa__qty': '3'}, chamadas[-1])
        f = c_ger.get(f'{M}/', {'visao': 'simulador'}).json()['simulador']
        t('gerente: a 3ª coluna é Real Coordenação', f['colunas'][2]['rotulo'] == 'Real Coordenação')
        f = c_coord.get(f'{M}/', {'visao': 'simulador'}).json()['simulador']
        t('coordenador: sem a 3ª coluna', [c['chave'] for c in f['colunas']] == ['meta', 'real']
          and all('realpdv' not in p['campos'] for p in f['pilares']))
        t('fora do Simulador, os valores sim__ não chegam ao motor',
          c_cons.get(f'{M}/', {'sim__movel__real': '1'}).status_code == 200 and chamadas[-1][5] == {})
        s = c_cons.get(f'{M}/').json()
        pil = {p['chave']: p for p in s['simulacao']['pilares']}
        t('fixa mostra a quantidade como realizado', pil['fixa']['realizado'] == 12 and pil['fixa']['quantidade'] is True)
        t('atingimento é fração', pil['movel']['atingimento'] == 0.6)
        t('linha unida A+B leva a comissão cheia (maior dos campos)', pil['eletronicos']['ganho'] == 30)
        restantes = s['dias_uteis']['restantes']
        esperado = round(4000 / restantes, 2) if restantes else 0
        t('meta-diária = o que falta ÷ dias úteis restantes', pil['movel']['por_dia'] == esperado, (pil['movel'], restantes))
        t('pilar batido não pede meta-diária', pil['fixa']['por_dia'] == 0)
        t('loja só para o consultor', pil['movel']['loja'] and pil['movel']['coordenacao'] is None)

    def falha(*a, **kw):
        raise RuntimeError('MySQL fora')

    with mock.patch.object(mod, 'calcular', side_effect=falha):
        r = c_cons.get(f'{M}/')
        t('motor fora do ar: 200 com erro amigável', r.status_code == 200 and r.json()['erro']
          and r.json()['simulacao'] is None)

    print('\n== COPIAR PROJEÇÃO / REALIZADO ==')
    visoes_pedidas = []

    def motor_falso(alvo, fatores, hunter, view_mode=None, **kw):
        visoes_pedidas.append(view_mode)
        return dict(SIM_FALSA, rows=[dict(r) for r in SIM_FALSA['rows']])

    with mock.patch('simulator.views.compute_consultor_simulation', side_effect=motor_falso):
        r = c_cons.get(f'{M}/valores/', {'visao': 'realizado'})
        j = r.json()
        t('valores da coluna pela view da web (_simulador_valores_json)', r.status_code == 200 and visoes_pedidas[-1] == sv.VIEW_REALIZADO
          and j['valores']['real'].get('movel__real') == '6000' and j['valores']['realpdv'].get('movel__realpdv') == '40000'
          and j['metas'].get('movel') == 10000.0, r.content[:300])
        r = c_cons.get(f'{M}/valores/', {'visao': 'xyz'})
        t('visão inválida: 400 com a mensagem da web', r.status_code == 400 and 'Visão inválida' in r.json().get('erro', ''))
        t('mesmo recorte do resumo: outra loja 404', c_cons.get(f'{M}/valores/', {'visao': 'projecao', 'usuario': fora.id}).status_code == 404)
        r = c_chefe.get(f'{M}/valores/', {'visao': 'projecao'})
        t('superadmin sem escolher pessoa: 400', r.status_code == 400)

    print('\n== TABELA DA REDE ==')
    from users.commission_views import pode_ver_comissionamento

    def linha(u, setor_id, papel_, real, proj):
        return {'id': u.id, 'name': u.get_full_name(), 'role_key': papel_, 'role_label': papel_.title(), 'sector': 'ZZ',
                'sector_id': setor_id, 'coordinator': 'ZZ COORD', 'realizado': real, 'projecao': proj, 'diferenca': proj - real,
                'fatores': [{'tipo': 'pilar', 'label': 'Móvel', 'delta': proj - real, 'realizado': real, 'projecao': proj,
                             'atingimento': 0.8}], 'motivo': 'Móvel +', 'has_data': True, 'erro': ''}

    dataset = {'rows': [linha(cons, loja.id, sv.ROLE_CONSULTOR, 100.0, 150.0), linha(fora, outra.id, sv.ROLE_CONSULTOR, 50.0, 40.0)],
               'generated_at': None, 'built_on': None, 'status': 'ready', 'is_stale': False}
    with mock.patch('simulator.tabela.get_tabela_dataset', return_value=dataset):
        t('"pode_ver_tabela" = a porta da web (pode_ver_comissionamento)',
          all(c.get(f'{M}/').json()['pode_ver_tabela'] == pode_ver_comissionamento(u)
              for c, u in ((c_cons, cons), (c_ger, gerente), (c_chefe, chefe))))
        r = c_ger.get(f'{M}/tabela/')
        j = r.json()
        t('gerente: só a própria loja (_scope_rows)', r.status_code == 200 and [x['id'] for x in j['linhas']] == [cons.id]
          and j['resumo']['projecao'] == 150.0 and j['linhas'][0]['fatores'][0]['delta'] == 50.0, r.content[:300])
        j = c_chefe.get(f'{M}/tabela/').json()
        t('superadmin: a rede toda', {x['id'] for x in j['linhas']} == {cons.id, fora.id} and j['papeis'][0]['quantos'] == 2)
        with mock.patch.object(mod, '_pode_ver_tabela', return_value=False):
            t('quem não vê o comissionamento: 403', c_cons.get(f'{M}/tabela/').status_code == 403)
    with mock.patch('simulator.tabela.get_tabela_dataset',
                    return_value={'rows': [], 'generated_at': None, 'built_on': None, 'status': 'building', 'is_stale': True}):
        t('montando: "calculando", sem rodar o motor aqui', c_chefe.get(f'{M}/tabela/').json()['status'] == 'calculando')

    print('\n== NÚMEROS: API × WEB (pessoas reais, só leitura) ==')
    fabrica = APIRequestFactory()

    def api(u, **params):
        req = fabrica.get(f'{M}/', params)
        force_authenticate(req, user=u)
        return silencioso(mod.resumo, req)

    def web(u, **params):
        capturado = {}

        def render_falso(request, template, context=None, *a, **kw):
            capturado['ctx'] = context or {}
            return HttpResponse('ok')

        req = RequestFactory().get('/simulator/', {'fragment': 'results', **params})
        req.user = u
        req._messages = CookieStorage(req)
        with mock.patch('simulator.views.render', side_effect=render_falso):
            silencioso(sim_views.simulator_dashboard, req)
        return capturado.get('ctx', {}).get('simulation')

    def comparar(rotulo, u, **params):
        t0 = time.time()
        r = api(u, **params)
        tempo = time.time() - t0
        sim_web = web(u, **params)
        s = r.data.get('simulacao')
        if not sim_web or sim_web.get('error'):
            print(f'  ({rotulo}: a web também não calculou — {sim_web and sim_web.get("error")})')
            t(f'{rotulo}: API dá o mesmo erro que a web', s is None and r.data.get('erro'))
            return tempo
        t(f'{rotulo}: ganho total igual à web', s and abs(s['ganho_total'] - round(sim_web['totals']['ganho_total'], 2)) < 0.01,
          (s and s['ganho_total'], sim_web['totals']['ganho_total']))
        iguais = s and len(s['pilares']) == len(sim_web['rows']) and all(
            a['chave'] == w['key']
            and abs((a['atingimento'] or 0) - round(w.get('attainment') or 0, 4)) < 0.0001
            and abs((a['meta'] or 0) - round(w.get('meta') or 0, 2)) < 0.01
            and abs((a['por_dia'] or 0) - round(w.get('daily_needed') or 0, 2)) < 0.01
            for a, w in zip(s['pilares'], sim_web['rows']))
        t(f'{rotulo}: pilar a pilar (meta, atingimento, meta-diária) igual', iguais)
        return tempo

    def real_de(papel):
        for u in (User.objects.filter(is_active=True, sector__isnull=False)
                  .exclude(email__endswith='@exemplo-teste.local').order_by('id')):
            if sv.get_user_role(u) == papel and not bloqueio_do_usuario(u, usar_cache=False):
                if papel != sv.ROLE_GERENTE or not sv.is_gerente_operacional(u):
                    return u
        return None

    cons_real = None
    for u in sv.get_all_consultors():
        if u.sector_id and not bloqueio_do_usuario(u, usar_cache=False):
            cons_real = u
            break
    t('achou um consultor real', cons_real is not None)
    if cons_real:
        frio = comparar('consultor (projeção)', cons_real)
        quente = comparar('consultor (projeção, de novo)', cons_real)
        print(f'  tempo do consultor: {frio:.1f} s frio (MySQL do mês) / {quente:.2f} s com o mês em cache')
        t('com o realizado do mês em cache responde em menos de 5 s', quente < 5, quente)
        r = api(cons_real, visao='realizado')
        sim_web = web(cons_real, view='realizado')
        t('consultor (realizado): ganho igual à web', r.data.get('simulacao') and sim_web
          and abs(r.data['simulacao']['ganho_total'] - round(sim_web['totals']['ganho_total'], 2)) < 0.01)
        r = api(cons_real, hunter_movel='2', acelerador='1')
        sim_web = web(cons_real, hunter_movel='2', acelerador='1')
        t('consultor com Hunter II e acelerador: igual à web', r.data.get('simulacao') and sim_web
          and abs(r.data['simulacao']['ganho_total'] - round(sim_web['totals']['ganho_total'], 2)) < 0.01
          and r.data['simulacao']['acelerador_ligado'] is True)
        req = fabrica.get(f'{M}/', {'usuario': fora.id})
        force_authenticate(req, user=cons_real)
        t('consultor real não abre outra loja: 404', mod.resumo(req).status_code == 404)
    ger_real = real_de(sv.ROLE_GERENTE)
    if ger_real:
        tempo = comparar('gerente', ger_real)
        print(f'  tempo do gerente: {tempo:.1f} s')
    coord_real = next((u for u in sv.get_all_coordinators() if not sv.is_sniper_user(u)
                       and not bloqueio_do_usuario(u, usar_cache=False)), None)
    if coord_real:
        tempo = comparar('coordenador', coord_real)
        print(f'  tempo do coordenador: {tempo:.1f} s')
        ids_api = {p['id'] for p in mod.alvos_disponiveis(coord_real, sv.ROLE_COORDENADOR)}
        setores = sv.get_coordinator_sectors(coord_real)
        t('coordenador real: seletor = ele + quem está na carteira',
          ids_api == {coord_real.id} | set(User.objects.filter(is_active=True, sector__in=setores)
                                           .exclude(id__in=sv.get_simulator_excluded_user_ids())
                                           .values_list('id', flat=True)))
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
