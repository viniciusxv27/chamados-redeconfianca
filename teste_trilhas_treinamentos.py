"""Trilhas e Treinamentos num módulo só (04/11/2026). Banco descartável; nada sai para a rede.

O que fica preso aqui:

- um item único no menu, na ordem alfabética, e a busca do portal achando as duas partes;
- cada pessoa vê as abas da permissão dela, e a tela continua barrando quem não tem;
- todas as URLs antigas respondem, com os mesmos nomes e endereços;
- o bloqueio de trilha obrigatória não mudou, e abrir a entrada não grava progresso;
- progresso, certificado e assinatura seguem aparecendo.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-trilhas-treinos-'))
(PASTA / 'zz_trilhas_treinos_settings.py').write_text(f"""
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
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_trilhas_treinos_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import datetime as dt
import re
import unicodedata

import requests
from django.core.cache import caches
from django.urls import reverse
from django.utils import timezone

from cursos.middleware import LIBERADOS as LIBERADOS_CURSO
from django.contrib.auth.models import AnonymousUser
from knowledge_trails.bloqueio import LIBERADOS as LIBERADOS_TRILHA
from knowledge_trails.modulo import abas_de
from knowledge_trails.models import (Certificate, KnowledgeTrail, Lesson, LessonProgress, TrailModule,
                                     TrailProgress, TrailSignature)
from tangerino.middleware import LIBERADOS as LIBERADOS_FERIAS
from trainings.models import Training, TrainingCategory, TrainingView
from users import module_access as ma
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


def pessoa(username, hierarchy='PADRAO', sector=None, **kw):
    return User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
                                    first_name='ZZ', last_name=username, hierarchy=hierarchy, sector=sector, **kw)


def cliente(user):
    c = Client()
    c.force_login(user)
    return c


def norm(x):
    x = unicodedata.normalize('NFKD', x)
    return ''.join(c for c in x if not unicodedata.combining(c)).lower()


def abas(html):
    return re.findall(r'data-aba="(\w+)"', html)


def aba_ativa(html):
    return re.findall(r'data-aba="(\w+)"\s+aria-current="page"', html)


setor_a = Sector.objects.create(name='ZZ Setor A')
setor_b = Sector.objects.create(name='ZZ Setor B')
chefe = pessoa('zztt.chefe', 'SUPERADMIN', setor_a)
admin_ = pessoa('zztt.admin', 'ADMIN', setor_a)
supervisor = pessoa('zztt.sup', 'SUPERVISOR', setor_a)
administrativo = pessoa('zztt.adm', 'ADMINISTRATIVO', setor_a)
comum = pessoa('zztt.comum', 'PADRAO', setor_a)
liberado = pessoa('zztt.liberado', 'PADRAO', setor_b)
ma.set_user_modules(liberado, ['treinamentos.gestao'], granted_by=chefe)
liberado = User.objects.get(pk=liberado.pk)

trilha_a = KnowledgeTrail.objects.create(title='ZZ Trilha do setor A', description='d', sector=setor_a, created_by=chefe)
trilha_b = KnowledgeTrail.objects.create(title='ZZ Trilha do setor B', description='d', sector=setor_b, created_by=chefe)
trilha_off = KnowledgeTrail.objects.create(title='ZZ Trilha inativa', description='d', sector=setor_a, created_by=chefe, is_active=False)
categoria = TrainingCategory.objects.create(name='ZZ Categoria')
treino = Training.objects.create(title='ZZ Treinamento', description='d', category=categoria,
                                 uploaded_by=chefe, video_file='videos/zz.mp4', file_size=10)

cc, cl, cs, ca, cg, cx = (cliente(comum), cliente(liberado), cliente(supervisor), cliente(administrativo),
                          cliente(chefe), cliente(admin_))

print('== MENU: UM ITEM SÓ ==')
html = cc.get('/').content.decode()
i = html.index('id="colaborador-menu"')
bloco = html[i:html.index('</ul>', i)]
rotulos = [r.strip() for r in re.findall(r'<span(?: class="text-sm[^"]*")?>([^<{]+)</span>', bloco)]
t('tem "Trilhas e Treinamentos"', 'Trilhas e Treinamentos' in rotulos, rotulos)
t('os dois itens antigos saíram', 'Treinamentos' not in rotulos and 'Trilhas de Conhecimento' not in rotulos, rotulos)
t('a seção segue em ordem alfabética', rotulos == sorted(rotulos, key=norm), rotulos)
t('o item entra por /trilhas/ e responde pela rota antiga',
  'data-rota="/trilhas/" data-rotas-antigas="/trainings/"' in html)
t('comentário de template não vazou na tela', 'data-rotas-antigas segura' not in html)
t('quem não gere não recebe endereço de gestão no menu',
  '/trainings/manage/' not in html and '/trilhas/manage/' not in html and '/trilhas/create/' not in html)

