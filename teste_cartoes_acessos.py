"""Cartões: o SUPERADMIN escolhe quem mais cuida dos cartões.

Pedido (29/09/2026): "em /cartoes e /cartoes/novo, permita superadmin adicionar
usuários para conseguir gerenciar, criar e manusear os cartões".

O que este teste cobre:

- antes de ser liberado, o usuário comum não entra no módulo nem cria cartão;
- o SUPERADMIN abre a lista, adiciona alguém e essa pessoa passa a ver **todos**
  os cartões, criar cartão e lançar gasto no cartão de qualquer um;
- quem foi liberado **não** mexe na lista (isso continua sendo do SUPERADMIN);
- tirar da lista tira o acesso na mesma hora;
- adicionar quem já é SUPERADMIN não duplica nada;
- quem foi liberado é avisado;
- a tela mostra "Novo cartão" para quem gere e "Quem cuida" só para o SUPERADMIN;
- a tabela nova, antes do migrate, responde "não" em vez de derrubar o módulo.

Roda num sqlite descartável (a tabela `cartoes_acessocartoes` só existe no
Postgres compartilhado depois do migrate). Nada sai para o banco de verdade.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-cartoes-'))
(PASTA / 'zz_cartoes_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_cartoes_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model
from django.db import DatabaseError

from cartoes.models import AcessoCartoes, Cartao
from cartoes.permissions import (can_manage_cartao, cartoes_do_usuario, pode_administrar_acessos,
                                 pode_gerir_cartoes)
from core.models import Notification
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


loja = Sector.objects.create(name='ZZ Loja dos Cartões')
chefe = User.objects.create_user(username='zzk.chefe', email='zzk.chefe@exemplo-teste.local',
                                 password='S3nha!teste', first_name='ZZK', last_name='Chefe',
                                 hierarchy='SUPERADMIN', sector=loja)
financeiro = User.objects.create_user(username='zzk.fin', email='zzk.fin@exemplo-teste.local',
                                      password='S3nha!teste', first_name='ZZK',
                                      last_name='Financeiro', sector=loja)
dona = User.objects.create_user(username='zzk.dona', email='zzk.dona@exemplo-teste.local',
                                password='S3nha!teste', first_name='ZZK', last_name='Dona',
                                sector=loja)
estranho = User.objects.create_user(username='zzk.estranho', email='zzk.estranho@exemplo-teste.local',
                                    password='S3nha!teste', first_name='ZZK', last_name='Estranho',
                                    sector=loja)

cartao_da_dona = Cartao.objects.create(apelido='ZZ Cartão da Dona', first4='1234', last4='4321',
                                       validade_mes=12, validade_ano=2030, bandeira='VISA',
                                       responsavel=dona, ativo=True)

c_chefe = Client(); c_chefe.force_login(chefe)
c_fin = Client(); c_fin.force_login(financeiro)
c_estranho = Client(); c_estranho.force_login(estranho)

print('== ANTES DE SER LIBERADO ==')
t('não gere os cartões', not pode_gerir_cartoes(financeiro))
r = c_fin.get('/cartoes/', follow=True)
t('o módulo não abre', 'Acesso restrito' in r.content.decode(), r.status_code)
r = c_fin.get('/cartoes/novo/', follow=True)
t('e criar cartão também não',
  'quem cuida dos cartões pode criar' in r.content.decode(), r.status_code)
t('nem gerenciar cartão de outro', not can_manage_cartao(financeiro, cartao_da_dona))

print('\n== O SUPERADMIN LIBERA ==')
r = c_chefe.get('/cartoes/acessos/')
t('a lista abre para o SUPERADMIN', r.status_code == 200, r.status_code)
html = r.content.decode()
t('mostrando quem pode ser liberado', 'ZZK Financeiro' in html and 'ct-busca' in html)
t('com busca por nome ou loja', 'Buscar por nome ou loja' in html)

Notification.objects.all().delete()
r = c_chefe.post('/cartoes/acessos/',
                 {'user': str(financeiro.id), 'observacao': 'ZZ cuida do financeiro'}, follow=True)
t('a pessoa entra na lista', AcessoCartoes.objects.filter(user=financeiro).exists())
acesso = AcessoCartoes.objects.get(user=financeiro)
t('com quem liberou e o motivo',
  acesso.liberado_por_id == chefe.id and acesso.observacao == 'ZZ cuida do financeiro')
t('e ela é avisada',
  Notification.objects.filter(user=financeiro, title__icontains='cartões').exists(),
  list(Notification.objects.values_list('user_id', 'title')))

print('\n== O QUE ELA PASSA A PODER ==')
t('gere o módulo', pode_gerir_cartoes(financeiro))
t('vê todos os cartões', list(cartoes_do_usuario(financeiro)) == [cartao_da_dona],
  list(cartoes_do_usuario(financeiro)))
t('e gerencia o cartão de outra pessoa', can_manage_cartao(financeiro, cartao_da_dona))

r = c_fin.get('/cartoes/')
t('o módulo abre', r.status_code == 200, r.status_code)
html = r.content.decode()
t('com a visão de gestão', 'Gestão de todos os cartões' in html)
t('e o botão de criar cartão', '/cartoes/novo/' in html)
t('mas sem a lista de quem cuida', '/cartoes/acessos/' not in html)

r = c_fin.get('/cartoes/novo/')
t('a tela de criar abre', r.status_code == 200, r.status_code)
r = c_fin.post('/cartoes/novo/', {
    'apelido': 'ZZ Cartão criado pelo financeiro', 'first4': '5555', 'last4': '9999',
    'responsavel': str(dona.id), 'bandeira': 'MASTERCARD',
    'validade_mes': '10', 'validade_ano': '2031',
}, follow=True)
criado = Cartao.objects.filter(apelido='ZZ Cartão criado pelo financeiro').first()
t('e ela cria o cartão', criado is not None, r.status_code)
t('para quem ela escolher', criado and criado.responsavel_id == dona.id)

r = c_fin.get(f'/cartoes/{cartao_da_dona.pk}/')
t('o extrato do cartão de outro abre', r.status_code == 200, r.status_code)

print('\n== O QUE ELA NÃO PODE ==')
t('não administra a lista', not pode_administrar_acessos(financeiro))
r = c_fin.get('/cartoes/acessos/', follow=True)
t('a tela da lista a manda de volta',
  'Só o SUPERADMIN define' in r.content.decode(), r.status_code)
r = c_fin.post('/cartoes/acessos/', {'user': str(estranho.id)}, follow=True)
t('e o POST não libera ninguém', not AcessoCartoes.objects.filter(user=estranho).exists())

print('\n== TIRAR DA LISTA ==')
r = c_chefe.post('/cartoes/acessos/', {'user': str(chefe.id)}, follow=True)
t('adicionar quem já é SUPERADMIN não cria linha',
  not AcessoCartoes.objects.filter(user=chefe).exists()
  and 'já é SUPERADMIN' in r.content.decode())

r = c_chefe.post(f'/cartoes/acessos/{acesso.pk}/remover/', follow=True)
t('sai da lista', not AcessoCartoes.objects.filter(user=financeiro).exists())
t('e perde a gestão na hora', not pode_gerir_cartoes(financeiro))
r = c_fin.get('/cartoes/', follow=True)
t('o módulo fecha de novo', 'Acesso restrito' in r.content.decode(), r.status_code)

print('\n== ANTES DO MIGRATE ==')
with mock.patch.object(AcessoCartoes, 'objects') as gerente:
    gerente.filter.side_effect = DatabaseError('relation "cartoes_acessocartoes" does not exist')
    t('sem a tabela, a pergunta responde "não" em vez de quebrar',
      AcessoCartoes.tem_acesso(dona) is False)
    t('e o SUPERADMIN continua gerindo', pode_gerir_cartoes(chefe))

print('\n== O SUPERADMIN CONTINUA VENDO TUDO ==')
html = c_chefe.get('/cartoes/').content.decode()
t('inclusive o atalho da lista', '/cartoes/acessos/' in html and 'Quem cuida' in html)
t('e o botão de criar', '/cartoes/novo/' in html)

print(f'\n{ok} OK / {fail} falhas')
print(f'(descartável em {PASTA})')
sys.exit(1 if fail else 0)
