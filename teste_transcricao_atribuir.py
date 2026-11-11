"""Agenda › transcrição: responsável das Tarefas Criadas — qualquer usuário ativo, por quem enxerga a transcrição (11/11/2026).

Banco SQLite descartável; nada sai.
"""
import os
import pathlib
import re
import sys
import tempfile

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-transc-atrib-'))
(PASTA / 'zz_transc_atrib_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-transc-at'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-transc-at-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_transc_atrib_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import json  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402

from agenda.models import MeetingTranscription  # noqa: E402
from core.models import TaskActivity  # noqa: E402
from users.models import Sector  # noqa: E402

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


setor = Sector.objects.create(name='Loja ZZ Atribuição')


def pessoa(login, nome, **kw):
    primeiro, ultimo = nome.split(' ', 1)
    return User.objects.create_user(username=login, email=f'{login}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=primeiro, last_name=ultimo, sector=setor, **kw)


dono = pessoa('zz.at.dono', 'Dora Dona', hierarchy='PADRAO')
compart = pessoa('zz.at.comp', 'Caio Compartilhado', hierarchy='PADRAO')
estranho = pessoa('zz.at.estranho', 'Eva Estranha', hierarchy='PADRAO')
superadm = pessoa('zz.at.super', 'Sara Super', hierarchy='SUPERADMIN')
alvo = pessoa('zz.at.alvo', 'Álvaro Joaquim', hierarchy='PADRAO')
inativo = pessoa('zz.at.inativo', 'Ivo Inativo', hierarchy='PADRAO', is_active=False)

reuniao = MeetingTranscription.objects.create(owner=dono, title='ZZ Reunião', status='completed')
reuniao.shared_with.add(compart)
tarefa = TaskActivity.objects.create(title='ZZ Fazer a planilha', description='x', assigned_to=dono, created_by=dono)
reuniao.tasks_created.add(tarefa)
url = f'/agenda/api/transcricoes/{reuniao.pk}/tasks/{tarefa.pk}/assign/'


def cliente(u):
    c = Client()
    c.force_login(u)
    return c


def atribuir(u, user_id):
    return cliente(u).post(url, json.dumps({'user_id': user_id}), content_type='application/json')


print('== TELA ==')
for nome, u in (('dono', dono), ('compartilhado', compart), ('SUPERADMIN', superadm)):
    html = cliente(u).get(f'/agenda/transcricoes/{reuniao.pk}/').content.decode()
    t(f'{nome}: busca de pessoa nas Tarefas Criadas', 'data-busca-pessoa' in html and 'id="pessoasTarefa"' in html
      and 'lvaro Joaquim' in html and 'Responsável:' in html)
    t(f'{nome}: inativo fora da lista', 'Ivo Inativo' not in html)
    t(f'{nome}: setor junto do nome', '"setor": "Loja ZZ Atribui' in html)
r = cliente(estranho).get(f'/agenda/transcricoes/{reuniao.pk}/')
t('quem não enxerga a transcrição não abre', r.status_code == 404, r.status_code)

print('== ATRIBUIR ==')
r = atribuir(compart, alvo.pk)
tarefa.refresh_from_db()
t('compartilhado atribui a qualquer usuário', r.status_code == 200 and tarefa.assigned_to == alvo
  and r.json()['assigned_to'] == 'Álvaro Joaquim', r.content[:200])
r = atribuir(superadm, superadm.pk)
tarefa.refresh_from_db()
t('SUPERADMIN atribui (inclusive a si)', r.status_code == 200 and tarefa.assigned_to == superadm)
r = atribuir(dono, compart.pk)
tarefa.refresh_from_db()
t('dono continua atribuindo', r.status_code == 200 and tarefa.assigned_to == compart)
r = atribuir(estranho, estranho.pk)
tarefa.refresh_from_db()
t('quem não enxerga não atribui', r.status_code == 404 and tarefa.assigned_to == compart, r.status_code)
r = atribuir(dono, inativo.pk)
tarefa.refresh_from_db()
t('usuário inativo recusado', r.status_code == 404 and tarefa.assigned_to == compart)
for ruim in (None, '', 'abc', 999999):
    r = atribuir(dono, ruim)
    t(f'user_id {ruim!r} recusado', r.status_code in (400, 404))
r = cliente(dono).post(url, '[1]', content_type='application/json')
t('corpo que não é objeto recusado', r.status_code == 400)
html = cliente(dono).get(f'/agenda/transcricoes/{reuniao.pk}/').content.decode()
t('tela mostra o responsável atual', 'data-responsavel>Caio Compartilhado<' in html)

print('== JS E TEMPLATE ==')
import re as _re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
blocos = _re.findall(r'<script>(.*?)</script>', html, _re.S)
node = shutil.which('node')
if node:
    falhas = []
    for b in blocos:
        p = PASTA / 'js.js'
        p.write_text(b)
        r = subprocess.run([node, '--check', str(p)], capture_output=True, text=True)
        if r.returncode:
            falhas.append(r.stderr[:300])
    t('JS da tela com sintaxe válida', not falhas, falhas)
t('nenhum comentário vazando', '{#' not in html and '#}' not in html)

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