print('\n== BUSCA DO PORTAL ==')
extras = html[html.index('var EXTRAS = ['):html.index('function montarIndice')]
t('acha as duas partes pelo nome antigo', "titulo: 'Trilhas de Conhecimento', url: '/trilhas/'" in extras
  and "titulo: 'Treinamentos', url: '/trainings/'" in extras)
t('a gestão de trilhas não aparece para quem não gere', 'Gerenciar trilhas' not in extras)
extras_sup = (lambda h: h[h.index('var EXTRAS = ['):h.index('function montarIndice')])(cs.get('/').content.decode())
t('e aparece para quem gere', "titulo: 'Gerenciar trilhas', url: '/trilhas/manage/'" in extras_sup
  and "titulo: 'Nova trilha', url: '/trilhas/create/'" in extras_sup)

print('\n== ABAS POR PERMISSÃO ==')
VER = ['trilhas', 'treinamentos']
GT = 'gerir_trilhas'
GR = 'gerir_treinamentos'
esperado = [
    ('colaborador comum', cc, VER),
    ('PADRÃO com treinamentos.gestao', cl, VER + [GR]),
    ('ADMINISTRATIVO (gere treinamento, não trilha)', ca, VER + [GR]),
    ('SUPERVISOR', cs, VER + [GT, GR]),
    ('ADMIN', cx, VER + [GT, GR]),
    ('SUPERADMIN', cg, VER + [GT, GR]),
]
for rotulo, c, abas_certas in esperado:
    for url in ('/trilhas/', '/trainings/'):
        h = c.get(url).content.decode()
        t(f'{rotulo} em {url}: {abas_certas}', abas(h) == abas_certas, abas(h))

anonimo = Client()
r = anonimo.get('/trainings/')
t('sem login, /trainings/ segue sem desenhar o miolo (o base.html só o mostra logado)',
  r.status_code == 200 and not abas(r.content.decode()) and treino.title not in r.content.decode())
t('e a regra das abas não oferece trilha a quem não entrou', [a['chave'] for a in abas_de(AnonymousUser())] == ['treinamentos'])
t('e /trilhas/ continua pedindo login', anonimo.get('/trilhas/').status_code == 302)

print('\n== A ABA ATIVA ==')
telas = [
    (cc, '/trilhas/', 'trilhas'),
    (cc, f'/trilhas/trail/{trilha_a.pk}/', 'trilhas'),
    (cc, '/trainings/', 'treinamentos'),
    (cc, f'/trainings/{treino.pk}/', 'treinamentos'),
    (cg, '/trilhas/manage/', GT),
    (cg, f'/trilhas/manage/{trilha_a.pk}/', GT),
    (cg, '/trilhas/create/', GT),
    (cg, f'/trilhas/edit/{trilha_a.pk}/', GT),
    (cg, f'/trilhas/manage/{trilha_a.pk}/edit-map/', GT),
    (cg, f'/trilhas/trail/{trilha_a.pk}/create-module/', GT),
    (cg, '/trainings/manage/', GR),
    (cg, '/trainings/upload/', GR),
    (cg, '/trainings/categories/manage/', GR),
]
for c, url, chave in telas:
    r = c.get(url)
    h = r.content.decode()
    t(f'{url} abre com a aba {chave}', r.status_code == 200 and aba_ativa(h) == [chave], (r.status_code, aba_ativa(h)))
    t(f'{url} sem comentário vazado', '{#' not in h and '{%' not in h)

print('\n== A TELA CONTINUA SENDO A PORTA ==')
for rotulo, c in (('comum', cc), ('liberado só de treinamento', cl), ('ADMINISTRATIVO', ca)):
    r = c.get('/trilhas/manage/')
    t(f'{rotulo} não entra em Gerenciar trilhas', r.status_code == 302 and r['Location'] == '/trilhas/', r.status_code)
    r = c.get('/trilhas/create/')
    t(f'{rotulo} não cria trilha', r.status_code == 302, r.status_code)
for url in ('/trainings/manage/', '/trainings/upload/', '/trainings/categories/manage/'):
    t(f'comum não entra em {url}', cc.get(url).status_code == 302)
    t(f'liberado entra em {url}', cl.get(url).status_code == 200)
h = cl.get('/trainings/').content.decode()
t('os botões da lista obedecem à liberação individual', '/trainings/upload/' in h and '/trainings/categories/manage/' in h)
h = cc.get('/trainings/').content.decode()
t('e somem para quem não tem', '/trainings/upload/' not in h and '/trainings/manage/' not in h)

print('\n== GERENCIAR TRILHAS ==')
h = cg.get('/trilhas/manage/').content.decode()
t('SUPERADMIN vê todas, inclusive a inativa',
  all(x.title in h for x in (trilha_a, trilha_b, trilha_off)))
