"""CONECTAR: o colaborador abrindo para ele mesmo em /impulso/conectar/novo/.

Pedido (18/11/2026): quem abre para si mesmo pode anexar documento e vídeo, o
conteúdo sai Obrigatório e Exige entrega, e o período vem pré-selecionado com o
mês inteiro atual.

Roda num sqlite descartável; nada toca o banco de verdade nem o MinIO.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-conectar-proprio-'))
(PASTA / 'zz_conectar_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_conectar_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from communications.models import CommunicationGroup
from core.models import Notification
from impulso import views
from impulso.models import ConclusaoConteudo, ConteudoConectar, EntregaConteudo
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


def arquivo(nome, tamanho=64, tipo='application/pdf'):
    return SimpleUploadedFile(nome, b'z' * tamanho, content_type=tipo)


def fetch(cliente, url, dados=None, arquivos=None):
    """POST como a tela faz: por fetch, esperando JSON."""
    carga = dict(dados or {})
    carga.update(arquivos or {})
    r = cliente.post(url, carga, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}



import datetime
from django.utils import timezone

hoje = timezone.localdate()
primeiro = hoje.replace(day=1)
ultimo = (primeiro + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)

loja = Sector.objects.create(name='ZZ Loja do Proprio')
ana = User.objects.create_user(username='zzp.ana', email='zzp.ana@exemplo-teste.local',
                               password='S3nha!teste', first_name='ZZP', last_name='Ana', sector=loja)
gestor = User.objects.create_user(username='zzp.gestor', email='zzp.gestor@exemplo-teste.local',
                                  password='S3nha!teste', first_name='ZZP', last_name='Gestor',
                                  sector=loja, is_superuser=True, hierarchy='SUPERADMIN')
adm = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=gestor)
gestores = CommunicationGroup.objects.create(name='GESTORES (IMPULSO)', created_by=gestor)
for pessoa in (ana, gestor):
    pessoa.communication_groups.add(adm)
gestor.communication_groups.add(gestores)

c_ana = Client(); c_ana.force_login(ana)
c_gestor = Client(); c_gestor.force_login(gestor)

print('== TELA DE QUEM ABRE PARA SI ==')
r = c_ana.get('/impulso/conectar/novo/')
html = r.content.decode()
t('abre a tela', r.status_code == 200, r.status_code)
t('tem campo de documento', 'name="arquivo"' in html)
t('tem campo de vídeo', 'name="video"' in html)
t('início vem com o 1º dia do mês', f'name="inicio" value="{primeiro:%Y-%m-%d}"' in html)
t('fim vem com o último dia do mês', f'name="fim" value="{ultimo:%Y-%m-%d}"' in html)
t('mostra obrigatório e exige entrega marcados', html.count('checked disabled') == 2, html.count('checked disabled'))
t('não oferece atribuir a outras pessoas', 'name="obrigatorio_para"' not in html)

print('\n== ENVIO COM DOCUMENTO E VÍDEO ==')
r = c_ana.post('/impulso/conectar/novo/', {
    'grupo': 'POP_VIDEO', 'titulo': 'ZZ POP da vitrine nova', 'descricao': 'Como montei',
    'arquivo': arquivo('pop.pdf'), 'video': arquivo('vitrine.mp4', tipo='video/mp4'),
    'inicio': f'{primeiro:%Y-%m-%d}', 'fim': f'{ultimo:%Y-%m-%d}',
})
c = ConteudoConectar.objects.filter(titulo='ZZ POP da vitrine nova').first()
t('cria o conteúdo e vai para o detalhe', r.status_code == 302 and c is not None, r.status_code)
if c:
    t('sai obrigatório', c.obrigatorio is True)
    t('sai exigindo entrega', c.exige_entrega is True)
    t('período = mês inteiro', (c.inicio, c.fim) == (primeiro, ultimo), (c.inicio, c.fim))
    t('guarda documento e vídeo', bool(c.arquivo) and bool(c.video), (c.arquivo, c.video))
    t('feito pela equipe e atrelado a ela', c.criado_por_equipe and list(c.obrigatorio_para.all()) == [ana])
    conc = ConclusaoConteudo.objects.filter(conteudo=c, user=ana).first()
    t('entrega já na fila de conferência', conc is not None and conc.concluido and not conc.aprovada,
      conc and (conc.concluido, conc.aprovada))

print('\n== ENVIO SEM PERÍODO NO POST (tela antiga/cache) ==')
c_ana.post('/impulso/conectar/novo/', {'grupo': 'POP_VIDEO', 'titulo': 'ZZ sem período',
                                       'url': 'https://exemplo.local/video'})
c = ConteudoConectar.objects.filter(titulo='ZZ sem período').first()
t('cai no mês inteiro atual', c is not None and (c.inicio, c.fim) == (primeiro, ultimo), c and (c.inicio, c.fim))
t('ainda obrigatório e exige entrega mesmo sem as caixas', c is not None and c.obrigatorio and c.exige_entrega)

print('\n== ENVIO SEM NADA ANEXADO ==')
antes = ConteudoConectar.objects.count()
r = c_ana.post('/impulso/conectar/novo/', {'grupo': 'POP_VIDEO', 'titulo': 'ZZ vazio'}, follow=True)
t('recusa e explica', ConteudoConectar.objects.count() == antes
  and 'Anexe o documento, o vídeo ou informe o link' in r.content.decode())

print('\n== FIM ANTES DO INÍCIO ==')
r = c_ana.post('/impulso/conectar/novo/', {'grupo': 'POP_VIDEO', 'titulo': 'ZZ invertido',
                                           'url': 'https://exemplo.local/x',
                                           'inicio': f'{ultimo:%Y-%m-%d}', 'fim': f'{primeiro:%Y-%m-%d}'}, follow=True)
t('recusa período invertido', not ConteudoConectar.objects.filter(titulo='ZZ invertido').exists())

print('\n== GESTOR ==')
r = c_gestor.get('/impulso/conectar/novo/')
html = r.content.decode()
t('gestor também vê o mês inteiro pré-selecionado', f'value="{primeiro:%Y-%m-%d}"' in html and f'value="{ultimo:%Y-%m-%d}"' in html)
c_gestor.post('/impulso/conectar/novo/', {'grupo': 'POP_VIDEO', 'titulo': 'ZZ do gestor',
                                          'url': 'https://exemplo.local/g'})
c = ConteudoConectar.objects.filter(titulo='ZZ do gestor').first()
t('gestor continua escolhendo (sem marcar = não obrigatório, sem entrega)',
  c is not None and not c.obrigatorio and not c.exige_entrega, c and (c.obrigatorio, c.exige_entrega))

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
