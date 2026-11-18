"""Módulo Power BI no app (/api/mobile/v1/m/power_bi/): acesso, lista e abrir.

- Usuários e dashboards de teste (@exemplo-teste.local / nomes ZZ) dentro de uma
  transação desfeita no fim; login pelo endpoint do app.
- A lista é comparada com a da tela web (``_visible_reports_for``).
- Cache em memória (LocMem) para não limpar nem gravar no Redis compartilhado.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')

import redeconfianca.settings as _cfg  # noqa: E402

# Nunca o Redis compartilhado: o clear() do django-redis é FLUSHDB no banco inteiro.
_cfg.CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': f'teste-pbi-{n}'}
               for n in _cfg.CACHES}

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import caches  # noqa: E402

if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.auth.models import AnonymousUser  # noqa: E402
from django.db import transaction  # noqa: E402
from django.test import Client  # noqa: E402

from api_mobile.modulos import power_bi as mod  # noqa: E402
from communications.models import CommunicationGroup  # noqa: E402
from power_bi import views as pv  # noqa: E402
from power_bi.models import PowerBIAccessLog, PowerBIReport  # noqa: E402
from users.models import Sector  # noqa: E402

User = get_user_model()
ok = fail = 0
BASE = '/api/mobile/v1'
M = f'{BASE}/m/power_bi'


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


for nome in settings.CACHES:
    assert 'locmem' in settings.CACHES[nome]['BACKEND'].lower(), 'cache do teste precisa ser LocMem'
    caches[nome].clear()          # só LocMem (conferido acima)

marcador = transaction.atomic()
marcador.__enter__()
try:
    loja = Sector.objects.create(name='ZZ Loja PBI App')
    outra = Sector.objects.create(name='ZZ Outra PBI App')

    def novo(apelido, hierarquia='PADRAO', setor=loja):
        return User.objects.create_user(
            username=f'zzpbi.{apelido}', email=f'zzpbi.{apelido}@exemplo-teste.local', password='S3nha!teste',
            first_name=f'ZZPbi{apelido.title()}', last_name='Teste', sector=setor, hierarchy=hierarquia)

    ana = novo('ana')
    beto = novo('beto', setor=outra)
    chefe = novo('chefe', 'SUPERADMIN', outra)
    grupo = CommunicationGroup.objects.create(name='ZZ Grupo PBI App', created_by=chefe)
    grupo.members.add(beto)

    def relatorio(nome, nova_guia=False, ativo=True, url='https://app.powerbi.com/view?r=zz-teste'):
        return PowerBIReport.objects.create(name=nome, embed_url=url, allow_open_in_new_tab=nova_guia,
                                            is_active=ativo, sort_order=0)

    r_setor = relatorio('ZZ BI do setor', nova_guia=True)
    r_setor.allowed_sectors.add(loja)
    r_grupo = relatorio('ZZ BI do grupo', url='https://painel.exemplo-teste.local/zz')
    r_grupo.allowed_groups.add(grupo)
    r_pessoa = relatorio('ZZ BI da pessoa')
    r_pessoa.allowed_users.add(beto)
    r_hier = relatorio('ZZ BI de hierarquia')
    r_hier.allowed_hierarchies = ['ADMIN']
    r_hier.save()
    r_inativo = relatorio('ZZ BI inativo', ativo=False)
    r_todos = relatorio('ZZ BI para todos')

    def entrar(u):
        r = Client().post(f'{BASE}/auth/login/', {'email': u.email, 'senha': 'S3nha!teste', 'plataforma': 'android',
                                                   'dispositivo': 'ZZ'}, content_type='application/json')
        return Client(HTTP_AUTHORIZATION=f"Bearer {r.json()['token']}")

    c_ana, c_beto, c_chefe = (entrar(u) for u in (ana, beto, chefe))

    print('== ACESSO ==')
    chaves = {m['chave'] for m in c_ana.get(f'{BASE}/modulos/').json()}
    t('logado vê o módulo (a web mostra o menu a todos)', 'power_bi' in chaves, chaves)
    t('e entra (200)', c_ana.get(f'{M}/').status_code == 200)
    t('sem token: 401/403', Client().get(f'{M}/').status_code in (401, 403))
    t('anônimo não passa no gate', mod.pode_entrar(AnonymousUser()) is False)
    inativo = novo('inativo')
    inativo.is_active = False
    t('inativo não passa no gate', mod.pode_entrar(inativo) is False)

    print('\n== LISTA ==')
    r = c_ana.get(f'{M}/')
    ids = [d['id'] for d in r.json()['resultados']]
    t('a lista é a mesma da web (mesma ordem)', ids == [x.id for x in pv._visible_reports_for(ana)])
    t('vê o do setor e o sem regra', {r_setor.id, r_todos.id} <= set(ids), ids)
    t('não vê o de grupo, de pessoa, de hierarquia e o inativo',
      not ({r_grupo.id, r_pessoa.id, r_hier.id, r_inativo.id} & set(ids)))
    item = next(d for d in r.json()['resultados'] if d['id'] == r_setor.id)
    t('item sem o link do embed na lista', 'url' not in item and 'embed_url' not in item and item['abre_direto'] is True
      and item['origem'] == 'power_bi', item)
    ids_b = {d['id'] for d in c_beto.get(f'{M}/').json()['resultados']}
    t('beto vê o do grupo e o dele, não o do setor da ana', {r_grupo.id, r_pessoa.id} <= ids_b and r_setor.id not in ids_b)
    t('origem "outros" fora do Power BI',
      next(d for d in c_beto.get(f'{M}/').json()['resultados'] if d['id'] == r_grupo.id)['origem'] == 'outros')
    ids_c = {d['id'] for d in c_chefe.get(f'{M}/').json()['resultados']}
    t('SUPERADMIN vê todos os ativos', {r_setor.id, r_grupo.id, r_pessoa.id, r_hier.id, r_todos.id} <= ids_c
      and r_inativo.id not in ids_c)

    print('\n== ABRIR ==')
    antes = PowerBIAccessLog.objects.filter(report=r_setor, user=ana).count()
    r = c_ana.post(f'{M}/{r_setor.id}/abrir/')
    t('com nova guia: devolve o link do embed', r.status_code == 200 and r.json()['url'] == r_setor.embed_url
      and r.json()['onde'] == 'direto', r.content[:200])
    t('e registra o acesso como o viewer da web',
      PowerBIAccessLog.objects.filter(report=r_setor, user=ana).count() == antes + 1)
    r = c_ana.post(f'{M}/{r_todos.id}/abrir/')
    t('sem nova guia: abre a página do portal', r.status_code == 200 and r.json()['onde'] == 'portal'
      and r.json()['url'].endswith(f'/power-bi/{r_todos.id}/'), r.content[:200])
    t('sem log duplicado (a página do portal registra)',
      not PowerBIAccessLog.objects.filter(report=r_todos, user=ana).exists())
    t('o de outra pessoa: 404', c_ana.post(f'{M}/{r_pessoa.id}/abrir/').status_code == 404)
    t('o de grupo que não é dela: 404', c_ana.post(f'{M}/{r_grupo.id}/abrir/').status_code == 404)
    t('inativo: 404 até para o SUPERADMIN', c_chefe.post(f'{M}/{r_inativo.id}/abrir/').status_code == 404)
    t('id que não existe: 404', c_ana.post(f'{M}/999999999/abrir/').status_code == 404)
    t('beto abre o dele', c_beto.post(f'{M}/{r_pessoa.id}/abrir/').status_code == 200)
    t('abrir por GET: 405', c_ana.get(f'{M}/{r_setor.id}/abrir/').status_code == 405)
finally:
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada deste teste foi gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