h = cs.get('/trilhas/manage/').content.decode()
t('SUPERVISOR vê as do setor dele', trilha_a.title in h and trilha_off.title in h)
t('e não as de outro setor', trilha_b.title not in h)
t('a regra da trilha segue valendo para o SUPERVISOR',
  cs.get(f'/trilhas/manage/{trilha_b.pk}/').status_code == 302 and cs.get(f'/trilhas/manage/{trilha_a.pk}/').status_code == 200)
h = cg.get('/trilhas/manage/', {'status': 'inactive'}).content.decode()
t('filtra por situação', trilha_off.title in h and trilha_a.title not in h)
h = cg.get('/trilhas/manage/', {'search': 'setor B'}).content.decode()
t('filtra por título', trilha_b.title in h and trilha_a.title not in h)
t('filtro sem resultado avisa', 'Nenhuma trilha com esses filtros' in cg.get('/trilhas/manage/', {'search': 'xyznada'}).content.decode())
TrailProgress.objects.create(trail=trilha_b, user=comum, status='completed')
TrailProgress.objects.create(trail=trilha_b, user=supervisor, status='in_progress')
TrailProgress.objects.create(trail=trilha_b, user=liberado, status='not_started')
h = cg.get('/trilhas/manage/', {'search': 'setor B'}).content.decode()
t('conta quem iniciou e quem concluiu, sem somar "não iniciado"', '2 iniciaram' in h and '1 concluíram' in h, h[-200:])
TrailProgress.objects.filter(trail=trilha_b).delete()

print('\n== TODAS AS URLS ANTIGAS ==')
antigas = {
    'knowledge_trails:dashboard': ([], '/trilhas/'),
    'knowledge_trails:trail_detail': ([1], '/trilhas/trail/1/'),
    'knowledge_trails:trail_blocked': ([1], '/trilhas/trail/1/bloqueada/'),
    'knowledge_trails:trail_sign': ([1], '/trilhas/trail/1/assinar/'),
    'knowledge_trails:lesson_view': ([1], '/trilhas/lesson/1/'),
    'knowledge_trails:leaderboard': ([1], '/trilhas/trail/1/leaderboard/'),
    'knowledge_trails:certificate_view': ([1], '/trilhas/certificate/1/'),
    'knowledge_trails:download_certificate': ([1], '/trilhas/certificate/1/download/'),
    'knowledge_trails:manage_trail': ([1], '/trilhas/manage/1/'),
    'knowledge_trails:edit_trail_map': ([1], '/trilhas/manage/1/edit-map/'),
    'knowledge_trails:create_trail': ([], '/trilhas/create/'),
    'knowledge_trails:edit_trail': ([1], '/trilhas/edit/1/'),
    'knowledge_trails:delete_trail': ([1], '/trilhas/delete/1/'),
    'knowledge_trails:create_module': ([1], '/trilhas/trail/1/create-module/'),
    'knowledge_trails:edit_module': ([1], '/trilhas/module/1/edit/'),
    'knowledge_trails:delete_module': ([1], '/trilhas/module/1/delete/'),
    'knowledge_trails:create_lesson': ([1], '/trilhas/module/1/create-lesson/'),
    'knowledge_trails:edit_lesson': ([1], '/trilhas/lesson/1/edit/'),
    'knowledge_trails:delete_lesson': ([1], '/trilhas/lesson/1/delete/'),
    'knowledge_trails:edit_lesson_quiz': ([1], '/trilhas/lesson/1/quiz/'),
    'knowledge_trails:create_quiz_question': ([1], '/trilhas/lesson/1/quiz/create-question/'),
    'knowledge_trails:quiz_answers': ([1], '/trilhas/lesson/1/quiz/answers/'),
    'knowledge_trails:user_quiz_detail': ([1, 2], '/trilhas/lesson/1/quiz/user/2/'),
    'knowledge_trails:edit_quiz_question': ([1], '/trilhas/quiz/question/1/edit/'),
    'knowledge_trails:delete_quiz_question': ([1], '/trilhas/quiz/question/1/delete/'),
    'trainings_list': ([], '/trainings/'),
    'training_detail': ([1], '/trainings/1/'),
    'mark_training_completed': ([1], '/trainings/1/mark-completed/'),
    'training_upload': ([], '/trainings/upload/'),
    'training_video_iniciar': ([], '/trainings/upload/video/iniciar/'),
    'training_video_concluir': ([], '/trainings/upload/video/concluir/'),
    'training_video_cancelar': ([], '/trainings/upload/video/cancelar/'),
    'trainings_manage': ([], '/trainings/manage/'),
    'training_toggle_status': ([1], '/trainings/1/toggle-status/'),
    'training_delete': ([1], '/trainings/1/delete/'),
    'manage_training_categories': ([], '/trainings/categories/manage/'),
    'edit_training_category': ([1], '/trainings/categories/1/edit/'),
    'update_training_progress': ([], '/trainings/api/update-progress/'),
}
erradas = {n: reverse(n, args=a) for n, (a, u) in antigas.items() if reverse(n, args=a) != u}
t(f'as {len(antigas)} rotas mantêm nome e endereço', not erradas, erradas)
h = cg.get(reverse('training_delete', args=[treino.pk])).content.decode()
t('a confirmação de exclusão de treinamento abre', 'Excluir' in h)

