"""Entrevista de desligamento (28/10/2026) e janela de experiência marcada na criação.

Pedido: separar o tipo de saída do motivo real; opções "concorrente", "fixo" e "onboarding";
perguntar "o que te faria ficar?"; eliminar os registros duplicados; cobrir as lojas de SP.
Em /users/: todo usuário criado vem com "Se Há Janela de Experiencia" marcado.

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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-entrevista-'))
(PASTA / 'zz_entrevista_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ent'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ent-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_entrevista_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

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


import importlib
from datetime import date

from django.apps import apps as django_apps

from feedback import views as fv
from feedback.models import ExitInterviewParticipation, ExitInterviewRecording, ExitInterviewResponse

K = fv.EXIT_INTERVIEW_KEY
chefe = User.objects.create_user(username='zze.super', email='zze.super@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZE', last_name='Super', is_superuser=True, hierarchy='SUPERADMIN')
sp = Sector.objects.create(name='Loja Carapicuíba')
escr = Sector.objects.create(name='Ilha de Qualidade')
saindo = User.objects.create_user(username='zze.sai', email='zze.sai@exemplo-teste.local', password='S3nha!teste',
                                  first_name='ZZE', last_name='Saindo', sector=sp)
c = Client(); c.force_login(chefe)

print('Perguntas')
chaves = [q['key'] for q in fv._exit_questions()]
t('tipo de saída e motivo real são perguntas separadas', 'tipo_saida' in chaves and 'motivo_desligamento' in chaves)
motivo = next(q for q in fv._exit_questions() if q['key'] == 'motivo_desligamento')
t('motivo real tem Concorrente, Fixo e Onboarding', all(o in motivo['options'] for o in ('Concorrente', 'Fixo', 'Onboarding')))
t('"Fui desligado pela empresa" saiu do motivo', fv.EXIT_MOTIVO_ANTIGO_DESLIGADO not in motivo['options'])
t('pergunta "O que te faria ficar?"', any(q['label'] == 'O que te faria ficar?' for q in fv._exit_questions()))
h = c.get('/feedback/entrevista-desligamento/').content.decode()
t('o formulário mostra as novas perguntas', 'Qual foi o tipo de saída?' in h and 'O que te faria ficar?' in h)

print('Lojas de SP')
t('o setor de SP vira a loja da entrevista', fv._loja_da_entrevista(sp) == 'CARAPICUÍBA')
t('setor de escritório vira ESCRITÓRIO', fv._loja_da_entrevista(escr) == 'ESCRITÓRIO')
t('o formulário leva a loja do colaborador', f'value="{saindo.id}" data-loja="CARAPICUÍBA"' in h)


def respostas(**extra):
    dados = {'subject_id': saindo.id, 'dismissal_date': date.today().isoformat()}
    for q in fv._exit_questions():
        if q['type'] == 'scale':
            dados[q['key']] = '4'
        elif q['type'] == 'choice':
            dados[q['key']] = q['options'][0]
        else:
            dados[q['key']] = 'ZZE resposta'
    dados.pop('loja')
    dados.update(extra)
    return dados


print('Uma entrevista por pessoa')
c.post('/feedback/entrevista-desligamento/', respostas(tipo_saida='Pedido de demissão', motivo_desligamento='Concorrente'))
r1 = ExitInterviewResponse.objects.filter(user=saindo).first()
t('registra, com a loja vinda do setor', r1 and r1.answers['choice']['loja'] == 'CARAPICUÍBA'
  and r1.answers['choice']['tipo_saida'] == 'Pedido de demissão', r1 and r1.answers['choice'])
r = c.post('/feedback/entrevista-desligamento/', respostas(motivo_desligamento='Fixo'), follow=True)
t('enviar de novo atualiza, não duplica', ExitInterviewResponse.objects.filter(user=saindo).count() == 1
  and ExitInterviewResponse.objects.get(user=saindo).answers['choice']['motivo_desligamento'] == 'Fixo')
t('e a tela diz que atualizou', 'atualizada' in r.content.decode())

print('Duplicados antigos')
outro = User.objects.create_user(username='zze.dup', email='zze.dup@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZE', last_name='Dup', sector=sp)
igual = {'choice': {'motivo_desligamento': 'Questões pessoais'}, 'scale': {}, 'text': {}, 'scale_obs': {}}
a = ExitInterviewResponse.objects.create(survey_key=K, user=outro, answers=igual)
b = ExitInterviewResponse.objects.create(survey_key=K, user=outro, answers=igual)
com_video = ExitInterviewResponse.objects.create(survey_key=K, user=outro, answers=igual)
ExitInterviewRecording.objects.create(response=com_video, original_name='v.mp4')
dif = User.objects.create_user(username='zze.dif', email='zze.dif@exemplo-teste.local', password='S3nha!teste',
                               first_name='ZZE', last_name='Diferente')
d1 = ExitInterviewResponse.objects.create(survey_key=K, user=dif, answers={'choice': {'loja': 'GLÓRIA'}})
d2 = ExitInterviewResponse.objects.create(survey_key=K, user=dif, answers={'choice': {'loja': 'PIUMA'}})
mig = importlib.import_module('feedback.migrations.0015_entrevista_sem_duplicados')
mig.tirar_duplicadas(django_apps, None)
t('cópias idênticas somem e fica a com vídeo', list(ExitInterviewResponse.objects.filter(user=outro).values_list('id', flat=True))
  == [com_video.id])
t('duplicado com respostas diferentes fica', ExitInterviewResponse.objects.filter(user=dif).count() == 2)

print('Relatório com registros antigos')
velho = User.objects.create_user(username='zze.velho', email='zze.velho@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZE', last_name='Velho')
ExitInterviewResponse.objects.create(survey_key=K, user=velho, answers={'choice': {'motivo_desligamento': fv.EXIT_MOTIVO_ANTIGO_DESLIGADO}})
r = c.get('/feedback/entrevista-desligamento/relatorio/')
stats = {s['key']: {i['option']: i['count'] for i in s['items']} for s in r.context['choice_stats']}
t('"Fui desligado" antigo conta como Dispensa pela empresa', stats['tipo_saida'].get('Dispensa pela empresa') == 1, stats['tipo_saida'])
t('e o motivo real fica como não registrado', stats['motivo_desligamento'].get(fv.EXIT_MOTIVO_NAO_REGISTRADO) == 1
  and fv.EXIT_MOTIVO_ANTIGO_DESLIGADO not in stats['motivo_desligamento'], stats['motivo_desligamento'])
t('o que está gravado não muda', ExitInterviewResponse.objects.get(user=velho).answers['choice']['motivo_desligamento']
  == fv.EXIT_MOTIVO_ANTIGO_DESLIGADO)
t('o Excel sai com as perguntas novas', c.get('/feedback/entrevista-desligamento/relatorio/exportar/').status_code == 200)

print('Janela de experiência em /users/')
novo = User.objects.create_user(username='zze.novo', email='zze.novo@exemplo-teste.local', password='S3nha!teste')
t('usuário criado por qualquer caminho nasce com a janela marcada', novo.has_experience_window is True)
h = c.get('/users/manage/users/create/').content.decode()
i = h.find('name="has_experience_window"')
t('a tela de criação vem com a caixa marcada', i > 0 and 'checked' in h[i:i + 120], h[i:i + 120])
t('editar continua respeitando o desmarcado', (lambda u: (setattr(u, 'has_experience_window', False), u.save(),
                                                          User.objects.get(pk=u.pk).has_experience_window))(novo)[2] is False)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
