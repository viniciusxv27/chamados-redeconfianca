"""Acompanhamento do Impulso: "Vídeos e POPs" mostra item a item o que entrou na conta.

Pedido (27/10/2026): /impulso/acompanhamento/146/ dizia 4 POPs e a pessoa só via 3 no mês,
todos concluídos. A conta junta POP e vídeo, conta o conteúdo "para toda a equipe" (sem
destinatário) e só pontua o que o gestor aprovou; a lista do Conectar filtrada pela pessoa
escondia justamente o conteúdo "para toda a equipe".

Cobre:
- o detalhamento lista cada item (tipo, título, "para toda a equipe", situação);
- o texto da linha separa aprovado, aguardando conferência e POP × vídeo;
- POP com período fora do mês não entra;
- a página do acompanhamento mostra os itens;
- a lista do Conectar filtrada pela pessoa mostra o conteúdo "para toda a equipe";
- o detalhamento cabe num JSONField (fechamento do mês).
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-conectar-itens-'))
(PASTA / 'zz_conectar_itens_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-con'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-con-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_conectar_itens_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

from communications.models import CommunicationGroup
from impulso.models import ConclusaoConteudo, ConteudoConectar
from impulso.scoring import calcular_pontuacao, linhas_detalhadas
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


import datetime
import json

from django.utils import timezone

# ── cenário ────────────────────────────────────────────────────────────────
loja = Sector.objects.create(name='ZZ Loja Itens')
gestor = User.objects.create_user(username='zzi.gestor', email='zzi.gestor@exemplo-teste.local',
                                  password='S3nha!teste', first_name='ZZI', last_name='Gestor',
                                  sector=loja, is_superuser=True, hierarchy='SUPERADMIN')
ana = User.objects.create_user(username='zzi.ana', email='zzi.ana@exemplo-teste.local',
                               password='S3nha!teste', first_name='ZZI', last_name='Ana', sector=loja)
adm = CommunicationGroup.objects.create(name="ADM's LOJAS", created_by=gestor)
for pessoa in (ana, gestor):
    pessoa.communication_groups.add(adm)

hoje = timezone.localdate()
ini_mes = hoje.replace(day=1)
fim_mes = (ini_mes + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)


def conteudo(titulo, tipo='POP', para=None, inicio=ini_mes, fim=fim_mes):
    c = ConteudoConectar.objects.create(tipo=tipo, titulo=titulo, obrigatorio=True, ativo=True,
                                        inicio=inicio, fim=fim, criado_por=gestor)
    if para:
        c.obrigatorio_para.set(para)
    return c


def concluir(c, aprovacao='APROVADA'):
    ConclusaoConteudo.objects.create(conteudo=c, user=ana, concluido=True, concluido_em=timezone.now(),
                                     aprovacao=aprovacao)


p1, p2, p3 = (conteudo(f'ZZI POP {n}', para=[ana]) for n in (1, 2, 3))
todos = conteudo('ZZI POP da equipe toda')               # sem destinatário: vale para todos
video = conteudo('ZZI Vídeo do mês', tipo='VIDEO', para=[ana])
conteudo('ZZI POP do mês passado', para=[ana], inicio=ini_mes - datetime.timedelta(days=20),
         fim=ini_mes - datetime.timedelta(days=1))
for c in (p1, p2, p3):
    concluir(c)
concluir(video, 'PENDENTE')
bruno = User.objects.create_user(username='zzi.bruno', email='zzi.bruno@exemplo-teste.local',
                                 password='S3nha!teste', first_name='ZZI', last_name='Bruno', sector=loja)
bruno.communication_groups.add(adm)
conteudo('ZZI POP do Bruno', para=[bruno])

print('Detalhamento da pontuação')
dados = calcular_pontuacao(ana)
vp = dados['detalhes']['videos_pops']
t('conta 5 itens no mês (3 POPs dela + POP da equipe + vídeo)', vp['total'] == 5, vp)
t('3 aprovados, 1 aguardando', (vp['concluidos'], vp['aguardando']) == (3, 1), vp)
por_titulo = {i['titulo']: i for i in vp['itens']}
t('POP do mês passado fica de fora', 'ZZI POP do mês passado' not in por_titulo, list(por_titulo))
t('POP da equipe marcado "para toda a equipe" e não feito',
  por_titulo['ZZI POP da equipe toda']['para_todos'] and por_titulo['ZZI POP da equipe toda']['situacao'] == 'NAO_FEITO')
t('POP dela não é "para toda a equipe"', not por_titulo['ZZI POP 1']['para_todos'])
t('vídeo aparece como Vídeo, aguardando', (por_titulo['ZZI Vídeo do mês']['tipo'],
                                           por_titulo['ZZI Vídeo do mês']['situacao']) == ('Vídeo', 'PENDENTE'))
linha = next(l for l in linhas_detalhadas(dados) if l.get('itens') and any(i['tipo'] == 'Vídeo' for i in l['itens']))
t('texto da linha explica a conta', linha['info'] == '3 de 5 aprovado(s) · 1 aguardando conferência · 4 POP + 1 Vídeo',
  linha['info'])
try:
    json.dumps(dados['detalhes'], default=None)
    t('detalhamento cabe em JSON', True)
except TypeError as exc:
    t('detalhamento cabe em JSON', False, exc)

print('Tela do acompanhamento')
cli = Client()
cli.force_login(gestor)
r = cli.get(f'/impulso/acompanhamento/{ana.id}/')
html = r.content.decode()
t('abre (200)', r.status_code == 200, r.status_code)
t('lista o POP da equipe com o selo', 'ZZI POP da equipe toda' in html and 'para toda a equipe</span>' in html)
t('mostra "aguardando conferência" no vídeo', 'concluído · aguardando conferência' in html)
t('liga cada item ao conteúdo', f'/impulso/conectar/{todos.id}/' in html)

print('Conectar filtrado pela pessoa')
r = cli.get('/impulso/conectar/', {'q': 'ZZI Ana'})
html = r.content.decode()
t('abre (200)', r.status_code == 200, r.status_code)
t('mostra os POPs dela', 'ZZI POP 1' in html)
t('o filtro valeu (POP de outra pessoa some)', 'ZZI POP do Bruno' not in html)
t('mostra o POP "para toda a equipe"', 'ZZI POP da equipe toda' in html)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
