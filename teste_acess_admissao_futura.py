"""Acess /pendentes/ (22/10/2026): admissão no futuro → a data de criar no GEFAC aparece embaixo do nome.

Transação desfeita no fim; nada sai para a rede.
"""
import os
import sys
from datetime import timedelta
from unittest import mock

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
from django.conf import settings

settings.CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-af'},
                   'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-af2'}}
django.setup()
from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from django.utils import timezone

from acess import servicos as sv
from acess.models import AcessoAcess
from acess.permissoes import CHAVES
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
    raise AssertionError('o teste tentou sair para a rede')


marcador = transaction.atomic()
marcador.__enter__()
try:
    with mock.patch.object(requests, 'post', sem_rede), mock.patch.object(requests, 'get', sem_rede):
        hoje = timezone.localdate()
        loja = Sector.objects.create(name='Loja ZZ Admissao', adabas='ZZA0009-001')
        op = User.objects.create_user(username='zz.af.op', email='zz.af.op@exemplo-teste.local', password='x',
                                      first_name='ZZ', last_name='Op', hierarchy='ADMIN')
        AcessoAcess.objects.create(user=op, permissoes=sorted(CHAVES))
        co = Client()
        co.force_login(op)

        def pessoa(n, cpf, admissao):
            u = User.objects.create_user(username=f'zz.af.{n}', email=f'zz.af.{n}@exemplo-teste.local', password='x',
                                         first_name='ZZAF', last_name=n.upper(), sector=loja, admission_date=admissao)
            return sv.criar_colaborador({'nome': f'ZZAF {n}', 'cpf': cpf}, loja, usuario=op, user=u)

        futura = hoje + timedelta(days=5)
        c1 = pessoa('futuro', '52998224725', futura)
        c2 = pessoa('passado', '11144477735', hoje - timedelta(days=3))
        c3 = pessoa('semdata', '39053344705', None)
        h = co.get('/acess/pendentes/?etapa=1&q=ZZAF').content.decode()
        i = h.index('ZZAF FUTURO')
        t('admissão no futuro: data do GEFAC embaixo do nome', f'Criar no GEFAC em {futura:%d/%m/%Y}' in h[i:i + 900]
          and 'admissão daqui a 5 dias' in h[i:i + 900])
        t('quem já foi admitido ou não tem data: nada', h.count('Criar no GEFAC em') == 1)
        sv.concluir_gefac(c1, c1.versao, op)
        h = co.get('/acess/pendentes/?etapa=2&q=ZZAF').content.decode()
        t('nas outras etapas aparece a admissão futura', f'Admissão em {futura:%d/%m/%Y}' in h)
finally:
    marcador.__exit__(Exception, Exception('desfaz'), None)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
