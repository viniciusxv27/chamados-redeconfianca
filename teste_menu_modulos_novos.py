"""Menu e acesso dos módulos novos (Contas a Pagar e Auditoria D-1): só SUPERADMIN e quem ele liberar (11/11/2026).

Banco SQLite descartável; nada sai.
"""
import os
import pathlib
import re
import sys
import tempfile

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-menu-novos-'))
(PASTA / 'zz_menu_novos_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-menu-nv'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-menu-nv-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_menu_novos_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model  # noqa: E402

from auditoria_d1.models import AcessoAuditoriaD1  # noqa: E402
from contas_pagar.models import AcessoContasPagar  # noqa: E402
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


setor = Sector.objects.create(name='Loja ZZ Menu')


def pessoa(login, hierarquia):
    u = User.objects.create_user(username=login, email=f'{login}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZ', last_name=login, hierarchy=hierarquia, sector=setor)
    c = Client()
    c.force_login(u)
    return u, c


def menu(c):
    h = c.get('/rotina-adm/', follow=True).content.decode()
    return ('href="/contas-a-pagar/"' in h, 'href="/auditoria-d1/"' in h)


_, c_super = pessoa('zz.menu.super', 'SUPERADMIN')
_, c_admin = pessoa('zz.menu.admin', 'ADMIN')
cp_user, c_cp = pessoa('zz.menu.cp', 'PADRAO')
d1_user, c_d1 = pessoa('zz.menu.d1', 'PADRAO')
AcessoContasPagar.objects.create(user=cp_user, papel='CONSULTA')
AcessoAuditoriaD1.objects.create(user=d1_user, papel='GESTOR')

t('SUPERADMIN vê os dois itens no menu', menu(c_super) == (True, True), menu(c_super))
t('ADMIN sem liberação não vê nenhum', menu(c_admin) == (False, False), menu(c_admin))
t('liberado no Contas a Pagar vê só ele', menu(c_cp) == (True, False), menu(c_cp))
t('liberado na Auditoria D-1 vê só ela', menu(c_d1) == (False, True), menu(c_d1))
t('ADMIN sem liberação é barrado nas duas telas', c_admin.get('/contas-a-pagar/').status_code == 302
  and c_admin.get('/auditoria-d1/').status_code == 302)
t('só o SUPERADMIN abre as listas de acesso', c_super.get('/contas-a-pagar/acessos/').status_code == 200
  and c_super.get('/auditoria-d1/acessos/').status_code == 200
  and c_cp.get('/contas-a-pagar/acessos/').status_code == 302 and c_d1.get('/auditoria-d1/acessos/').status_code == 302)

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
