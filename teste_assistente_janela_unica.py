"""Janela do canto: Orientação e Claude numa conversa só (/assistente/perguntar/).

Rodar: venv/bin/python teste_assistente_janela_unica.py
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco descartável; o Claude e a busca são dublês (nada sai).
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-janela-unica-'))
(PASTA / 'zz_janela_unica_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-janela'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-janela-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_janela_unica_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import json
import types

sys.modules.setdefault('anthropic', types.SimpleNamespace(Anthropic=mock.MagicMock()))
from django.contrib.auth import get_user_model

from assistente import claude_client, colaborador
from assistente.models import AssistenteMensagem, ChaveClaudeEmpresa

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


ana = User.objects.create_user(username='zz.ju.ana', email='zz.ju.ana@exemplo-teste.local', password='x',
                               first_name='ZZ', last_name='Ana', hierarchy='PADRAO')
c = Client(); c.force_login(ana)
URL = '/assistente/perguntar/'


def perguntar(texto, historico=None):
    r = c.post(URL, data=json.dumps({'mensagem': texto, 'historico': historico or []}), content_type='application/json')
    return r.status_code, r.json()


ORIENTACAO = {'ia': True, 'resposta': 'Passo a passo da orientação.', 'fontes': [{'tipo': 'pop', 'url': '/pops/1/', 'titulo': 'POP'}],
              'paginas': [], 'chamado': {'url': '/tickets/create/?x=1', 'rotulo': 'Abrir chamado'}}
TELA = {'fontes': [{'tipo': 'pop', 'rotulo': 'POP', 'titulo': 'Sangria', 'url': '/pops/7/', 'resumo': ''}],
        'paginas': [{'tipo': 'pagina', 'rotulo': 'Página', 'titulo': 'Caixa', 'url': '/contagem-caixa/', 'resumo': ''}],
        'chamado': {'url': '/tickets/create/?t=sangria', 'rotulo': 'Abrir chamado'}}

print('\n== A JANELA ==')
h = c.get('/').content.decode() if c.get('/').status_code == 200 else ''
import re as _re
w = open('templates/assistente/_widget.html').read()
t('sem as abas Orientação/Claude e com o endereço único', 'rcaModos' not in w and "data-url-unica=\"{% url 'assistente:perguntar' %}\"" in w)
js = open('static/js/rc-assistente.js').read()
t('o script manda tudo para o endereço único', 'raiz.dataset.urlUnica' in js and 'urlClaude' not in js and 'data-modo' not in js)

print('\n== SEM CLAUDE: ORIENTAÇÃO ==')
with mock.patch.object(colaborador, 'orientar', return_value=dict(ORIENTACAO)) as ori, \
        mock.patch.object(claude_client, 'responder') as resp:
    st, j = perguntar('Como faço uma sangria?')
t('quem não tem Claude recebe a orientação', st == 200 and j['claude'] is False and j['resposta'] == 'Passo a passo da orientação.'
  and ori.called and not resp.called, j)

print('\n== COM CLAUDE (CHAVE DA EMPRESA) ==')
emp = ChaveClaudeEmpresa.get(); emp.set_api_key('sk-ant-empresa-zz'); emp.save()
with mock.patch.object(colaborador, 'material_para_claude', return_value=('\n\n---\nMATERIAL DO PORTAL: POP Sangria', dict(TELA))) as mat, \
        mock.patch.object(claude_client, 'responder', return_value='Resposta do Claude com o POP.') as resp, \
        mock.patch.object(colaborador, 'orientar') as ori:
    st, j = perguntar('Como faço uma sangria?', [{'papel': 'user', 'texto': 'oi'}, {'papel': 'assistant', 'texto': 'Olá!'}])
args = resp.call_args[0] if resp.called else ()
t('a resposta é do Claude, com os cartões da orientação', st == 200 and j['claude'] is True
  and j['resposta'] == 'Resposta do Claude com o POP.' and j['fontes'][0]['titulo'] == 'Sangria'
  and j['paginas'][0]['url'] == '/contagem-caixa/' and j['chamado']['url'].startswith('/tickets/create/') and not ori.called, j)
t('o Claude recebe a pergunta com o material da busca e a conversa da janela',
  args and args[1].startswith('Como faço uma sangria?') and 'MATERIAL DO PORTAL' in args[1]
  and args[2] == [{'role': 'user', 'content': 'oi'}, {'role': 'assistant', 'content': 'Olá!'}], args)
msgs = list(AssistenteMensagem.objects.filter(user=ana).order_by('criado_em').values_list('papel', 'conteudo'))
t('a conversa vai para a página do assistente, sem o material colado', msgs == [('user', 'Como faço uma sangria?'),
                                                                              ('assistant', 'Resposta do Claude com o POP.')], msgs)

with mock.patch.object(colaborador, 'material_para_claude', side_effect=RuntimeError('MinIO fora')), \
        mock.patch.object(claude_client, 'responder', return_value='Sem material, mas respondi.') as resp:
    st, j = perguntar('Qual meu saldo de banco de horas?')
t('busca da orientação quebrada não impede o Claude', st == 200 and j['claude'] and j['resposta'] == 'Sem material, mas respondi.'
  and j['fontes'] == [] and resp.call_args[0][1] == 'Qual meu saldo de banco de horas?', j)

with mock.patch.object(colaborador, 'material_para_claude', return_value=('', dict(TELA))), \
        mock.patch.object(claude_client, 'responder', side_effect=RuntimeError('Limite de uso')), \
        mock.patch.object(colaborador, 'orientar', return_value=dict(ORIENTACAO)):
    st, j = perguntar('Como faço uma sangria?')
t('Claude fora do ar: cai na orientação, avisando', st == 200 and j['claude'] is False
  and j['resposta'].startswith('O Claude não respondeu agora') and 'Passo a passo da orientação.' in j['resposta'], j)

with mock.patch.object(colaborador, 'material_para_claude', return_value=('', dict(TELA))), \
        mock.patch.object(claude_client, 'responder', side_effect=claude_client.NaoConectado('sem chave')), \
        mock.patch.object(colaborador, 'orientar', return_value=dict(ORIENTACAO)):
    st, j = perguntar('Como faço uma sangria?')
t('chave inválida/sem chave no meio do caminho: orientação', st == 200 and j['claude'] is False)

st, j = perguntar('')
t('pergunta vazia é recusada', st == 400 and j['erro'])
r = c.get(URL)
t('só POST', r.status_code == 405)

print('\n== MATERIAL DE VERDADE (BANCO VAZIO) ==')
from django.test import RequestFactory
req = RequestFactory().post(URL); req.user = ana
bloco, tela = colaborador.material_para_claude(req, 'como faço uma sangria no caixa', [])
t('sem nada achado: bloco vazio e tela com as listas vazias (sem quebrar)', bloco == '' and tela['fontes'] == []
  and tela['paginas'] == [] or bloco.startswith('\n\n---\nMATERIAL'), (bloco[:80], tela))

print(f'\n{OK} OK, {FALHAS} falha(s)')
