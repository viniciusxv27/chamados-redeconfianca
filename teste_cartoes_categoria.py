"""/cartoes/{id}/gasto/ (24/11/2026): a categoria é um select das que já existem ou "Criar nova".

Pedido: "Categoria deve ser um select para selecionar as que já existem ou Criar uma Nova".

Cobre: a lista vem dos gastos já lançados, uma de cada (grafias do mesmo nome juntam, vale a
mais usada); escolher uma existente grava como ela é; criar nova grava e passa a aparecer na
lista; digitar uma que já existe com outra grafia grava com a de sempre (não duplica); a IA
(API do WhatsApp) também cai na grafia de sempre; o formulário com erro volta com a escolhida;
o JS passa no node --check.

Banco descartável (sqlite, syncdb): nada sai daqui.
"""
import os
import pathlib
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-cartao-categoria-'))
(PASTA / 'zz_cartao_categoria_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cartcat'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cartcat-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_cartao_categoria_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import re
import subprocess
from datetime import date

import requests
from django.contrib.auth import get_user_model

from cartoes import views as cv
from cartoes.models import Cartao, Gasto

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
mock.patch.object(cv, 'enviar_texto', side_effect=AssertionError('teste tentou mandar WhatsApp')).start()

chefe = User.objects.create_user(username='zzcat.chefe', email='zzcat.chefe@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Chefe', last_name='ZZ', hierarchy='SUPERADMIN', is_superuser=True)
cartao = Cartao.objects.create(first4='4111', last4='1111', responsavel=chefe, validade_mes=12, validade_ano=2030,
                               bandeira=Cartao._meta.get_field('bandeira').choices[0][0])
for categoria, n in (('Alimentação', 5), ('alimentacao', 1), ('ALIMENTAÇÃO ', 1), ('Combustível', 2),
                     ('Material de escritório', 1), ('', 3)):
    for _ in range(n):
        Gasto.objects.create(cartao=cartao, criado_por=chefe, valor=10, data_gasto=date.today(),
                             categoria_gasto=categoria, parcelas=1)
c = Client(); c.force_login(chefe)
URL = f'/cartoes/{cartao.pk}/gasto/'

print('== 1) A LISTA ==')
r = c.get(URL)
html = r.content.decode()
t('o formulário abre', r.status_code == 200, r.status_code)
opcoes = re.findall(r'<option value="([^"]*)"', html.split('id="f-categoria-sel"')[1].split('</select>')[0])
t('as existentes, uma de cada, em ordem, com "Criar nova" no fim',
  opcoes == ['', 'Alimentação', 'Combustível', 'Material de escritório', '__nova__'], opcoes)
t('o que vai para o servidor é o campo escondido', 'type="hidden" name="categoria_gasto" id="f-categoria"' in html)
t('"Criar nova" abre o campo do nome', 'id="f-categoria-texto"' in html and '+ Criar nova categoria' in html)


def lancar(categoria, **mais):
    dados = {'valor': '25,90', 'estabelecimento': 'Padaria ZZ', 'parcelas': '1', 'data_gasto': date.today().isoformat(),
             'categoria_gasto': categoria, 'descricao': 'Lanche ZZ'}
    dados.update(mais)
    return c.post(URL, dados)


print('\n== 2) LANÇAR ==')
antes = Gasto.objects.count()
lancar('Combustível')
t('existente escolhida: grava como ela é', Gasto.objects.order_by('-pk').first().categoria_gasto == 'Combustível'
  and Gasto.objects.count() == antes + 1)
lancar('  alimentacao ')
t('digitada com outra grafia: grava com a de sempre (não duplica)',
  Gasto.objects.order_by('-pk').first().categoria_gasto == 'Alimentação')
lancar('Manutenção da loja')
t('nova: grava', Gasto.objects.order_by('-pk').first().categoria_gasto == 'Manutenção da loja')
opcoes = re.findall(r'<option value="([^"]*)"', c.get(URL).content.decode().split('id="f-categoria-sel"')[1].split('</select>')[0])
t('e passa a aparecer na lista', 'Manutenção da loja' in opcoes and opcoes.count('Alimentação') == 1, opcoes)
lancar('')
t('sem categoria continua valendo', Gasto.objects.order_by('-pk').first().categoria_gasto == '')

print('\n== 3) FORMULÁRIO COM ERRO E IA ==')
r = lancar('Combustível', valor='')
t('com erro, o formulário volta com a categoria escolhida', r.status_code == 200
  and 'id="f-categoria" value="Combustível"' in r.content.decode())
t('a categoria da IA (API do WhatsApp) também cai na grafia de sempre', cv.padronizar_categoria('ALIMENTACAO') == 'Alimentação'
  and cv.padronizar_categoria('Estacionamento') == 'Estacionamento' and cv.padronizar_categoria('   ') == '')
fonte = (pathlib.Path(__file__).parent / 'cartoes' / 'views.py').read_text(encoding='utf-8')
t('a API usa a mesma padronização', "categoria_gasto = padronizar_categoria((ia.get('categoria')" in fonte)
t('a leitura da IA na tela escolhe a existente ou abre "nova"', 'escolherCategoria(data.categoria)' in html)

print('\n== 4) JS ==')
js = '\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', c.get(URL).content.decode(), re.S))
js = next((sc for sc in re.findall(r'<script[^>]*>(.*?)</script>', c.get(URL).content.decode(), re.S)
           if 'escolherCategoria' in sc), '')
arq = PASTA / 'gasto.js'
arq.write_text(js, encoding='utf-8')
try:
    chk = subprocess.run(['node', '--check', str(arq)], capture_output=True, text=True, timeout=30)
    t('JS do formulário passa no node --check', chk.returncode == 0, chk.stderr[:300])
except FileNotFoundError:
    print('  (node não encontrado: pulei o --check)')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
