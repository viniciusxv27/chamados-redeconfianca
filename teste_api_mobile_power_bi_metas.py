"""Módulo Metas no app (/api/mobile/v1/m/power_bi_metas/): acesso, recorte e números.

- Carga de teste (competência 01/2099) com lojas e pessoas ZZ dentro de uma
  transação desfeita no fim; login pelo endpoint do app.
- Números: a API e a view web (goals_list_view, com o render trocado por quem
  guarda o contexto) para as MESMAS pessoas — as de teste e, na carga real mais
  recente, pessoas reais (sem criar sessão nem token para elas).
- Cache em memória (LocMem) para não limpar nem gravar no Redis compartilhado.
"""
import contextlib
import io
import os
import sys
from decimal import Decimal
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')

import redeconfianca.settings as _cfg  # noqa: E402

# Nunca o Redis compartilhado: o clear() do django-redis é FLUSHDB no banco inteiro.
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-pbim-{n}'}
               for n in _cfg.CACHES}

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import caches  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.messages.storage.cookie import CookieStorage  # noqa: E402
from django.db import transaction  # noqa: E402
from django.http import HttpResponse  # noqa: E402
from django.test import Client, RequestFactory  # noqa: E402

from api_mobile.modulos import power_bi_metas as mod  # noqa: E402
from communications.models import CommunicationGroup  # noqa: E402
from power_bi import views as pv  # noqa: E402
from power_bi.models import GoalEntry, GoalUpload  # noqa: E402
from users.models import Sector  # noqa: E402

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/power_bi_metas'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def quieto(fn, *a, **kw):
    """_is_gerentes_group_user imprime uma linha por chamada."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


capturado = {}


def render_falso(request, template, ctx=None, *a, **kw):
    capturado['ctx'] = ctx or {}
    return HttpResponse('ok')


def web(user, **get):
    capturado.clear()
    req = RequestFactory().get('/power-bi/metas/', get)
    req.user = user
    req._messages = CookieStorage(req)
    with mock.patch('power_bi.views.render', side_effect=render_falso):
        quieto(pv.goals_list_view, req)
    return capturado['ctx']


def mesmos_numeros(nome, user, get_web, get_api=None):
    """A API e a view web para a mesma pessoa e o mesmo filtro."""
    w = web(user, **get_web)
    a = quieto(mod.contexto, user, get_api or get_web)
    def dec(x):
        return round(float(x or 0), 2)
    iguais = [
        ('perfil', (w['is_cn_user'], w['is_gerente_user']), (a['is_cn_user'], a['is_gerente_user'])),
        ('competência', w['current_upload'] and w['current_upload'].id, a['current_upload'] and a['current_upload'].id),
        ('meta total', dec(w['total_value']), dec(a['total_value'])),
        ('total CN', dec(w['cn_total']), dec(a['cn_total'])),
        ('total loja', dec(w['pdv_total']), dec(a['pdv_total'])),
        ('linhas CN/loja', (w['cn_entries_count'], w['pdv_entries_count']), (a['cn_entries_count'], a['pdv_entries_count'])),
        ('painel CN', [(r['pilar'], dec(r['total'])) for r in w['cn_pilar_rows']], [(r['pilar'], dec(r['total'])) for r in a['cn_pilar_rows']]),
        ('painel loja', [(r['pilar'], dec(r['total'])) for r in w['pdv_pilar_rows']], [(r['pilar'], dec(r['total'])) for r in a['pdv_pilar_rows']]),
        ('top consultores', [(x['name'], dec(x['total'])) for x in w['top_consultants']], [(x['nome'], dec(x['total'])) for x in a['top_consultants']]),
        ('top lojas', [(x['store'], dec(x['total'])) for x in w['top_stores']], [(x['nome'], dec(x['total'])) for x in a['top_stores']]),
        ('lojas/pilares/vendedores', (w['all_stores'], w['all_pilares'], w['available_sellers'], w['available_cns']),
         (a['all_stores'], a['all_pilares'], a['available_sellers'], a['available_cns'])),
        ('tabela do gerente', [(r['seller'], dec(r['row_total']), [dec(c['value']) for c in r['ordered_cells']]) for r in w['manager_cn_table_rows']],
         [(r['vendedor'], dec(r['total']), [dec(c['valor']) for c in r['celulas']]) for r in (a['manager_table'] or {}).get('linhas', [])]),
    ]
    dif = [(k, x, y) for k, x, y in iguais if x != y]
    t(f'{nome}: mesmos números da web', not dif, dif[:2])


for nome in settings.CACHES:
    assert 'locmem' in settings.CACHES[nome]['BACKEND'].lower(), 'cache do teste precisa ser LocMem'
    caches[nome].clear()          # só LocMem (conferido acima)

marcador = transaction.atomic()
marcador.__enter__()
try:
    # Nomes sem palavras em comum: o _stores_match junta lojas com 2 palavras iguais.
    loja = Sector.objects.create(name='QWXZ ALFAZZ')
    outra = Sector.objects.create(name='PLMK BETAZZ')

    def novo(apelido, hierarquia='PADRAO', setor=loja):
        return User.objects.create_user(
            username=f'zzmet.{apelido}', email=f'zzmet.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZMet{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia)

    cn = novo('cn')
    colega = novo('colega')
    fora = novo('fora', setor=outra)
    gerente = novo('gerente')
    rede = novo('rede', 'ADMIN', outra)
    chefe = novo('chefe', 'SUPERADMIN', outra)
    grupo_gerentes = CommunicationGroup.objects.filter(name__icontains='GERENTES').first()
    grupo_gerentes.members.add(gerente)

    carga = GoalUpload.objects.create(year=2099, month=1, fixa_as_percentage=True, source_file_name='zz_metas.xlsx',
                                      uploaded_by=chefe)
    n = 0

    def linha(folha, nome, loja_nome, pilar, valor):
        global n
        n += 1
        GoalEntry.objects.create(upload=carga, sheet_type=folha, user_name=nome, store_name=loja_nome, pilar=pilar,
                                 goal_value=Decimal(valor), row_number=n)

    CN, PDV = GoalEntry.SHEET_CN_REAL, GoalEntry.SHEET_PDV_REAL
    linha(CN, cn.get_full_name(), 'QWXZ ALFAZZ', 'MOVEL', '1000')
    linha(CN, cn.get_full_name(), 'QWXZ ALFAZZ', 'FIXA', '5')
    linha(CN, colega.get_full_name(), 'QWXZ ALFAZZ', 'MOVEL', '2000')
    linha(CN, fora.get_full_name(), 'PLMK BETAZZ', 'MOVEL', '3000')
    linha(PDV, '', 'QWXZ ALFAZZ', 'MOVEL', '10000')
    linha(PDV, '', 'QWXZ ALFAZZ', 'FIXA', '20')
    linha(PDV, '', 'PLMK BETAZZ', 'MOVEL', '50000')

    def entrar(u):
        r = Client().post(f'{BASE}/auth/login/', {'email': u.email, 'senha': 'S3nha!teste', 'plataforma': 'android',
                                                   'dispositivo': 'ZZ'}, content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")

    c_cn, c_ger, c_rede, c_chefe = (entrar(u) for u in (cn, gerente, rede, chefe))

    def get(c, **params):
        with contextlib.redirect_stdout(io.StringIO()):
            return c.get(f'{M}/', params)

    print('== ACESSO ==')
    chaves = {m['chave'] for m in c_cn.get(f'{BASE}/modulos/').json()}
    t('logado vê o módulo (a web mostra o menu a todos)', 'power_bi_metas' in chaves, chaves)
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))
    t('consultor não vê o histórico de cargas (403)', c_cn.get(f'{M}/cargas/').status_code == 403)
    t('ADMIN também não (só SUPERADMIN, como a web)', c_rede.get(f'{M}/cargas/').status_code == 403)
    r = c_chefe.get(f'{M}/cargas/')
    nossa = next((x for x in r.json()['resultados'] if x['id'] == carga.id), None) if r.status_code == 200 else None
    t('SUPERADMIN vê as cargas com arquivo e itens', nossa and nossa['itens'] == 7 and nossa['arquivo'] == 'zz_metas.xlsx'
      and nossa['rotulo'] == '01/2099', nossa)

    print('\n== CONSULTOR ==')
    r = get(c_cn)
    j = r.json()
    t('abre a carga mais recente', r.status_code == 200 and j['competencia']['rotulo'] == '01/2099', r.content[:200])
    t('perfil consultor, sem filtros', j['perfil'] == 'cn' and not j['filtros']['lojas'] and not j['filtros']['vendedores'])
    t('só as linhas dele: MOVEL 1000 e FIXA em quantidade',
      [(p['pilar'], p['total'], p['quantidade']) for p in j['painel_cn']] == [('FIXA', 5.0, True), ('MOVEL', 1000.0, False)],
      j['painel_cn'])
    t('total CN sem a FIXA; loja = a dele', j['totais']['cn'] == 1000 and j['totais']['loja'] == 10000
      and j['totais']['meta'] == 10000, j['totais'])
    t('não vê o colega nem a outra loja', [x['nome'] for x in j['top_consultores']['itens']] == [cn.get_full_name()]
      and all(p['total'] != 50000 for p in j['painel_loja']))
    j2 = get(c_cn, loja='PLMK BETAZZ', vendedor=fora.get_full_name()).json()
    t('filtro de loja/vendedor é ignorado para o consultor', j2['totais'] == j['totais'] and j2['painel_cn'] == j['painel_cn'])
    mesmos_numeros('consultor', cn, {})

    print('\n== GERENTE ==')
    j = get(c_ger).json()
    t('perfil gerente, sem meta total', j['perfil'] == 'gerente' and j['totais']['meta'] is None)
    nomes = [lin['vendedor'] for lin in j['tabela_gerente']['linhas']]
    t('tabela com os CNs da loja dele, sem a outra loja',
      sorted(nomes) == sorted([cn.get_full_name(), colega.get_full_name()]), nomes)
    lin_cn = next(lin for lin in j['tabela_gerente']['linhas'] if lin['vendedor'] == cn.get_full_name())
    t('linha do CN: MOVEL na coluna certa e FIXA fora do total',
      lin_cn['celulas'][2]['valor'] == 1000 and lin_cn['celulas'][3]['quantidade'] is True and lin_cn['total'] == 1000, lin_cn)
    j2 = get(c_ger, cn=colega.get_full_name()).json()
    t('filtro por CN da loja', [lin['vendedor'] for lin in j2['tabela_gerente']['linhas']] == [colega.get_full_name()]
      and j2['totais']['cn'] == 2000)
    j3 = get(c_ger, cn=fora.get_full_name()).json()
    t('CN de outra loja: nada (não vaza)', j3['tabela_gerente']['linhas'] == [] and j3['totais']['cn'] == 0)
    mesmos_numeros('gerente', gerente, {})
    mesmos_numeros('gerente com CN', gerente, {'cn': colega.get_full_name()})

    print('\n== REDE ==')
    j = get(c_rede).json()
    t('perfil rede com lojas e pilares', j['perfil'] == 'rede' and {'QWXZ ALFAZZ', 'PLMK BETAZZ'} <= set(j['filtros']['lojas'])
      and j['filtros']['pilares'] == ['FIXA', 'MOVEL'], j['filtros'])
    t('meta total = soma das lojas (sem FIXA)', j['totais']['loja'] == 60000 and j['totais']['meta'] == 60000, j['totais'])
    t('top lojas em ordem', [x['nome'] for x in j['top_lojas']['itens']] == ['PLMK BETAZZ', 'QWXZ ALFAZZ'])
    t('ADMIN sem is_superuser não tem o top consultores (igual à web)', j['top_consultores'] is None)
    j2 = get(c_rede, loja='PLMK BETAZZ').json()
    t('filtro por loja', j2['totais']['loja'] == 50000 and j2['totais']['cn'] == 3000 and j2['filtros']['loja'] == 'PLMK BETAZZ')
    j3 = get(c_rede, vendedor=colega.get_full_name()).json()
    t('filtro por vendedor: total é só o dele', j3['totais']['meta'] == 2000 and j3['totais']['cn'] == 2000, j3['totais'])
    mesmos_numeros('rede', rede, {})
    mesmos_numeros('rede por loja', rede, {'store': 'PLMK BETAZZ', 'pilar': 'MOVEL'})
    mesmos_numeros('rede por vendedor', rede, {'seller': colega.get_full_name()})

    print('\n== COMPETÊNCIA ==')
    real = GoalUpload.objects.exclude(id=carga.id).order_by('-year', '-month').first()
    if real:
        j = get(c_rede, ano=real.year, mes=real.month).json()
        t('escolhe outra competência', j['competencia']['rotulo'] == f'{real.month:02d}/{real.year}'
          and j['competencia_trocada'] is False)
        t('lista de competências traz as duas', {'01/2099', f'{real.month:02d}/{real.year}'} <= {c['rotulo'] for c in j['competencias']})
    j = get(c_rede, ano=1999, mes=3).json()
    t('mês sem carga: cai na mais recente e avisa', j['competencia']['rotulo'] == '01/2099' and j['competencia_trocada'] is True)
    t('ano inválido não quebra', get(c_rede, ano='abc', mes='x').status_code == 200)

    print('\n== PESSOAS REAIS (carga real mais recente) ==')
    if real:
        filtro = {'year': str(real.year), 'month': str(real.month)}
        nomes_cn = set(GoalEntry.objects.filter(upload=real, sheet_type=CN).values_list('user_name', flat=True))
        padroes = User.objects.filter(is_active=True, hierarchy='PADRAO').exclude(email__endswith='@exemplo-teste.local')
        cn_real = next((u for u in padroes.exclude(communication_groups=grupo_gerentes)[:400]
                        if u.get_full_name().strip().upper() in {(x or '').strip().upper() for x in nomes_cn}), None)
        ger_real = (padroes.filter(communication_groups=grupo_gerentes).exclude(id=gerente.id)
                    .exclude(sector__isnull=True).first())
        sup_real = User.objects.filter(is_active=True, is_superuser=True).first()
        adm_real = User.objects.filter(is_active=True, hierarchy='ADMINISTRATIVO').first()
        for rot, u in (('consultor real', cn_real), ('gerente real', ger_real), ('superuser real', sup_real),
                       ('administrativo real', adm_real)):
            if u:
                mesmos_numeros(rot, u, filtro)
            else:
                print(f'  ({rot}: ninguém no banco, pulo)')
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
