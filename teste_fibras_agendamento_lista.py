"""/fibras/lista/: Agendamento editável na própria linha (17/11/2026).

Quem tem "Mudar status" edita e grava (e fica no histórico da venda); quem só vê recebe 403;
data inválida é recusada; venda fora do recorte é 404. Sqlite descartável; nada sai para a rede.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: a tabela das entregas e a coluna
# `exige_entrega` só existem no Postgres compartilhado depois do migrate, e o
# migrate é dele. O schema aqui sai direto dos models (`--run-syncdb`), como em
# qualquer outra sessão — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-fibras-agenda-'))
(PASTA / 'zz_fibras_agenda_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fba'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fba-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_fibras_agenda_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from users.models import Sector

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


from unittest import mock as _mock

import requests
from django.utils import timezone

from fibras.models import AcessoFibras, Fibra, HistoricoAcessoFibras
from fibras.permissoes import completar


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


_mock.patch.object(requests, 'post', sem_rede).start()
_mock.patch.object(requests, 'get', sem_rede).start()

loja = Sector.objects.create(name='ZZ LOJA FIBRA')
outra = Sector.objects.create(name='ZZ OUTRA LOJA')
chefe = User.objects.create_user(username='zzf.super', email='zzf.super@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZF', last_name='Super', sector=loja, hierarchy='SUPERADMIN')
vend = User.objects.create_user(username='zzf.vend', email='zzf.vend@exemplo-teste.local', password='S3nha!teste',
                                first_name='Zé', last_name='Vendedor', sector=loja, hierarchy='PADRAO')
sup = User.objects.create_user(username='zzf.sup', email='zzf.sup@exemplo-teste.local', password='S3nha!teste',
                               first_name='ZZF', last_name='Supervisor', sector=outra, hierarchy='SUPERVISOR')


def fibra(venda, pdv, vendedor):
    return Fibra.objects.create(numero_da_venda=venda, numero_protocolo='71' + venda[-4:], pdv=pdv, vendedor=vendedor,
                                data_da_venda=timezone.localdate(), cliente=f'Cliente {venda}', pilar='FIXA',
                                valor=100, status=Fibra.STATUS_PENDENTE)


f_dele = fibra('ZZF0001', 'ZZ LOJA FIBRA', 'ZE VENDEDOR')          # sem acento/caixa: é o Zé
f_loja = fibra('ZZF0002', 'ZZ LOJA FIBRA', 'OUTRA PESSOA')
f_fora = fibra('ZZF0003', 'ZZ OUTRA LOJA', 'MAIS ALGUEM')

cs, cv, cp = Client(), Client(), Client()
cs.force_login(chefe)
cv.force_login(vend)
cp.force_login(sup)
MENU = 'href="/fibras/" data-rota="/fibras/"'


from fibras.models import FibraStatusHistory
URL = lambda pk: f'/fibras/{pk}/agendamento/ajax/'  # noqa: E731
AJ = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}

cs.post('/fibras/acessos/', {'user': vend.pk, 'permissoes': ['loja.ver'], 'ativo': 'on'})
print('Só vê (sem Mudar status)')
r = cv.post(URL(f_dele.pk), {'data_agendamento': '2026-12-01'}, **AJ)
t('403 em JSON', r.status_code == 403 and r.json()['ok'] is False, r.status_code)
f_dele.refresh_from_db()
t('não gravou', f_dele.data_agendamento is None)
h = cv.get('/fibras/lista/?periodo=todos').content.decode()
t('lista mostra o texto, sem input de data', 'class="fb-ag-input"' not in h and 'ZZF0001' in h)
t('anônimo vai para o login', Client().post(URL(f_dele.pk), {'data_agendamento': '2026-12-01'}).status_code == 302)

print('Com Mudar status')
cs.post('/fibras/acessos/', {'user': vend.pk, 'permissoes': ['loja.ver', 'status.alterar'], 'ativo': 'on'})
h = cv.get('/fibras/lista/?periodo=todos').content.decode()
t('lista traz o input de data', 'class="fb-ag-input"' in h and 'fbUrlAgenda' in h)
r = cv.post(URL(f_loja.pk), {'data_agendamento': '2026-12-01'}, **AJ)
f_loja.refresh_from_db()
t('grava e devolve formatado', r.status_code == 200 and r.json()['data_agendamento_fmt'] == '01/12/2026'
  and r.json()['data_agendamento'] == '2026-12-01' and str(f_loja.data_agendamento) == '2026-12-01', r.content[:200])
hist = FibraStatusHistory.objects.filter(fibra=f_loja, alterado_por=vend).first()
t('registra no histórico', hist and 'sem data para 01/12/2026' in hist.retorno and hist.status_anterior == hist.status_novo)
h = cv.get('/fibras/lista/?periodo=todos').content.decode()
t('lista mostra o valor gravado', 'value="2026-12-01"' in h)
n = FibraStatusHistory.objects.count()
cv.post(URL(f_loja.pk), {'data_agendamento': '2026-12-01'}, **AJ)
t('mesmo valor não duplica histórico', FibraStatusHistory.objects.count() == n)
r = cv.post(URL(f_loja.pk), {'data_agendamento': ''}, **AJ)
f_loja.refresh_from_db()
t('vazio limpa', r.status_code == 200 and f_loja.data_agendamento is None and r.json()['data_agendamento'] == '')
for ruim in ('31/12/2026', 'abc', '2026-02-30', '0001-01-01', '9999-12-31'):
    r = cv.post(URL(f_loja.pk), {'data_agendamento': ruim}, **AJ)
    f_loja.refresh_from_db()
    t(f'recusa {ruim!r}', r.status_code == 400 and r.json()['ok'] is False and f_loja.data_agendamento is None, r.status_code)
r = cv.post(URL(f_fora.pk), {'data_agendamento': '2026-12-01'}, **AJ)
t('venda fora do recorte: 404', r.status_code == 404, r.status_code)
t('GET não vale', cv.get(URL(f_loja.pk)).status_code == 405)
r = cs.post(URL(f_fora.pk), {'data_agendamento': '2026-12-02'}, **AJ)
t('SUPERADMIN edita qualquer venda', r.status_code == 200)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
