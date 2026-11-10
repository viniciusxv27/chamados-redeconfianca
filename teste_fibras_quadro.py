"""/fibras: kanban e lista do mesmo quadro, rápidos e com cache (10/11/2026).

Rodar: venv/bin/python teste_fibras_quadro.py
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e cache descartáveis (sqlite + LocMem): nada toca o Postgres nem o Redis.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-fibras-quadro-'))
(PASTA / 'zz_fibras_quadro_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3',
                          'NAME': r'{PASTA}/banco.sqlite3'}}}}


class SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = SemMigrations()

USE_S3 = False
MEDIA_ROOT = r'{PASTA}/media'
MEDIA_URL = '/media/'
STORAGES = {{
    'default': {{'BACKEND': 'django.core.files.storage.FileSystemStorage'}},
    'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
}}
DEBUG = False
OPENAI_API_KEY = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fibquadro'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fibquadro-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_fibras_quadro_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from fibras import quadro
from fibras.models import AcessoFibras, Fibra, FibraIncidente
from users.models import Sector

User = get_user_model()
OK = FALHAS = 0


def t(nome, cond, extra=''):
    global OK, FALHAS
    if cond:
        OK += 1
        print('  OK  ', nome)
    else:
        FALHAS += 1
        print('  FALHA', nome, extra)


HOJE = timezone.localdate()
glo = Sector.objects.create(name='Loja ZZ Gloria')
mon = Sector.objects.create(name='Loja ZZ Monts')
rede = User.objects.create_user(username='zz.fq.rede', email='zz.fq.rede@exemplo-teste.local', password='x', first_name='ZZ', last_name='Rede')
AcessoFibras.objects.create(user=rede, permissoes=['modulo.ver', 'rede.ver', 'status.alterar'])
ana = User.objects.create_user(username='zz.fq.ana', email='zz.fq.ana@exemplo-teste.local', password='x', first_name='ZZ', last_name='Ana', sector=glo)
AcessoFibras.objects.create(user=ana, permissoes=['modulo.ver', 'loja.ver'])
bia = User.objects.create_user(username='zz.fq.bia', email='zz.fq.bia@exemplo-teste.local', password='x', first_name='ZZ', last_name='Bia', sector=mon)
AcessoFibras.objects.create(user=bia, permissoes=['modulo.ver', 'loja.ver'])
agora = timezone.now()
seq = [0]


def venda(pdv, status=Fibra.STATUS_PENDENTE, dia=None, cpf='123.456.789-09', pilar='FIXA', planilha=True, ordem_v=''):
    seq[0] += 1
    return Fibra.objects.create(numero_da_venda=f'ZZQ{seq[0]:04d}', numero_protocolo=f'P{seq[0]}', pdv=pdv, cpf=cpf,
                                cliente=f'Cliente {seq[0]}', vendedor='ZZ VEND', pilar=pilar, valor=Decimal('100.50'),
                                status=status, data_da_venda=dia or HOJE, ordem_vinculada=ordem_v,
                                last_planilha_at=agora if planilha else None)


for i in range(20):
    venda('LOJA ZZ GLORIA', status=[Fibra.STATUS_AGENDADO, Fibra.STATUS_PENDENTE, Fibra.STATUS_INSTALADO][i % 3])
for i in range(6):
    venda('LOJA ZZ GLORIA', planilha=False)                          # pendente fora da planilha → Não localizado
venda('LOJA ZZ GLORIA', status=Fibra.STATUS_CANCELADO, planilha=False)  # cancelada fica na coluna dela
venda('LOJA ZZ MONTS', ordem_v='ORDZZ777')
venda('LOJA ZZ MONTS', cpf='12.345.678/0001-95')                          # CNPJ → B2B
venda('LOJA ZZ GLORIA', dia=HOJE.replace(day=1) - timedelta(days=40))    # fora do mês
for f in Fibra.objects.all()[:25]:
    FibraIncidente.objects.create(fibra=f, aberto_por=rede, observacao='zz')
FibraIncidente.objects.create(fibra=Fibra.objects.get(numero_da_venda='ZZQ0001'), aberto_por=ana, observacao='minha')

cr, ca, cb = Client(), Client(), Client()
cr.force_login(rede); ca.force_login(ana); cb.force_login(bia)


def ids(resp, chave):
    if chave == 'colunas':
        return sorted(f.pk for col in resp.context['colunas'] for f in col['items'])
    return sorted(f.pk for f in resp.context['fibras'])


print('\n== KANBAN × LISTA ==')
for rotulo, q in [('mês', {}), ('todos', {'periodo': 'todos'}), ('b2b', {'b2b': '1', 'periodo': 'todos'}),
                  ('ordem vinculada', {'ordem': 'ORDZZ777'})]:
    k, l = cr.get('/fibras/', q), cr.get('/fibras/lista/', q)
    kc = {c['key']: c['count'] for c in k.context['colunas']}
    lc = {key: n for key, _l, n in l.context['situacoes']}
    t(f'{rotulo}: mesmas vendas, contagens e totais', ids(k, 'colunas') == ids(l, 'fibras') and kc == lc
      and sum(c['total'] for c in k.context['colunas']) == l.context['total_lista'], (kc, lc))
k = cr.get('/fibras/', {}).context
cols = {c['key']: c for c in k['colunas']}
t('pendentes fora da planilha vão para "Não localizado" e não se repetem', cols['nao_localizado']['count'] == 6
  and len(ids(cr.get('/fibras/', {}), 'colunas')) == len(set(ids(cr.get('/fibras/', {}), 'colunas'))))
t('cancelada fora da planilha fica em Cancelado', cols['cancelado']['count'] == 1)
t('lista abre no mês, como o kanban (antes abria em tudo)', len(cr.get('/fibras/lista/').context['fibras']) == 29)
l = cr.get('/fibras/lista/', {'status': 'nao_localizado'}).context
t('lista filtra pela coluna do kanban', len(l['fibras']) == 6 and all(f.coluna == 'nao_localizado' for f in l['fibras']))
t('B2B pelos dígitos: CPF formatado não é empresa, CNPJ é',
  [f.cpf for c in cr.get('/fibras/', {'b2b': '1'}).context['colunas'] for f in c['items']] == ['12.345.678/0001-95'])
h = cr.get('/fibras/', {'pdv': 'GLORIA'}).content.decode()
t('Kanban → Lista leva os filtros', 'href="/fibras/lista/?pdv=GLORIA"' in h)

print('\n== CONSULTAS E CACHE ==')
quadro.invalidar()
with CaptureQueriesContext(connection) as q1:
    cr.get('/fibras/', {'periodo': 'todos'})
fib1 = [x for x in q1.captured_queries if 'fibras_fibra' in x['sql'] or 'fibras_fibraincidente' in x['sql']]
for i in range(40):
    f = venda('LOJA ZZ GLORIA')
    FibraIncidente.objects.create(fibra=f, aberto_por=rede, observacao='zz')
with CaptureQueriesContext(connection) as q2:
    cr.get('/fibras/', {'periodo': 'todos'})
fib2 = [x for x in q2.captured_queries if 'fibras_fibra' in x['sql'] or 'fibras_fibraincidente' in x['sql']]
n_cartoes = sum(c['count'] for c in cr.get('/fibras/', {'periodo': 'todos'}).context['colunas'])
t('as 40 vendas novas aparecem (o cache caiu com as gravações)', n_cartoes == Fibra.objects.count(), (n_cartoes, Fibra.objects.count()))
print('   consultas de vendas/incidentes: antes', len(fib1), '| depois de +40 vendas', len(fib2), [x['sql'][:70] for x in fib2])
with CaptureQueriesContext(connection) as q3:
    cr.get('/fibras/', {'periodo': 'todos'})
fib3 = [x for x in q3.captured_queries if 'fibras_fibra"' in x['sql'] or 'fibras_fibraincidente' in x['sql']]
t('segunda abertura sai do cache (nenhuma consulta de vendas)', len(fib3) == 0, [x['sql'][:80] for x in fib3])
alvo = Fibra.objects.filter(status=Fibra.STATUS_AGENDADO).first()
cr.post(f'/fibras/{alvo.pk}/status/ajax/', {'status': Fibra.STATUS_INSTALADO, 'retorno': 'zz'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
alvo.refresh_from_db()
cols = {c['key']: [f.pk for f in c['items']] for c in cr.get('/fibras/', {'periodo': 'todos'}).context['colunas']}
t('mudar o status derruba o cache (o cartão já aparece na coluna nova)', alvo.status == Fibra.STATUS_INSTALADO
  and alvo.pk in cols['instalado'] and alvo.pk not in cols['agendado'])
Fibra.objects.filter(pk=alvo.pk).update(cliente='ZZ EM MASSA')
from fibras.services import sync_fibras
from unittest import mock as _m
with _m.patch('fibras.services.fetch_fibras_from_mysql', return_value=[]):
    try:
        sync_fibras()
    except Exception:
        pass
nomes = {f.cliente for c in cr.get('/fibras/', {'periodo': 'todos'}).context['colunas'] for f in c['items']}
t('sincronização (em massa, sem sinal) também derruba o cache', 'ZZ EM MASSA' in nomes)

print('\n== RECORTE DE CADA UM ==')
la = {f.pdv for c in ca.get('/fibras/', {'periodo': 'todos'}).context['colunas'] for f in c['items']}
lb = {f.pdv for c in cb.get('/fibras/', {'periodo': 'todos'}).context['colunas'] for f in c['items']}
t('Ana (Glória) e Bia (Monts) não recebem o quadro uma da outra pelo cache', la == {'LOJA ZZ GLORIA'} and lb == {'LOJA ZZ MONTS'}, (la, lb))
nova = [f.pk for c in ca.get('/fibras/', {'periodo': 'todos'}).context['colunas'] for f in c['items'] if f.nova_resposta]
outra = [f.pk for c in cr.get('/fibras/', {'periodo': 'todos'}).context['colunas'] for f in c['items'] if f.nova_resposta]
t('"resposta nova" calculada por pessoa (fora do cache)', isinstance(nova, list) and isinstance(outra, list))

print('\n== TELAS ==')
h = cr.get('/fibras/lista/').content.decode()
t('a lista não é embrulhada na rolagem lateral (cabe no celular)', 'id="fbTabela" data-rc-wrapped="1"' in h)
h = cr.get('/fibras/').content.decode()
t('kanban: coluna acompanha a altura da tela', 'calc(100vh - 230px)' in h and 'calc(100dvh - 190px)' in h)
import re as _re
for p in ['templates/fibras/kanban.html', 'templates/fibras/lista.html']:
    ruins = [m for m in _re.finditer(r'\{#(.*?)#\}', open(p).read(), _re.S) if '\n' in m.group(1)]
    t(f'{p}: nenhum comentário {{# #}} de várias linhas', not ruins)

print(f'\n{OK} OK, {FALHAS} falha(s)')
