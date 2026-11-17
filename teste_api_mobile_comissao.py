"""Módulo Comissão no app (/api/mobile/v1/m/comissao/): acesso, recorte e números.

- Usuários de teste (@exemplo-teste.local) para a regra de acesso, pelo login do app.
- Números: a API e a view web (commission_view) para a MESMA pessoa real, sem
  criar sessão nem token para ela (APIRequestFactory + force_authenticate; a view
  web por RequestFactory com o render trocado por quem guarda o contexto).
- Cache em memória (LocMem) para não limpar nem gravar no Redis compartilhado.
- Tudo dentro de uma transação desfeita no fim.
"""
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')

import redeconfianca.settings as _cfg  # noqa: E402

# Nunca o Redis compartilhado: o clear() do django-redis é FLUSHDB no banco inteiro.
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-comissao-{n}'}
               for n in _cfg.CACHES}

import warnings  # noqa: E402

import django  # noqa: E402

warnings.filterwarnings('ignore', message='Cache key contains characters')

django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import cache, caches  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.messages.storage.cookie import CookieStorage  # noqa: E402
from django.db import transaction  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.test import Client, RequestFactory  # noqa: E402
from rest_framework.test import APIRequestFactory, force_authenticate  # noqa: E402

from api_mobile.modulos import comissao as mod  # noqa: E402
from api_mobile.permissoes import bloqueio_do_usuario  # noqa: E402
from communications.models import CommunicationGroup  # noqa: E402
from users import commission_views as cv  # noqa: E402
from users.models import (CommissionSpreadsheetVersion, CommissionUserReferenceHistory, Sector,  # noqa: E402
                          UserModuleAccess)

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/comissao'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def silencioso(fn, *a, **kw):
    """As funções da planilha imprimem DEBUG a cada coluna; o teste não precisa disso."""
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


for nome in settings.CACHES:
    assert 'locmem' in settings.CACHES[nome]['BACKEND'].lower(), 'cache do teste precisa ser LocMem'
    caches[nome].clear()          # só LocMem (conferido acima)

marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='ZZ Loja Comissao App')
    outra = Sector.objects.create(name='ZZ Outra Loja Comissao App')

    def novo(apelido, hierarquia='PADRAO', setor=loja):
        return User.objects.create_user(
            username=f'zzcom.{apelido}', email=f'zzcom.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZCom{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia)

    cn = novo('cn')
    colega = novo('colega')
    fora = novo('fora', setor=outra)
    gerente = novo('gerente')
    chefe = novo('chefe', 'SUPERADMIN', outra)
    grupo_gerentes = CommunicationGroup.objects.filter(name__icontains='GERENTES').first()
    grupo_gerentes.members.add(gerente)

    def entrar(u):
        r = Client().post(f'{BASE}/auth/login/', {'email': u.email, 'senha': 'S3nha!teste', 'plataforma': 'android',
                                                   'dispositivo': 'ZZ'}, content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")

    c_cn, c_colega, c_fora, c_ger, c_chefe = (entrar(u) for u in (cn, colega, fora, gerente, chefe))

    def chaves_modulos(c):
        return {m['chave'] for m in c.get(f'{BASE}/modulos/').json()}

    print('== ACESSO ==')
    with mock.patch('users.commission_liberacao.liberado_para_todos', return_value=True):
        t('versão liberada a todos: o consultor vê o módulo', 'comissao' in chaves_modulos(c_cn))
        r = c_cn.get(f'{M}/')
        t('e entra (200)', r.status_code == 200, r.content[:300])
    with mock.patch('users.commission_liberacao.liberado_para_todos', return_value=False):
        t('sem versão para todos: o consultor não vê o módulo', 'comissao' not in chaves_modulos(c_cn))
        t('e leva 403 no resumo', c_cn.get(f'{M}/').status_code == 403)
        t('e 403 no histórico', c_cn.get(f'{M}/historico/').status_code == 403)
        t('o gerente continua entrando', c_ger.get(f'{M}/pessoas/').status_code == 200)
        t('o superadmin continua entrando', 'comissao' in chaves_modulos(c_chefe))
        UserModuleAccess.objects.create(user=cn, module_key='comissionamento')
        t('liberação individual devolve o acesso', c_cn.get(f'{M}/pessoas/').status_code == 200)
        UserModuleAccess.objects.filter(user=cn).delete()
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))

    with mock.patch('users.commission_liberacao.sem_versao_liberada', return_value=True):
        r = c_cn.get(f'{M}/')
        t('nenhuma versão liberada para o perfil: sem número, com aviso',
          r.status_code == 200 and r.json()['liberado'] is False and r.json()['dados'] is None
          and r.json()['aviso'], r.content[:200])

    print('\n== REFERÊNCIA ==')
    restrita = CommissionSpreadsheetVersion.objects.filter(
        status=CommissionSpreadsheetVersion.STATUS_RELEASED,
        liberado_para=CommissionSpreadsheetVersion.LIBERADO_GERENTES_COORDENADORES).first()
    if restrita:
        r = c_cn.get(f'{M}/', {'ano': restrita.year, 'mes': restrita.month, 'fase': restrita.contestacao_phase})
        ref = r.json()['referencia']
        t('consultor pedindo a prévia restrita cai na versão liberada',
          r.status_code == 200 and ref['selecao_invalida'] is True
          and (ref['ano'], ref['mes'], ref['fase']) != (restrita.year, restrita.month, restrita.contestacao_phase),
          ref)
        t('e a prévia restrita não aparece nas opções dele',
          all((o['ano'], o['mes'], o['fase']) != (restrita.year, restrita.month, restrita.contestacao_phase)
              for o in r.json()['opcoes']))
        r = c_ger.get(f'{M}/pessoas/', {'ano': restrita.year, 'mes': restrita.month, 'fase': restrita.contestacao_phase})
        t('o gerente abre a mesma referência', r.status_code == 200)
    else:
        print('  (sem versão restrita no banco: pulo a checagem da prévia)')

    print('\n== RECORTE ==')
    r = c_cn.get(f'{M}/')
    j = r.json()
    t('consultor vê a si mesmo (sem planilha: erro amigável, sem quebrar)',
      j['alvo']['id'] == cn.id and j['vendo_outro'] is False and (j['dados'] is not None or j['erro']), j)
    t('consultor não tem seletor de pessoa', j['pode_ver_outros'] is False
      and c_cn.get(f'{M}/pessoas/').json()['resultados'] == [])
    t('consultor abrindo o colega: 404', c_cn.get(f'{M}/', {'usuario': colega.id}).status_code == 404)
    t('id inválido: 404', c_cn.get(f'{M}/', {'usuario': 'abc'}).status_code == 404)
    r = c_ger.get(f'{M}/pessoas/')
    ids = {p['id'] for p in r.json()['resultados']}
    t('gerente lista os consultores da loja', {cn.id, colega.id} <= ids and fora.id not in ids, ids)
    r = c_ger.get(f'{M}/', {'usuario': cn.id})
    t('gerente abre o consultor da loja', r.status_code == 200 and r.json()['vendo_outro'] is True
      and r.json()['alvo']['id'] == cn.id, r.content[:200])
    t('gerente não abre quem é de outra loja: 404', c_ger.get(f'{M}/', {'usuario': fora.id}).status_code == 404)
    r = c_chefe.get(f'{M}/')
    t('superadmin sem escolher: pede para escolher', r.json()['precisa_escolher'] is True and r.json()['dados'] is None)
    t('superadmin abre qualquer pessoa', c_chefe.get(f'{M}/', {'usuario': fora.id}).status_code == 200)
    ids = {p['id'] for p in c_chefe.get(f'{M}/pessoas/').json()['resultados']}
    t('diretório do superadmin traz os consultores', {cn.id, fora.id} <= ids)

    print('\n== HISTÓRICO ==')
    versao = CommissionSpreadsheetVersion.objects.filter(
        status=CommissionSpreadsheetVersion.STATUS_RELEASED, contestacao_phase='pos',
        liberado_para=CommissionSpreadsheetVersion.LIBERADO_TODOS).order_by('-year', '-month').first()

    def linha_hist(u, valor):
        return CommissionUserReferenceHistory.objects.create(
            year=versao.year, month=versao.month, user_name=u.get_full_name(), user=u, role='cn',
            sheet_name='ZZ TESTE', source_version=versao,
            row_data={'PDV': u.sector.name, 'CARGO': 'CN', 'Total Comissão': valor,
                      'REMUNERAÇÃO FINAL TOTAL': valor})

    linha_hist(cn, 1234.5)
    linha_hist(colega, 777)
    linha_hist(fora, 999)
    r = c_cn.get(f'{M}/historico/')
    j = r.json()
    mes = next((m for m in j['meses'] if m['chave'] == f'{versao.year}-{versao.month:02d}'), None)
    t('consultor vê o próprio mês no histórico', r.status_code == 200 and mes and mes['total']['recebido'] == 1234.5
      and [lin['pessoa'] for lin in mes['linhas']] == [cn.get_full_name()], mes)
    t('e não tem a visão de equipe', j['tem_equipe'] is False
      and c_cn.get(f'{M}/historico/', {'escopo': 'equipe'}).json()['escopo'] == 'eu')
    r = c_ger.get(f'{M}/historico/', {'escopo': 'equipe'})
    nomes = {p['pessoa'] for p in r.json()['pessoas']}
    t('gerente vê a equipe da loja no histórico', {cn.get_full_name(), colega.get_full_name()} <= nomes
      and fora.get_full_name() not in nomes, nomes)
    if restrita:
        CommissionUserReferenceHistory.objects.create(
            year=restrita.year, month=restrita.month, user_name=cn.get_full_name(), user=cn, role='cn',
            sheet_name='ZZ TESTE PREVIA', source_version=restrita,
            row_data={'PDV': cn.sector.name, 'CARGO': 'CN', 'REMUNERAÇÃO FINAL TOTAL': 50})
        chaves = [(m['chave'], m['previa']) for m in c_cn.get(f'{M}/historico/').json()['meses']]
        t('prévia restrita a gerentes não entra no histórico do consultor',
          (f'{restrita.year}-{restrita.month:02d}', True) not in chaves, chaves)

    print('\n== MÉDIAS (PROJEÇÃO) ==')
    from simulator.averages import DATASET_KEY
    from django.utils import timezone

    def linha_proj(u, ganho):
        return {'id': u.id, 'name': u.get_full_name(), 'initials': 'ZZ', 'role_key': 'consultor',
                'role_label': 'Consultor', 'sector': u.sector.name, 'sector_id': u.sector_id,
                'coordinator': '', 'gain': ganho, 'has_data': True}

    cache.set(DATASET_KEY, {'rows': [linha_proj(cn, 1000), linha_proj(colega, 3000), linha_proj(fora, 5000)],
                            'generated_at': timezone.now(), 'built_on': timezone.localdate()}, 600)
    with mock.patch('simulator.averages._refresh_in_background') as recalc:
        r = c_cn.get(f'{M}/projecao/')
        t('consultor sem liberação: 403 nas médias', r.status_code == 403)
        t('e o resumo diz que não pode', c_cn.get(f'{M}/').json()['pode_projecao'] is False)
        r = c_ger.get(f'{M}/projecao/')
        t('gerente sem a liberação individual também não', r.status_code == 403)
        UserModuleAccess.objects.create(user=gerente, module_key='comissionamento.projecao')
        r = c_ger.get(f'{M}/projecao/')
        nomes = {lin['nome'] for lin in r.json().get('linhas', [])}
        t('gerente liberado vê as médias da própria loja', r.status_code == 200
          and nomes == {cn.get_full_name(), colega.get_full_name()} and r.json()['resumo']['media'] == 2000,
          r.content[:300])
        r = c_chefe.get(f'{M}/projecao/')
        t('superadmin vê a rede', r.status_code == 200 and fora.get_full_name() in {lin['nome'] for lin in r.json()['linhas']})
        t('a médias nunca dispara o recálculo da rede com o cache do dia', not recalc.called)

    print('\n== NÚMEROS: API × WEB (pessoa real, só leitura) ==')
    ref_padrao = mod._referencia(chefe, {})
    urls = cv.get_excel_urls(ref_padrao['year'], ref_padrao['month'], ref_padrao['phase'])
    t0 = time.time()
    todos = silencioso(cv.fetch_all_users_from_sheet, cv.SHEET_CN, excel_url=urls['comissao'],
                       cache_suffix=ref_padrao['cache_suffix'])
    print(f'  (planilha de CN lida em {time.time() - t0:.1f} s; o arquivo já veio do OneDrive nas chamadas acima)')
    real = None
    candidatos = User.objects.filter(is_active=True, hierarchy='PADRAO').exclude(email__endswith='@exemplo-teste.local')
    por_nome = {cv.normalize_person_name(u.get_full_name()): u for u in candidatos}
    for linha in (todos.get('users') or []):
        u = por_nome.get(cv.normalize_person_name(linha['data'].get('NOME')))
        if (u and mod.papel_de(u) == 'cn' and cv.pode_ver_comissionamento(u)
                and not bloqueio_do_usuario(u, usar_cache=False)):
            real = u
            break
    t('achou um consultor real na planilha do mês', real is not None)
    if real:
        fabrica = APIRequestFactory()

        def chamar_api(u, **params):
            req = fabrica.get(f'{M}/', params)
            force_authenticate(req, user=u)
            return mod.resumo(req)

        t0 = time.time()
        resp = silencioso(chamar_api, real)
        frio = time.time() - t0
        t0 = time.time()
        resp2 = silencioso(chamar_api, real)
        quente = time.time() - t0
        print(f'  tempo do resumo: {frio:.1f} s frio (planilhas) / {quente:.2f} s com cache')
        t('resumo responde com cache em menos de 3 s', quente < 3, quente)
        api = resp.data['dados']
        t('API trouxe os dados da pessoa real', resp.status_code == 200 and api is not None, resp.data.get('erro'))

        capturado = {}

        def render_falso(request, template, context=None, *a, **kw):
            capturado['template'] = template
            capturado['ctx'] = context or {}
            return HttpResponse('ok')

        req = RequestFactory().get('/users/commission/')
        req.user = real
        req._messages = CookieStorage(req)
        with mock.patch('users.commission_views.render', side_effect=render_falso):
            silencioso(cv.commission_view, req)
        web = capturado.get('ctx', {}).get('data')
        t('a view web montou o comissionamento', web is not None, capturado.get('template'))
        if api and web:
            t('RV total igual à web', abs(api['total'] - round(web['totais']['remuneracao_final'], 2)) < 0.01,
              (api['total'], web['totais']['remuneracao_final']))
            t('total de comissão igual', abs(api['comissao_total'] - round(web['comissoes']['total'], 2)) < 0.01)
            iguais = all(abs(a['atingimentos'][2]['pct'] - round(w['pct_1'], 2)) < 0.01
                         and abs(a['media'] - round(w['media'], 2)) < 0.01
                         and abs(a['total'] - round(w.get('soma_com_iq', 0), 2)) < 0.01
                         for a, w in zip(api['pilares'], web['pilares']))
            t('pilar a pilar: atingimento, média e total iguais', iguais and len(api['pilares']) == len(web['pilares']))
            t('mesma referência que a web', resp.data['referencia']['mes'] == capturado['ctx'].get('selected_reference_month'))
            t('resposta repetida é a mesma', resp2.data['dados']['total'] == api['total'])
        req = fabrica.get(f'{M}/', {'usuario': cn.id})
        force_authenticate(req, user=real)
        t('a pessoa real (consultor) não abre outra: 404', mod.resumo(req).status_code == 404)

        # Um gerente real: a visão de gerente da web (commission_gerente_view) × a API.
        ger_real = next((u for u in cv.get_group_members_by_name('GERENTES').exclude(email__endswith='@exemplo-teste.local')
                         if mod.papel_de(u) == 'gerente' and not bloqueio_do_usuario(u, usar_cache=False)
                         and silencioso(cv.fetch_excel_data, cv.SHEET_GERENTE, u.get_full_name(),
                                        excel_url=urls['comissao'], cache_suffix=ref_padrao['cache_suffix']).get('success')),
                        None)
        if ger_real:
            t0 = time.time()
            resp = silencioso(chamar_api, ger_real)
            print(f'  tempo do resumo do gerente: {time.time() - t0:.1f} s')
            capturado.clear()
            req = RequestFactory().get('/users/commission/')
            req.user = ger_real
            req._messages = CookieStorage(req)
            with mock.patch('users.commission_views.render', side_effect=render_falso):
                silencioso(cv.commission_view, req)
            web = capturado.get('ctx', {}).get('data')
            api = resp.data.get('dados')
            t('gerente real: RV total igual à web', api and web
              and abs(api['total'] - round(web['totais']['remuneracao_final'], 2)) < 0.01,
              (api and api['total'], web and web['totais']['remuneracao_final']))
            ids_web = {u.id for u in capturado['ctx'].get('sector_users', [])}
            ids_api = {p['id'] for p in mod.pessoas_visiveis(ger_real, 'gerente', urls, ref_padrao)}
            t('gerente real: o seletor tem as mesmas pessoas da web', ids_web == ids_api, (ids_web ^ ids_api))
        else:
            print('  (nenhum gerente real com linha na planilha: pulo a comparação do gerente)')
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
