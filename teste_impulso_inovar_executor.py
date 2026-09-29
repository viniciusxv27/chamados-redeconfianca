"""INOVAR: aprovar a ideia nomeia executor e prazo, e vira atividade no Confiar.

Pedido (29/09/2026): "quando formos aprovar ou não a ideia devemos selecionar
quem será o executor e qual será o prazo, após isso já aparece automaticamente
na atividade do executor".

O que este teste cobre:

- aprovar sem executor ou sem prazo não passa, e a ideia não muda de status;
- prazo no passado não passa;
- aprovar com os dois cria a atividade do executor, já valendo (sem uma segunda
  aprovação), com o prazo escolhido;
- a atividade nasce sem o nome de quem teve a ideia — a autoria continua sendo
  assunto de /impulso/inovar/adm/;
- a ideia guarda executor, prazo e a atividade criada;
- decidir de novo troca o dono da atividade em vez de abrir outra;
- arquivar não cria atividade nenhuma;
- a atividade aparece no Kanban do executor;
- quem não é gestor não decide;
- na tela: o formulário pede executor e prazo, e a ideia aprovada mostra para
  onde foi.

Roda num sqlite descartável (as colunas novas ainda não existem no Postgres
compartilhado). Nada sai para o banco de verdade.
"""
import os
import pathlib
import sys
import tempfile

import django

# Settings descartável: banco sqlite montado a partir dos models.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-inovar-'))
(PASTA / 'zz_inovar_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ino'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ino-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_inovar_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment
from django.utils import timezone

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from datetime import timedelta

from django.contrib.auth import get_user_model

from communications.models import CommunicationGroup
from core.models import Notification
from impulso.models import Ideia, Meta
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


loja = Sector.objects.create(name='ZZ Loja do Inovar')
gestor = User.objects.create_user(username='zzi.gestor', email='zzi.gestor@exemplo-teste.local',
                                  password='S3nha!teste', first_name='ZZI', last_name='Gestor',
                                  sector=loja, is_superuser=True, hierarchy='SUPERADMIN')
adm = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=gestor)
gestores = CommunicationGroup.objects.create(name='GESTORES (IMPULSO)', created_by=gestor)
autora = User.objects.create_user(username='zzi.autora', email='zzi.autora@exemplo-teste.local',
                                  password='S3nha!teste', first_name='ZZI', last_name='Autora',
                                  sector=loja)
executor = User.objects.create_user(username='zzi.exec', email='zzi.exec@exemplo-teste.local',
                                    password='S3nha!teste', first_name='ZZI', last_name='Executor',
                                    sector=loja)
outro = User.objects.create_user(username='zzi.outro', email='zzi.outro@exemplo-teste.local',
                                 password='S3nha!teste', first_name='ZZI', last_name='Outro',
                                 sector=loja)
for pessoa in (gestor, autora, executor, outro):
    pessoa.communication_groups.add(adm)
gestor.communication_groups.add(gestores)

ideia = Ideia.objects.create(
    autor=autora, descricao='ZZ Trocar a fila do caixa por senha eletrônica na Loja Centro',
    setor_impacto='Atendimento', motivo='ZZ A fila dobra na sexta e ninguém sabe a ordem')

c_gestor = Client(); c_gestor.force_login(gestor)
c_autora = Client(); c_autora.force_login(autora)
c_exec = Client(); c_exec.force_login(executor)

hoje = timezone.localdate()
url = f'/impulso/inovar/{ideia.id}/status/'

print('== APROVAR EXIGE EXECUTOR E PRAZO ==')
c_gestor.post(url, {'status': 'APROVADA', 'resposta_gestor': 'ZZ boa ideia'}, follow=True)
ideia.refresh_from_db()
t('aprovar sem executor nem prazo não passa', ideia.status != Ideia.Status.APROVADA, ideia.status)
t('e nenhuma atividade foi criada', Meta.objects.count() == 0)

c_gestor.post(url, {'status': 'APROVADA', 'executor': str(executor.id)}, follow=True)
ideia.refresh_from_db()
t('executor sem prazo também não', ideia.status != Ideia.Status.APROVADA, ideia.status)

c_gestor.post(url, {'status': 'APROVADA', 'prazo': (hoje + timedelta(days=5)).isoformat()},
              follow=True)
ideia.refresh_from_db()
t('prazo sem executor também não', ideia.status != Ideia.Status.APROVADA, ideia.status)

c_gestor.post(url, {'status': 'APROVADA', 'executor': str(executor.id),
                    'prazo': (hoje - timedelta(days=1)).isoformat()}, follow=True)
ideia.refresh_from_db()
t('prazo que já passou não passa', ideia.status != Ideia.Status.APROVADA, ideia.status)
t('e nada foi criado por engano', Meta.objects.count() == 0)

