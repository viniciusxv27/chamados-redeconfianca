"""Quadro "Projetos da Inteligência" no Kanban do Impulso (10/11/2026). Banco descartável; nada sai para a rede."""
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-intel-'))
(PASTA / 'zz_intel_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-intel'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-intel-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_intel_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext

from communications.models import CommunicationGroup
from impulso.models import ProjetoInteligencia
from impulso.views_projetos_intel import projetos_do_quadro
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


cache.clear()
intel = Sector.objects.create(name='INTELIGÊNCIA ')
outro = Sector.objects.create(name='ZZ Escritório')
SENHA = 'S3nha!teste'
chefe = User.objects.create_user(username='zzi.super', email='zzi.super@exemplo-teste.local', password=SENHA,
                                 first_name='ZZI', last_name='Super', sector=outro, hierarchy='SUPERADMIN')
kaio = User.objects.create_user(username='zzi.kaio', email='zzi.kaio@exemplo-teste.local', password=SENHA,
                                first_name='ZZI', last_name='Kaio', sector=intel, hierarchy='ADMIN')
bia = User.objects.create_user(username='zzi.bia', email='zzi.bia@exemplo-teste.local', password=SENHA,
                               first_name='ZZI', last_name='Bia', sector=intel, hierarchy='ADMIN')
ana = User.objects.create_user(username='zzi.ana', email='zzi.ana@exemplo-teste.local', password=SENHA,
                               first_name='ZZI', last_name='Ana', sector=outro, hierarchy='ADMIN')
ana.sectors.add(intel)   # Inteligência só no M2M: não é do setor (vale o setor principal)
adm = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=chefe)
for p in (chefe, kaio, bia, ana):
    p.communication_groups.add(adm)

cs, ck, cb, ca = Client(), Client(), Client(), Client()
cs.force_login(chefe); ck.force_login(kaio); cb.force_login(bia); ca.force_login(ana)
URL = '/impulso/metas/projetos-inteligencia/'

print('Quem vê o quadro')
for nome, c in (('SUPERADMIN', cs), ('membro da Inteligência', ck)):
    r = c.get('/impulso/metas/')
    h = r.content.decode()
    t(f'{nome}: Kanban abre com o quadro', r.status_code == 200 and 'id="intel-projetos"' in h and 'intel-projetos-dados' in h, r.status_code)
r = ca.get('/impulso/metas/')
h = r.content.decode()
t('fora do setor (só no M2M): Kanban abre sem o quadro', r.status_code == 200 and 'intel-projetos' not in h and r.context['projetos_intel'] is None)
t('fora do setor: criar dá 404', ca.post(URL, {'nome': 'X'}).status_code == 404)
t('fora do setor: GET no criar dá 404', ca.get(URL).status_code == 404)

print('Incluir, renomear, estágio, excluir')
r = ck.post(URL, {'nome': '  Painel   de churn  '})
j = r.json()
t('inclui (nome limpo, começa em Ideação)', r.status_code == 200 and j['ok'] and j['projeto']['nome'] == 'Painel de churn'
  and j['projeto']['estagio'] == 'IDEACAO', j)
pid = j['projeto']['id']
p = ProjetoInteligencia.objects.get(pk=pid)
t('grava quem criou', p.criado_por_id == kaio.id and p.atualizado_por_id == kaio.id)
t('GET no criar não é aceito (405)', ck.get(URL).status_code == 405)
r = cb.post(f'{URL}{pid}/', {'estagio': 'DESENVOLVIMENTO'})
p.refresh_from_db()
t('outro membro muda o estágio (lista compartilhada)', r.json()['ok'] and p.estagio == 'DESENVOLVIMENTO' and p.nome == 'Painel de churn')
t('grava quem mexeu por último', p.atualizado_por_id == bia.id and p.criado_por_id == kaio.id)
r = cs.post(f'{URL}{pid}/', {'nome': 'Painel de churn v2', 'nota': 'com a base do SAP'})
p.refresh_from_db()
t('SUPERADMIN renomeia e anota', r.json()['ok'] and p.nome == 'Painel de churn v2' and p.nota == 'com a base do SAP' and p.estagio == 'DESENVOLVIMENTO')
r = cb.get('/impulso/metas/')
t('o projeto aparece para o outro membro', 'Painel de churn v2' in r.content.decode()
  and r.context['projetos_intel'] == [{'id': pid, 'nome': 'Painel de churn v2', 'nota': 'com a base do SAP', 'estagio': 'DESENVOLVIMENTO'}])
