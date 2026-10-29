"""/ponto/configuracao/ (29/10/2026): Ponto e Férias ligam e desligam separados.

Pedido: poder desligar só as férias, e elas sumirem para todos menos o SUPERADMIN.

Cobre, por tipo de usuário (padrão do grupo, ADMIN/ADMINISTRAÇÃO, SUPERADMIN, superusuário):
- só férias desligada: ponto segue, férias some (menu, abas, telas, popup, bloqueio, assistente);
- só ponto desligado: férias segue sozinha;
- a configuração grava a chave nova e não mexe na outra.

Roda num sqlite descartável; nada fala com o Tangerino (requests falso).
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: a tabela das entregas e a coluna
# `exige_entrega` só existem no Postgres compartilhado depois do migrate, e o
# migrate é dele. O schema aqui sai direto dos models (`--run-syncdb`), como em
# qualquer outra sessão — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-ponto-ferias-'))
(PASTA / 'zz_ponto_ferias_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pfer'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pfer-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_ponto_ferias_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import requests

from communications.models import CommunicationGroup
from tangerino.models import ConfiguracaoTangerino
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
    raise AssertionError('teste tentou sair para a rede')


mock.patch.object(requests, 'post', sem_rede).start()
mock.patch.object(requests, 'get', sem_rede).start()
mock.patch.object(requests, 'request', sem_rede).start()

loja = Sector.objects.create(name='ZZ LOJA PONTO')


def usuario(nome, hierarquia, **extra):
    u = User.objects.create_user(username=f'zzpf.{nome}', email=f'zzpf.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZPF', last_name=nome, sector=loja, hierarchy=hierarquia, **extra)
    c = Client(); c.force_login(u)
    return u, c


padrao, cp = usuario('padrao', 'PADRAO')
fora, cf = usuario('fora', 'PADRAO')
admin, ca = usuario('admin', 'ADMIN')
superadmin, cs = usuario('super', 'SUPERADMIN')
raiz, cr = usuario('raiz', 'PADRAO', is_superuser=True, is_staff=True)
grupo = CommunicationGroup.objects.create(name='ZZ Ponto liberado', created_by=raiz)
grupo.members.add(padrao)

config = ConfiguracaoTangerino.get()
config.ativo, config.ferias_ativo, config.restrito_ao_grupo, config.grupo = True, True, True, grupo
config.save()
t('férias nasce ligada (não muda nada para quem já usa)', ConfiguracaoTangerino._meta.get_field('ferias_ativo').default is True)

print('Tudo ligado')
c = ConfiguracaoTangerino.get()
t('padrão do grupo vê ponto e férias', c.libera_ponto(padrao) and c.libera_ferias(padrao))
t('fora do grupo não vê nenhum', not c.libera_ponto(fora) and not c.libera_ferias(fora))
t('ADMIN vê os dois', c.libera_ponto(admin) and c.libera_ferias(admin))

print('Só férias desligada')
config.ferias_ativo = False; config.save()
c = ConfiguracaoTangerino.get()
t('padrão: ponto segue, férias some', c.libera_ponto(padrao) and not c.libera_ferias(padrao))
t('ADMIN também perde férias (só SUPERADMIN fica)', c.libera_ponto(admin) and not c.libera_ferias(admin))
t('SUPERADMIN e superusuário continuam vendo férias', c.libera_ferias(superadmin) and c.libera_ferias(raiz))
t('libera() geral segue verdadeiro pelo ponto', c.libera(padrao))

r = cp.get('/ferias/')
t('padrão: /ferias/ volta para a home', r.status_code == 302 and r.url == '/', (r.status_code, getattr(r, 'url', '')))
t('padrão: /ferias/equipe/ fechada', cp.get('/ferias/equipe/').status_code == 302)
t('ADMIN: /ferias/equipe/ fechada', ca.get('/ferias/equipe/').status_code == 302)
t('padrão: API do popup de férias fechada', cp.get('/api/tangerino/ferias/popup/').status_code == 302)
t('SUPERADMIN: /ferias/ abre', cs.get('/ferias/').status_code == 200)
r = cp.get('/ponto/')
t('padrão: /ponto/ continua abrindo', r.status_code == 200, r.status_code)
h = r.content.decode()
t('padrão: some "Férias" do menu e "Minhas férias" das abas', 'href="/ferias/"' not in h and 'Minhas férias' not in h)
t('padrão: "Meu Ponto" segue no menu', 'href="/ponto/"' in h)
t('padrão: popup de férias não entra na página', '/api/tangerino/ferias/popup/' not in h)
h = cs.get('/ponto/').content.decode()
t('SUPERADMIN: menu e aba de férias aparecem', 'href="/ferias/"' in h and 'Minhas férias' in h and 'Férias da equipe' in h)

from tangerino import middleware as mw
config.bloquear_navegacao_ferias = True; config.save()
with mock.patch.object(mw, 'esta_de_ferias', lambda u: {'inicio': None, 'fim': None}):
    r = cp.get('/ponto/')
t('bloqueio de "está de férias" não tranca ninguém com férias desligada', r.status_code == 200, r.status_code)
config.bloquear_navegacao_ferias = False; config.save()

from assistente import ferramentas
t('assistente: não responde férias', 'não estão disponíveis' in ferramentas._minhas_ferias(padrao, {}))
t('assistente: SUPERADMIN segue consultando', 'não estão disponíveis' not in ferramentas._minhas_ferias(superadmin, {}))

print('Só ponto desligado')
config.ativo, config.ferias_ativo = False, True; config.save()
c = ConfiguracaoTangerino.get()
t('padrão: férias segue sozinha', c.libera_ferias(padrao) and not c.libera_ponto(padrao))
r = cp.get('/ferias/')
t('padrão: /ferias/ abre', r.status_code == 200, r.status_code)
h = r.content.decode()
t('padrão: menu sem "Meu Ponto", com "Férias"', 'href="/ferias/"' in h and 'Meu Ponto' not in h)
t('padrão: aba "Meu ponto" some', 'Meu ponto</a>' not in h.replace('\n', '').replace('  ', ''))
t('padrão: /ponto/ fechado', cp.get('/ponto/').status_code == 302)
t('fora do grupo segue sem férias', not c.libera_ferias(fora))

print('Configuração')
config.ativo, config.ferias_ativo = True, True; config.save()
r = cr.get('/ponto/configuracao/')
h = r.content.decode()
t('tela tem as duas chaves', r.status_code == 200 and 'name="ativo"' in h and 'name="ferias_ativo"' in h
  and 'Ponto ativo' in h and 'Férias ativa' in h)
r = cr.post('/ponto/configuracao/', {'ativo': 'on', 'restrito_ao_grupo': 'on', 'grupo': str(grupo.id),
                                     'mostrar_popup_ferias': 'on', 'mostrar_widget_home': 'on'})
c = ConfiguracaoTangerino.get()
t('desmarcar só férias grava só férias', r.status_code == 302 and c.ativo and not c.ferias_ativo, (r.status_code, c.ativo, c.ferias_ativo))
r = cr.post('/ponto/configuracao/', {'ferias_ativo': 'on', 'restrito_ao_grupo': 'on', 'grupo': str(grupo.id)})
c = ConfiguracaoTangerino.get()
t('e o contrário', not c.ativo and c.ferias_ativo)

print(f'\n{ok} OK, {fail} falha(s)')