print('\n== APROVAR CRIA A ATIVIDADE DO EXECUTOR ==')
prazo = hoje + timedelta(days=7)
Notification.objects.all().delete()
c_gestor.post(url, {'status': 'APROVADA', 'executor': str(executor.id),
                    'prazo': prazo.isoformat(),
                    'resposta_gestor': 'ZZ vamos testar na Centro primeiro'}, follow=True)
ideia.refresh_from_db()
t('a ideia fica aprovada', ideia.status == Ideia.Status.APROVADA)
t('com executor e prazo guardados',
  ideia.executor_id == executor.id and ideia.prazo == prazo, (ideia.executor_id, ideia.prazo))

meta = ideia.meta_gerada
t('virou atividade', meta is not None)
t('do executor', meta.colaborador_id == executor.id)
t('com quem aprovou como gestor da atividade', meta.gestor_id == gestor.id)
t('com o prazo combinado', meta.prazo == prazo)
t('já valendo, sem segunda aprovação', meta.aprovacao == Meta.Aprovacao.APROVADA)
t('e entrando na coluna A Fazer', meta.status == Meta.Status.A_FAZER)
t('o título diz de onde veio', meta.titulo.startswith('Ideia aprovada —'), meta.titulo)
t('e traz a ideia junto', 'senha eletrônica' in meta.titulo, meta.titulo)
t('a descrição leva o motivo e o retorno',
  'A fila dobra' in meta.descricao and 'testar na Centro' in meta.descricao, meta.descricao[:120])
t('mas não entrega quem teve a ideia',
  'Autora' not in meta.descricao and 'zzi.autora' not in meta.descricao, meta.descricao[:200])

avisos = list(Notification.objects.values_list('user_id', 'title'))
t('o executor é avisado',
  any(u == executor.id and 'ideia aprovada' in (titulo or '').lower() for u, titulo in avisos),
  avisos)
t('e o autor também', any(u == autora.id for u, _ in avisos), avisos)

print('\n== A ATIVIDADE APARECE PARA O EXECUTOR ==')
# O Kanban abre no mês corrente e corta pelo prazo — o teste pede o mês do
# prazo para não depender do dia em que roda.
html = c_exec.get(f'/impulso/metas/?mes={prazo:%Y-%m}').content.decode()
t('no Kanban de quem vai executar', 'Ideia aprovada' in html and 'senha eletrônica' in html)
t('na coluna A Fazer', 'A Fazer' in html)
r = c_exec.get(f'/impulso/metas/{meta.id}/')
t('e o detalhe abre para ele', r.status_code == 200, r.status_code)

print('\n== DECIDIR DE NOVO NÃO DUPLICA ==')
novo_prazo = hoje + timedelta(days=20)
c_gestor.post(url, {'status': 'APROVADA', 'executor': str(outro.id),
                    'prazo': novo_prazo.isoformat()}, follow=True)
ideia.refresh_from_db()
meta.refresh_from_db()
t('continua sendo uma atividade só', Meta.objects.count() == 1, Meta.objects.count())
t('que mudou de dono', meta.colaborador_id == outro.id)
t('e de prazo', meta.prazo == novo_prazo)
t('a ideia acompanha', ideia.executor_id == outro.id and ideia.meta_gerada_id == meta.id)

print('\n== ARQUIVAR NÃO CRIA ATIVIDADE ==')
outra = Ideia.objects.create(autor=autora, descricao='ZZ Ideia que não vai para frente',
                             setor_impacto='Estoque', motivo='ZZ motivo qualquer')
c_gestor.post(f'/impulso/inovar/{outra.id}/status/',
              {'status': 'ARQUIVADA', 'resposta_gestor': 'ZZ não é o momento'}, follow=True)
outra.refresh_from_db()
t('a ideia é arquivada', outra.status == Ideia.Status.ARQUIVADA)
t('sem criar atividade', Meta.objects.count() == 1 and outra.meta_gerada_id is None)
t('e sem executor pendurado', outra.executor_id is None and outra.prazo is None)

print('\n== QUEM DECIDE ==')
r = c_autora.post(url, {'status': 'ARQUIVADA'}, follow=True)
ideia.refresh_from_db()
t('colaborador não decide ideia', ideia.status == Ideia.Status.APROVADA, ideia.status)

print('\n== A TELA ==')
html = c_gestor.get('/impulso/inovar/').content.decode()
t('o formulário pede quem vai executar', 'name="executor"' in html)
t('e até quando', 'name="prazo"' in html)
t('com os campos aparecendo na aprovação', 'data-execucao' in html and 'data-status' in html)
t('a ideia aprovada mostra para onde foi',
  'Virou atividade' in html and (outro.get_full_name() in html))
t('com link para a atividade', f'/impulso/metas/{meta.id}/' in html)

html = c_autora.get('/impulso/inovar/').content.decode()
t('o autor vê que a ideia dele virou atividade', 'Virou atividade' in html)

print(f'\n{ok} OK / {fail} falhas')
print(f'(descartável em {PASTA})')
sys.exit(1 if fail else 0)
