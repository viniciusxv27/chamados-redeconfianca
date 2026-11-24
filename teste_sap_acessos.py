"""/sap/gestores/ (24/11/2026): o SUPERADMIN libera a Visão SAP para usuários. Banco descartável."""
import io
import json
import os
import pathlib
import sys
import tempfile
import zipfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-sap-acessos-'))
(PASTA / 'zz_sap_acessos_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': r'{PASTA}/banco.sqlite3'}}}}


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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sapac'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sapac-2'}},
}}
""")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_sap_acessos_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import requests
from django.contrib.auth import get_user_model

from auditoria_sap.permissions import gestores_escolhidos, lojas_visiveis, pode_ver
from users.models import UserModuleAccess

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
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()


def novo(u, **k):
    k.setdefault('hierarchy', 'PADRAO')
    return User.objects.create_user(username=f'zzs.{u}', email=f'zzs.{u}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=u.title(), last_name='ZZ', **k)


sa = novo('super', hierarchy='SUPERADMIN')
adm = novo('adm', hierarchy='ADMIN')
maria = novo('maria')
joao = novo('joao')
saiu = novo('saiu', is_active=False)
csa, cadm, cm = Client(), Client(), Client()
csa.force_login(sa); cadm.force_login(adm); cm.force_login(maria)
U = '/sap/gestores/'

t('antes: Maria (PADRÃO) não vê a Visão SAP', not pode_ver(maria) and cm.get('/sap/').status_code == 302)
h = csa.get(U).content.decode()
t('SUPERADMIN vê a lista "Quem acessa a Visão SAP"', 'Quem acessa a Visão SAP' in h and 'name="acessos"' in h
  and 'Gestores da Visão SAP' in h)
t('a aba se chama "Acessos e gestores"', 'Acessos e gestores' in h)
t('ADMIN não abre a tela de acessos', cadm.get(U).status_code == 302)
cadm.post(U, {'secao': 'acessos', 'acessos': [maria.pk]})
t('ADMIN não libera ninguém', not UserModuleAccess.objects.filter(module_key='sap').exists())
r = csa.post(U, {'secao': 'acessos', 'acessos': [maria.pk, saiu.pk]}, follow=True)
t('SUPERADMIN libera a Maria (inativo é ignorado)', set(UserModuleAccess.objects.filter(module_key='sap')
                                                        .values_list('user_id', flat=True)) == {maria.pk}
  and '1 pessoa(s) liberada(s)' in r.content.decode())
t('liberar não mexe nos gestores', not gestores_escolhidos().exists())
maria = User.objects.get(pk=maria.pk)
t('Maria liberada vê a rede inteira', pode_ver(maria) and lojas_visiveis(maria) is None)
t('e abre a Visão SAP', cm.get('/sap/').status_code == 200, cm.get('/sap/').status_code)
h = csa.get(U).content.decode()
t('a Maria aparece marcada na lista', f'name="acessos" value="{maria.pk}" checked' in h)
csa.post(U, {'gestores': [joao.pk]})
t('salvar gestores não tira o acesso da Maria', UserModuleAccess.objects.filter(module_key='sap', user=maria).exists()
  and set(gestores_escolhidos().values_list('pk', flat=True)) == {joao.pk})
csa.post(U, {'secao': 'acessos'})
maria = User.objects.get(pk=maria.pk)
t('desmarcar todos tira o acesso', not UserModuleAccess.objects.filter(module_key='sap').exists() and not pode_ver(maria))
t('e a Maria volta a não entrar', cm.get('/sap/').status_code == 302)
t('o gestor escolhido continua', set(gestores_escolhidos().values_list('pk', flat=True)) == {joao.pk})

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
