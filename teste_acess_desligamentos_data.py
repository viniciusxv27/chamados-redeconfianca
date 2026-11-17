"""Acess: aviso de inativado no portal mostra a data de demissão do cadastro (User.demission_date)."""
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_tmp = tempfile.mkdtemp()
open(os.path.join(_tmp, 'zz_set_dem.py'), 'w').write(
    "from redeconfianca.settings import *\n"
    "class _M(dict):\n    def __contains__(s, k): return True\n    def __getitem__(s, k): return None\n"
    "MIGRATION_MODULES = _M()\n"
    f"DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': r'{_tmp}/db.sqlite3'}}}}\n"
    "CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dem'},\n"
    "          'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-dem2'}}\n")
sys.path.insert(0, _tmp)
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_set_dem'
import django

django.setup()
from django.core.management import call_command

call_command('migrate', '--run-syncdb', verbosity=0)
from django.conf import settings
from django.test.utils import setup_test_environment

setup_test_environment()
settings.ALLOWED_HOSTS.append('testserver')
import requests
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import Client
from django.db import connection
from django.test.utils import CaptureQueriesContext

from acess import servicos as sv
from acess.models import AcessoAcess, AvisoInativacao
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


def sem_rede(*a, **k):
    raise AssertionError('rede')


with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
    loja = Sector.objects.create(name='Loja ZZ Dem', adabas='ZZD0001-001')
    op = User.objects.create_user(username='zz.dem.op', email='op@exemplo-teste.local', password='x',
                                  first_name='ZZ', last_name='Op', hierarchy='ADMIN')
    AcessoAcess.objects.create(user=op, permissoes=['desligamentos.gerenciar'])
    co = Client()
    co.force_login(op)

    def pessoa(n, cpf, dem):
        u = User.objects.create_user(username=f'zz.dem.{n}', email=f'zz.dem.{n}@exemplo-teste.local', password='x',
                                     first_name='ZZ', last_name=f'Pessoa {n}', sector=loja, demission_date=dem)
        c = sv.criar_colaborador({'nome': f'ZZ Pessoa {n}', 'cpf': cpf}, loja, usuario=op, user=u)
        u.is_active = False
        u.save()
        return u, c

    pessoa('um', '52998224725', date(2026, 11, 5))
    pessoa('dois', '11144477735', None)
    t('dois avisos pendentes', AvisoInativacao.objects.filter(status='PENDENTE').count() == 2)
    h = co.get('/acess/desligamentos/').content.decode()
    t('mostra a data dd/mm/aaaa', 'data de demissão: 05/11/2026' in h)
    t('sem data: não informada', 'data de demissão: <span class="font-normal text-gray-400">não informada' in h)
    cpfs = []
    for base in range(100000000, 100000400):
        d = [int(c) for c in str(base)]
        for k in (9, 10):
            v = sum(x * (k + 1 - i) for i, x in enumerate(d)) * 10 % 11 % 10
            d.append(v)
        cpf = ''.join(map(str, d))
        if len(set(cpf)) > 1:
            cpfs.append(cpf)
    for n in range(3):
        pessoa(f'x{n}', cpfs[n], date(2026, 11, 1 + n))
    with CaptureQueriesContext(connection) as q1:
        co.get('/acess/desligamentos/')
    for n in range(3, 8):
        pessoa(f'x{n}', cpfs[n], date(2026, 11, 9))
    with CaptureQueriesContext(connection) as q2:
        co.get('/acess/desligamentos/')
    t('sem consulta por item', len(q1) == len(q2), f'{len(q1)} vs {len(q2)}')
print(f'\n{ok} OK, {fail} falhas')
sys.exit(1 if fail else 0)
