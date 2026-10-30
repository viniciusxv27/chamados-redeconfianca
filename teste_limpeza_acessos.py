"""/limpeza/ (30/10/2026): SUPERADMIN libera pessoas para Gerenciar ou Ver.

Cobre:
- o PADRÃO fora do GERENTES continua fora — até ser liberado (menu e URL);
- Ver: entra, vê as limpezas de todos os setores, relatórios e arquivo da rede, sem checklists;
- Gerenciar: checklists (inclusive de outro autor), registrar para qualquer setor, relatórios da rede;
- quem gerenciava pela hierarquia segue igual (relatório só dos setores dele);
- a tela de acessos é só do SUPERADMIN; tirar o acesso fecha de novo.

Roda num sqlite descartável.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-limpeza-acessos-'))
(PASTA / 'zz_limpeza_acessos_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-limp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-limp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_limpeza_acessos_settings'
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
from django.utils import timezone

from limpeza.models import AcessoLimpeza, LimpezaQuestion, LimpezaTemplate, LimpezaTodo
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


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()

gloria = Sector.objects.create(name='Loja Glória')
serra = Sector.objects.create(name='Loja Serra Sede')


def usuario(nome, hierarquia='PADRAO', setor=gloria):
    u = User.objects.create_user(username=f'zzl.{nome}', email=f'zzl.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZL', last_name=nome, sector=setor, hierarchy=hierarquia)
    c = Client(); c.force_login(u)
    return u, c


chefe, cs = usuario('super', 'SUPERADMIN')
admv, cadm = usuario('administrativo', 'ADMINISTRATIVO')
ver, cv = usuario('ver')
ger, cg = usuario('gerenciar')
nada, cn = usuario('nada')

tpl = LimpezaTemplate.objects.create(name='ZZ Checklist do chefe', created_by=chefe)
LimpezaQuestion.objects.create(template=tpl, text='Chão limpo?', order=1)
agora = timezone.now()
LimpezaTodo.objects.create(template=tpl, sector=gloria, month=agora.month, year=agora.year, realizada_em=agora, realizada_por=admv)
LimpezaTodo.objects.create(template=tpl, sector=serra, month=agora.month, year=agora.year, realizada_em=agora)

print('Antes de liberar')
t('PADRÃO fora do GERENTES não entra', cn.get('/limpeza/').status_code == 302 and cv.get('/limpeza/').status_code == 302)
t('e não vê o item no menu', 'href="/limpeza/"' not in cv.get('/', follow=True).content.decode())
t('ADMINISTRATIVO segue gerenciando pela hierarquia', cadm.get('/limpeza/templates/').status_code == 200)
t('acessos: só o SUPERADMIN', cadm.get('/limpeza/acessos/').status_code == 302 and cs.get('/limpeza/acessos/').status_code == 200)
t('SUPERADMIN vê o botão Acessos', 'href="/limpeza/acessos/"' in cs.get('/limpeza/').content.decode())

r = cs.post('/limpeza/acessos/', {'acao': 'adicionar', 'user': ver.id, 'papel': 'VER'})
cs.post('/limpeza/acessos/', {'acao': 'adicionar', 'user': ger.id, 'papel': 'GERENCIAR'})
t('SUPERADMIN libera Ver e Gerenciar', AcessoLimpeza.objects.get(user=ver).papel == 'VER'
  and AcessoLimpeza.objects.get(user=ger).papel == 'GERENCIAR' and AcessoLimpeza.objects.get(user=ger).liberado_por == chefe)
h = cs.get('/limpeza/acessos/').content.decode()
t('a tela lista os liberados', 'ZZL ver' in h and 'ZZL gerenciar' in h)

print('Ver')
cv = Client(); cv.force_login(User.objects.get(pk=ver.pk))
r = cv.get('/limpeza/')
h = r.content.decode()
t('entra mesmo sendo PADRÃO', r.status_code == 200)
t('vê o item no menu', 'href="/limpeza/"' in cv.get('/', follow=True).content.decode())
t('vê as limpezas de todos os setores', 'Loja Serra Sede' in h and h.count('/limpeza/registro/') >= 2)
t('tem Relatórios, não tem Checklists nem Acessos', 'href="/limpeza/relatorios/"' in h and 'href="/limpeza/templates/"' not in h
  and 'href="/limpeza/acessos/"' not in h)
t('relatório e arquivo abrem', cv.get('/limpeza/relatorios/').status_code == 200 and cv.get('/limpeza/arquivo/').status_code == 200)
t('relatório da rede (as duas lojas)', 'Loja Serra Sede' in cv.get('/limpeza/relatorios/').content.decode())
t('não mexe nos checklists', cv.get('/limpeza/templates/').status_code == 302
  and cv.get(f'/limpeza/templates/{tpl.id}/editar/').status_code == 302)
reg_serra = LimpezaTodo.objects.get(sector=serra)
t('abre registro de outro setor', cv.get(f'/limpeza/registro/{reg_serra.id}/').status_code == 200)
h = cv.get('/limpeza/registrar/').content.decode()
t('registra só para o setor dele', 'Loja Glória' in h and 'Loja Serra Sede' not in h)

print('Gerenciar')
cg = Client(); cg.force_login(User.objects.get(pk=ger.pk))
h = cg.get('/limpeza/templates/').content.decode()
t('vê os checklists, inclusive do SUPERADMIN', 'ZZ Checklist do chefe' in h)
r = cg.get(f'/limpeza/templates/{tpl.id}/editar/')
t('edita checklist de outro autor', r.status_code == 200, r.status_code)
t('registra para qualquer setor', 'Loja Serra Sede' in cg.get('/limpeza/registrar/').content.decode())
t('relatório da rede', 'Loja Serra Sede' in cg.get('/limpeza/relatorios/').content.decode())

print('Hierarquia antiga')
t('ADMINISTRATIVO: relatório só dos setores dele (como era)', 'Loja Serra Sede' not in
  cadm.get('/limpeza/relatorios/').content.decode().split('<table')[-1])

print('Tirar o acesso')
cs.post('/limpeza/acessos/', {'acao': 'salvar', 'user': ger.id, 'papel': 'VER'})
cg = Client(); cg.force_login(User.objects.get(pk=ger.pk))
t('trocar Gerenciar por Ver tira os checklists', cg.get('/limpeza/templates/').status_code == 302)
cs.post('/limpeza/acessos/', {'acao': 'remover', 'acesso': AcessoLimpeza.objects.get(user=ver).id})
cv = Client(); cv.force_login(User.objects.get(pk=ver.pk))
t('remover fecha a Limpeza de novo', cv.get('/limpeza/').status_code == 302)

print(f'\n{ok} OK, {fail} falha(s)')
