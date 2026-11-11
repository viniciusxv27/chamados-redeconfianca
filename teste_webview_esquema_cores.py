"""App WebView Android: a página declara o esquema de cores (11/11/2026).

Sem isto, com o celular em modo escuro, o WebView escurecia à força a página clara (tela acinzentada).
Rodar: venv/bin/python teste_webview_esquema_cores.py
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco descartável.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-webview-cores-'))
(PASTA / 'zz_webview_cores_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-wvcores'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-wvcores-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_webview_cores_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import glob
import re

from django.contrib.auth import get_user_model

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


UA = 'Mozilla/5.0 (Linux; Android 14; SM-A546E; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0 Mobile Safari/537.36'
claro = User.objects.create_user(username='zz.wv.claro', email='zz.wv.claro@exemplo-teste.local', password='x', first_name='ZZ', last_name='Claro')
escuro = User.objects.create_user(username='zz.wv.escuro', email='zz.wv.escuro@exemplo-teste.local', password='x', first_name='ZZ', last_name='Escuro')
escuro.theme = 'dark'; escuro.save()

anon = Client(HTTP_USER_AGENT=UA).get('/login/').content.decode()
t('login (sem sessão): página clara, sem escurecimento automático', '<meta name="color-scheme" content="only light">' in anon)
c = Client(HTTP_USER_AGENT=UA); c.force_login(claro)
h = c.get('/tickets/', follow=True).content.decode()
t('tema claro: "only light" (o WebView não escurece à força)', '<meta name="color-scheme" content="only light">' in h
  and ':root { color-scheme: only light; }' in h)
c = Client(HTTP_USER_AGENT=UA); c.force_login(escuro)
h = c.get('/tickets/', follow=True).content.decode()
t('tema escuro do portal: declara dark', '<meta name="color-scheme" content="dark">' in h and 'class="dark"' in h)
sem = []
for p in glob.glob('templates/**/*.html', recursive=True):
    s = open(p).read()
    if p.startswith('templates/emails/') or '{% extends' in s or not re.search(r'<head[ >]', s):
        continue
    if 'name="color-scheme"' not in s:
        sem.append(p)
t('toda página avulsa (fora o layout e os e-mails) declara o esquema de cores', not sem, sem)
ruins = [m for m in re.finditer(r'\{#(.*?)#\}', open('templates/base.html').read(), re.S) if '\n' in m.group(1)]
t('base.html sem comentário {# #} de várias linhas', not ruins)

print(f'\n{OK} OK, {FALHAS} falha(s)')
