"""Chave do Claude da empresa (/users/settings/): o SUPERADMIN configura e todos usam.

Rodar: venv/bin/python teste_assistente_chave_empresa.py
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco descartável; a Anthropic é um dublê (nenhuma chamada sai).
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-chave-claude-'))
(PASTA / 'zz_chave_claude_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-chave-claude'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-chave-claude-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_chave_claude_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from django.contrib.auth import get_user_model

import types
Anth = mock.MagicMock()
sys.modules.setdefault('anthropic', types.SimpleNamespace(Anthropic=Anth))
from assistente import claude_client
from assistente.models import AssistenteConexao, ChaveClaudeEmpresa

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


chefe = User.objects.create_user(username='zz.cc.chefe', email='zz.cc.chefe@exemplo-teste.local', password='x',
                                 first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN')
ana = User.objects.create_user(username='zz.cc.ana', email='zz.cc.ana@exemplo-teste.local', password='x',
                               first_name='ZZ', last_name='Ana', hierarchy='PADRAO')
bia = User.objects.create_user(username='zz.cc.bia', email='zz.cc.bia@exemplo-teste.local', password='x',
                               first_name='ZZ', last_name='Bia', hierarchy='PADRAO')
cc, ca, cb = Client(), Client(), Client()
cc.force_login(chefe); ca.force_login(ana); cb.force_login(bia)
conx_bia = claude_client.conexao(bia)
conx_bia.set_api_key('sk-ant-pessoal-da-bia-9999'); conx_bia.save()

print('\n== ANTES ==')
t('sem chave da empresa, quem não tem chave não usa', not claude_client.pode_usar(ana))
t('janelinha: Ana sem modo Claude', ca.get('/assistente/colaborador/estado/').json()['claude'] is False)
h = ca.get('/users/settings/').content.decode()
t('Ana não vê o bloco da empresa e vê o "Conectar Claude"', 'Chave da empresa' not in h and 'Conectar Claude' in h)
h = cc.get('/users/settings/').content.decode()
t('SUPERADMIN vê o bloco da chave da empresa', 'Chave da empresa' in h and 'Testar e salvar para todos' in h)

print('\n== SALVAR ==')
with mock.patch.object(claude_client, 'testar_chave', return_value=(True, 'ok')) as teste:
    ca.post('/assistente/empresa/salvar/', {'api_key': 'sk-ant-intrusa-0000', 'modelo': 'claude-haiku-5-5'})
t('quem não é SUPERADMIN não salva', not ChaveClaudeEmpresa.get().configurada and not teste.called)
with mock.patch.object(claude_client, 'testar_chave', return_value=(False, 'Chave inválida')):
    r = cc.post('/assistente/empresa/salvar/', {'api_key': 'sk-ant-errada', 'modelo': 'claude-haiku-5-5'}, follow=True)
t('chave que não passa no teste não é salva', not ChaveClaudeEmpresa.get().configurada and 'Chave inválida' in r.content.decode())
with mock.patch.object(claude_client, 'testar_chave', return_value=(True, 'ok')) as teste:
    cc.post('/assistente/empresa/salvar/', {'api_key': 'sk-ant-empresa-1234', 'modelo': 'claude-sonnet-5-5'})
emp = ChaveClaudeEmpresa.get()
t('salva cifrada, com o final e o modelo', emp.configurada and 'sk-ant-empresa-1234' not in emp.api_key_cifrada
  and emp.get_api_key() == 'sk-ant-empresa-1234' and emp.key_hint == '…1234' and emp.modelo == 'claude-sonnet-5-5'
  and emp.definida_por == chefe and teste.call_args[0] == ('sk-ant-empresa-1234', 'claude-sonnet-5-5'))

print('\n== TODOS USAM ==')
t('Ana passa a usar', claude_client.pode_usar(ana))
estado = ca.get('/assistente/colaborador/estado/').json()
t('janelinha: Ana com modo Claude e link da página', estado['claude'] is True and estado['chave_empresa'] is True
  and estado['pagina_claude'] == '/assistente/', estado)
t('a página do Claude abre para a Ana (não cai no modo colaborador)', 'chat-input' in ca.get('/assistente/').content.decode())
h = ca.get('/users/settings/').content.decode()
t('Ana vê "chave da empresa" e não o formulário de conectar', 'chave da empresa' in h and 'Conectar Claude' not in h
  and 'Chave da empresa <span' not in h and 'sk-ant-empresa' not in h)
Anth.reset_mock()
_c, modelo_bia = claude_client._cliente(bia)
t('Bia (com chave pessoal) usa a da empresa, com o modelo da empresa',
  Anth.call_args.kwargs.get('api_key') == 'sk-ant-empresa-1234' and modelo_bia == 'claude-sonnet-5-5', Anth.call_args)
with mock.patch.object(claude_client, 'responder', return_value='Olá, Ana!') as resp:
    r = ca.post('/assistente/enviar/', data='{"mensagem": "oi"}', content_type='application/json')
t('Ana conversa pelo /assistente/enviar/', r.status_code == 200 and r.json()['resposta'] == 'Olá, Ana!' and resp.called, r.content[:200])
h = cb.get('/users/settings/').content.decode()
t('Bia é avisada que a chave pessoal fica guardada sem uso', 'não é usada enquanto a da empresa' in h and '…9999' in h)

print('\n== MODELO, TESTE, REMOVER ==')
cc.post('/assistente/empresa/salvar/', {'api_key': '', 'modelo': 'claude-haiku-5-5'})
emp = ChaveClaudeEmpresa.get()
t('em branco só troca o modelo, mantém a chave', emp.modelo == 'claude-haiku-5-5' and emp.get_api_key() == 'sk-ant-empresa-1234')
with mock.patch.object(claude_client, 'testar_chave', return_value=(True, 'Conexão com o Claude OK.')) as teste:
    cc.post('/assistente/empresa/testar/')
t('testar usa a chave da empresa', teste.call_args[0] == ('sk-ant-empresa-1234', 'claude-haiku-5-5')
  and ChaveClaudeEmpresa.get().ultimo_ok_em is not None)
ca.post('/assistente/empresa/remover/')
t('quem não é SUPERADMIN não remove', ChaveClaudeEmpresa.get().configurada)
cc.post('/assistente/empresa/remover/')
t('SUPERADMIN remove', not ChaveClaudeEmpresa.get().configurada)
t('sem a da empresa, Ana volta a não usar e Bia volta à dela', not claude_client.pode_usar(ana) and claude_client.pode_usar(bia))
Anth.reset_mock()
claude_client._cliente(bia)
t('Bia usa a chave pessoal de novo', Anth.call_args.kwargs.get('api_key') == 'sk-ant-pessoal-da-bia-9999')
r = ca.post('/assistente/enviar/', data='{"mensagem": "oi"}', content_type='application/json')
t('Ana sem chave: precisa conectar', r.status_code == 400 and r.json().get('precisa_conectar'))

import re as _re
ruins = [m for m in _re.finditer(r'\{#(.*?)#\}', open('templates/users/settings.html').read(), _re.S) if '\n' in m.group(1)]
t('nenhum comentário {# #} de várias linhas na tela de configurações', not ruins)

print(f'\n{OK} OK, {FALHAS} falha(s)')
