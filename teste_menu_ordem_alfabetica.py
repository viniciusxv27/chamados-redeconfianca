"""Menu lateral em ordem alfabética (12/11/2026): itens de cada grupo, grupos e seções, para SUPERADMIN, ADMIN e PADRÃO.

Banco SQLite descartável; nada sai.
"""
import os
import pathlib
import re
import sys
import tempfile

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-menu-alfa-'))
(PASTA / 'zz_menu_alfa_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-menu-alfa'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-menu-alfa-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_menu_alfa_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import re  # noqa: E402
import unicodedata  # noqa: E402
from collections import Counter  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402

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


def norm(x):
    """Chave pt-BR: sem acento e sem diferença de maiúscula."""
    x = unicodedata.normalize('NFKD', x)
    return ''.join(c for c in x if not unicodedata.combining(c)).casefold()


def em_ordem(rotulos):
    return rotulos == sorted(rotulos, key=norm)


def menu_lateral(c):
    html = c.get('/users/profile/').content.decode()
    return html[html.index('id="sidebar"'):html.index('</aside>')]


def grupo(menu, ident):
    """Rótulos dos itens de um submenu, na ordem da tela ([] se o grupo não veio)."""
    if f'id="{ident}"' not in menu:
        return []
    i = menu.index(f'id="{ident}"')
    f = menu.index('</ul>', i)
    return [r.strip() for r in re.findall(r'<span(?: class="text-sm[^"]*")?>([^<{]+)</span>', menu[i:f])]


SUBMENUS = ('colaborador-menu', 'tickets-menu', 'financeiro-menu', 'gestao-menu', 'management-menu')
setor = Sector.objects.create(name='Loja ZZ Menu Alfa')


def pessoa(login, hierarquia, **kw):
    u = User.objects.create_user(username=login, email=f'{login}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZ', last_name=login, hierarchy=hierarquia, sector=setor, **kw)
    c = Client()
    c.force_login(u)
    return c


c_super = pessoa('zz.alfa.super', 'SUPERADMIN', is_superuser=True, is_staff=True)
c_admin = pessoa('zz.alfa.admin', 'ADMIN')
c_padrao = pessoa('zz.alfa.padrao', 'PADRAO')

print('SUPERADMIN')
m = menu_lateral(c_super)
topo = re.findall(r'<span class="text-sm sidebar-text">([^<]+)</span>', m[:m.index('Área do Colaborador')])
t('Home e Power BI continuam no topo', topo == ['Home', 'Power BI'], topo)
secoes = re.findall(r'tracking-wider text-gray-600 sidebar-text">([^<]+)</span>', m)
t('seções na ordem de sempre', secoes == ['Área do Colaborador', 'Operação', 'Configurações'], secoes)
grupos = re.findall(r'<span class="text-sm flex-1 sidebar-text">([^<]+)</span>', m)
t('grupos de Operação em ordem alfabética', grupos == ['Gestão Comercial', 'Chamados', 'Gestão Corporativa', 'Gestão Operacional']
  and em_ordem(grupos[1:]), grupos)
for ident in SUBMENUS:
    r = grupo(m, ident)
    t(f'{ident}: {len(r)} itens em ordem alfabética', len(r) >= 3 and em_ordem(r), r)
corp = grupo(m, 'financeiro-menu')
t('Gestão Corporativa: Gerenciamento de metas, Quiz e Uniformes no lugar certo',
  corp[-3:] == ['Quadro de HC', 'Quiz', 'Uniformes'] and corp.index('Gerenciamento de metas') == corp.index('Financeiro') + 1, corp)
oper = grupo(m, 'gestao-menu')
t('Gestão Operacional: Auditoria D-1 e Contas a Pagar no lugar certo',
  oper[:3] == ['Agenda', 'Arquivos', 'Auditoria D-1'] and oper.index('Contas a Pagar') + 1 == oper.index('Contestação'), oper)
ajuda = re.findall(r'<span class="sidebar-text">([^<]+)</span>', m[m.index('Configurações'):])
t('Ajuda e Denúncia no fim, nessa ordem', ajuda[-2:] == ['Ajuda', 'Denuncia'], ajuda)
links = re.findall(r'<a href="([^"]+)"', m)
rep = [h for h, n in Counter(links).items() if n > 1]
t('nenhum item repetido no menu', not rep, rep)

for nome, c in (('ADMIN', c_admin), ('PADRÃO', c_padrao)):
    print(nome)
    m = menu_lateral(c)
    for ident in SUBMENUS:
        r = grupo(m, ident)
        t(f'{ident}: {len(r)} itens em ordem alfabética', em_ordem(r), r)
    links = re.findall(r'<a href="([^"]+)"', m)
    t('nenhum item repetido no menu', len(links) == len(set(links)))
t('PADRÃO não vê Uniformes nem Contas a Pagar', 'Uniformes' not in grupo(m, 'financeiro-menu')
  and 'Contas a Pagar' not in grupo(m, 'gestao-menu'))

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