t('fora do setor não edita (404)', ca.post(f'{URL}{pid}/', {'estagio': 'TESTES'}).status_code == 404)
t('fora do setor não exclui (404)', ca.post(f'{URL}{pid}/excluir/').status_code == 404 and ProjetoInteligencia.objects.filter(pk=pid).exists())

print('Validação')
r = ck.post(URL, {'nome': '   '})
t('nome vazio é recusado (400)', r.status_code == 400 and not r.json()['ok'] and ProjetoInteligencia.objects.count() == 1)
r = ck.post(URL, {'nome': 'x' * 121})
t('nome longo é recusado', r.status_code == 400)
r = ck.post(f'{URL}{pid}/', {'estagio': 'PRONTO'})
p.refresh_from_db()
t('estágio inválido é recusado e nada muda', r.status_code == 400 and p.estagio == 'DESENVOLVIMENTO')
r = ck.post(f'{URL}{pid}/', {'nome': ''})
p.refresh_from_db()
t('renomear para vazio é recusado', r.status_code == 400 and p.nome == 'Painel de churn v2')
t('projeto inexistente dá 404', ck.post(f'{URL}99999/', {'estagio': 'TESTES'}).status_code == 404)
r = ck.post(URL, {'nome': 'Bot de metas', 'estagio': 'TESTES'})
t('inclui já com estágio', r.json()['projeto']['estagio'] == 'TESTES')
for e in ('IDEACAO', 'DESENVOLVIMENTO', 'TESTES', 'CONCLUSAO'):
    t(f'aceita o estágio {e}', ck.post(f'{URL}{pid}/', {'estagio': e}).json()['ok'])
r = ck.post(f'{URL}{pid}/excluir/')
t('exclui', r.json()['ok'] and not ProjetoInteligencia.objects.filter(pk=pid).exists())
t('excluir de novo dá 404', ck.post(f'{URL}{pid}/excluir/').status_code == 404)

print('Custo')
with CaptureQueriesContext(connection) as q:
    fora = projetos_do_quadro(ana)
t('fora do setor: nenhuma consulta', fora is None and len(q.captured_queries) == 0, [x['sql'] for x in q.captured_queries])
sem_setor = User.objects.create_user(username='zzi.sem', email='zzi.sem@exemplo-teste.local', password=SENHA, first_name='ZZI')
cache.clear()
with CaptureQueriesContext(connection) as q:
    fora = projetos_do_quadro(sem_setor)
t('sem setor: nenhuma consulta nem com o cache vazio', fora is None and len(q.captured_queries) == 0)
projetos_do_quadro(kaio)
with CaptureQueriesContext(connection) as q:
    dentro = projetos_do_quadro(kaio)
t('membro: uma consulta', len(dentro) == 1 and len(q.captured_queries) == 1, len(q.captured_queries))
with CaptureQueriesContext(connection) as q:
    ca.get('/impulso/metas/')
t('Kanban de quem é de fora não consulta a tabela do quadro',
  not any('projetointeligencia' in x['sql'].lower() for x in q.captured_queries))

print('Template')
src = Path('templates/impulso/_projetos_inteligencia.html').read_text() + Path('templates/impulso/metas_kanban.html').read_text()
t('nenhum {# #} em várias linhas', not re.search(r'\{#(?:(?!#\}).)*\n', src))
h = ck.get('/impulso/metas/').content.decode()
t('sem {# vazando na tela', '{#' not in h and '#}' not in h)
t('tem CSRF no script do quadro', "const csrftoken = '" in h and "const csrftoken = ''" not in h)

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