print('\n== BLOQUEIO DE TRILHA OBRIGATÓRIA ==')
t('as listas de liberados dos middlewares não mudaram',
  '/trilhas/' in LIBERADOS_TRILHA and '/trainings/' not in LIBERADOS_TRILHA
  and '/trilhas/' not in LIBERADOS_CURSO and '/trilhas/' not in LIBERADOS_FERIAS)
ontem = timezone.localdate() - dt.timedelta(days=1)
travada = KnowledgeTrail.objects.create(
    title='ZZ Trilha que trava', description='d', sector=setor_a, created_by=chefe, blocks_portal=True,
    mandatory_start_date=ontem - dt.timedelta(days=5), mandatory_end_date=ontem)
presa = pessoa('zztt.presa', 'PADRAO', setor_a)
travada.mandatory_users.add(presa)
cp = cliente(presa)
caches['local'].clear()
destino = f'/trilhas/trail/{travada.pk}/bloqueada/'
r = cp.get('/')
t('quem está pendente é levado à tela do bloqueio', r.status_code == 302 and r['Location'] == destino, r)
t('a entrada do módulo continua aberta para ela', cp.get('/trilhas/').status_code == 200)
r = cp.get('/trainings/')
t('e a aba de treinamentos leva ao bloqueio, como antes', r.status_code == 302 and r['Location'] == destino, r)
h = cp.get(destino).content.decode()
t('a tela do bloqueio não ganhou abas (não é hora de passear)', not abas(h) and 'trilha obrigatória pendente' in h)

print('\n== DADOS CONTINUAM VÁLIDOS ==')
visitante = pessoa('zztt.visita', 'PADRAO', setor_a)
cv = cliente(visitante)
cv.get('/trilhas/')
t('abrir a entrada do módulo não grava progresso',
  not TrailProgress.objects.filter(user=visitante).exists())
modulo = TrailModule.objects.create(trail=trilha_a, title='ZZ Módulo', description='d', order=1)
licao = Lesson.objects.create(module=modulo, title='ZZ Lição', description='d', order=1, lesson_type='text', points=10)
progresso = TrailProgress.objects.create(trail=trilha_a, user=visitante, status='completed', total_points_earned=10)
LessonProgress.objects.create(lesson=licao, user=visitante, completed=True)
certificado = Certificate.objects.create(trail_progress=progresso, user=visitante, trail=trilha_a)
KnowledgeTrail.objects.filter(pk=trilha_a.pk).update(require_signature=True)
TrailSignature.objects.create(trail=trilha_a, user=visitante, typed_name=visitante.get_full_name(), declaration='ZZ')
h = cv.get('/trilhas/').content.decode()
t('o progresso da lição aparece', '100%' in h and 'Revisar' in h)
t('os pontos e os contadores aparecem', re.search(r'>\s*10\s*</h3>\s*<p[^>]*>Pontos Totais', h) is not None)
t('o certificado segue listado', f'/trilhas/certificate/{certificado.pk}/' in h)
h = cv.get(f'/trilhas/certificate/{certificado.pk}/').content.decode()
t('e abre, com o link de baixar', f'/trilhas/certificate/{certificado.pk}/download/' in h)
t('a assinatura segue na trilha', 'Assinada por' in cv.get(f'/trilhas/trail/{trilha_a.pk}/').content.decode())
t('abrir a trilha continua criando o progresso de quem começa',
  (cc.get(f'/trilhas/trail/{trilha_a.pk}/').status_code == 200
   and TrailProgress.objects.filter(user=comum, trail=trilha_a).exists()))
TrainingView.objects.create(training=treino, user=visitante, completed=True)
h = cv.get('/trainings/').content.decode()
t('o histórico de treinamentos aparece', re.search(r'Concluído:</span>\s*<span[^>]*>1<', h) is not None)

print('\n== SEM MUDANÇA DE BANCO ==')
import subprocess
mudou = subprocess.run(['git', 'status', '--short', '--', 'knowledge_trails/models.py', 'knowledge_trails/migrations',
                        'trainings/models.py', 'trainings/migrations'], capture_output=True, text=True).stdout.strip()
t('models e migrações dos dois apps intactos', not mudou, mudou)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
