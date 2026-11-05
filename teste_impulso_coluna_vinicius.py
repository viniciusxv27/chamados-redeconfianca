"""/impulso/metas/ (05/11/2026): caixa "A fazer Vinicius" do SUPERADMIN abre uma coluna com o A Fazer criado pelo Vinicius (User 41). Banco descartável."""
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-kb-vinicius-'))
(PASTA / 'zz_kb_vinicius_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_kb_vinicius_settings'
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

from communications.models import CommunicationGroup
from impulso.models import Meta

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
    return User.objects.create_user(username=f'zzk.{u}', email=f'zzk.{u}@exemplo-teste.local', password='S3nha!teste', first_name=u.title(), last_name='ZZ', **k)


vinicius = novo('vinicius', id=41, hierarchy='SUPERADMIN')
chefe = novo('chefe', hierarchy='SUPERADMIN', is_superuser=True)
gestor = novo('gestor')
ana = novo('ana')
for nome_grupo, membros in (('ESCRITÓRIO (ADM)', [ana, gestor]), ('GESTORES (IMPULSO)', [gestor])):
    g, _ = CommunicationGroup.objects.get_or_create(name=nome_grupo, defaults={'created_by': chefe})
    g.members.add(*membros)
hoje = timezone.localdate()
base = dict(gestor=gestor, colaborador=ana, prazo=hoje, aprovacao=Meta.Aprovacao.APROVADA)
dele1 = Meta.objects.create(titulo='Do Vinicius 1', created_by=vinicius, **base)
dele2 = Meta.objects.create(titulo='Do Vinicius 2', created_by=vinicius, **base)
andando = Meta.objects.create(titulo='Dele em andamento', created_by=vinicius, status='EM_ANDAMENTO', **base)
outra = Meta.objects.create(titulo='De outra pessoa', created_by=gestor, **base)
# 05/11/2026: a coluna passou a trazer tudo o que ele criou e não foi concluído, de qualquer mês.
longe = Meta.objects.create(titulo='Dele mês que vem', created_by=vinicius,
                            **{**base, 'prazo': hoje + timezone.timedelta(days=70)})
feita = Meta.objects.create(titulo='Dele concluída', created_by=vinicius, status='CONCLUIDA', **base)
cs = Client(); cs.force_login(chefe)
cg = Client(); cg.force_login(gestor)
U = '/impulso/metas/'


def colunas(cliente, q=''):
    r = cliente.get(U + q)
    return r, {c['label']: [m.titulo for m in c['metas']] for c in r.context['colunas']}


r, c = colunas(cs)
h = r.content.decode()
t('caixa aparece para o SUPERADMIN, desmarcada', 'data-filtro-a-fazer-vinicius' in h and 'A fazer Vinicius' in h and not r.context['a_fazer_vinicius'])
t('desmarcada: quatro colunas, como sempre', 'A Fazer Vinicius' not in c and len(c) == 4 and 'xl:grid-cols-4' in h, list(c))
t('desmarcada: A Fazer traz tudo', set(c['A Fazer']) == {'Do Vinicius 1', 'Do Vinicius 2', 'De outra pessoa', 'Dele mês que vem'}, c['A Fazer'])
r, c = colunas(cs, '?a_fazer_vinicius=1')
h = r.content.decode()
t('marcada: abre a coluna antes do A Fazer', list(c)[:2] == ['A Fazer Vinicius', 'A Fazer'] and len(c) == 5 and 'xl:grid-cols-5' in h, list(c))
t('a coluna traz tudo dele em aberto, de qualquer status e mês', set(c['A Fazer Vinicius']) == {'Do Vinicius 1', 'Do Vinicius 2', 'Dele em andamento', 'Dele mês que vem'}, c['A Fazer Vinicius'])
t('sem aparecer duas vezes', c['A Fazer'] == ['De outra pessoa'], c['A Fazer'])
t('o que está em andamento sai da coluna comum (não aparece duas vezes)', c['Em Andamento'] == [], c['Em Andamento'])
t('concluída continua na Concluída', 'Dele concluída' in c['Concluída'] and 'Dele concluída' not in c['A Fazer Vinicius'], c['Concluída'])
t('total certo e agrupado por prazo', r.context['colunas'][0]['total'] == 4 and r.context['colunas'][0]['grupos'])
t('os cards dele são vermelhos', h.count('kanban-card imp-card p-3.5 imp-card-vinicius') == 4 and '.imp-card.imp-card-vinicius' in h,
  h.count('kanban-card imp-card p-3.5 imp-card-vinicius'))
t('o card em andamento diz o status', 'bg-red-600 text-white" title="Status da atividade">Em Andamento' in h)
t('caixa volta marcada', 'name="a_fazer_vinicius" value="1"' in h and 'checked' in h[h.index('data-filtro-a-fazer-vinicius'):h.index('A fazer Vinicius', h.index('data-filtro-a-fazer-vinicius'))])
t('dá para arrastar para as outras colunas', h.count('data-status="A_FAZER"') == 2 and 'data-droppable="true"' in h)
r, c = colunas(cs, '?a_fazer_vinicius=1&colaborador=999999')
t('mostra tudo dele mesmo com outros filtros', len(c['A Fazer Vinicius']) == 4 and c['A Fazer'] == [], c)
r = cs.get(U + '?a_fazer_vinicius=1&colaborador=%d' % ana.id)
h = r.content.decode()
forma = h[h.index('data-filtro-a-fazer-vinicius'):h.index('A fazer Vinicius', h.index('data-filtro-a-fazer-vinicius'))]
t('desmarcar mantém os outros filtros', f'name="colaborador" value="{ana.id}"' in forma and 'type="hidden" name="a_fazer_vinicius"' not in forma)
cs.get(U + '?a_fazer_vinicius=1')
d = cs.get(f'/impulso/metas/{dele1.id}/')
t('o Voltar da meta devolve a coluna aberta', 'a_fazer_vinicius=1' in d.content.decode())
r, c = colunas(cg, '?a_fazer_vinicius=1')
h = r.content.decode()
t('gestor não vê a caixa nem a coluna', 'data-filtro-a-fazer-vinicius' not in h and 'A Fazer Vinicius' not in c and not r.context['a_fazer_vinicius'], list(c))
t('e o A Fazer dele segue igual', {'Do Vinicius 1', 'De outra pessoa'} <= set(c['A Fazer']), c['A Fazer'])

print(f'\n{ok} OK, {fail} falha(s)')
