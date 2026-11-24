"""Rastro completo (/activity-history/rastro/): acessos gravados pelo middleware, varredura retroativa do banco,
filtro por dia e pessoas, exportação Excel/CSV e permissão (24/11/2026). Banco SQLite descartável; nada sai."""
import os
import pathlib
import sys
import tempfile
import types
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-rastro-'))
(PASTA / 'zz_rastro_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': r'{PASTA}/banco.sqlite3'}}}}


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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-rastro'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-rastro-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_rastro_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)


import io  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402

from bugs.models import RelatoBug  # noqa: E402
from core import rastro  # noqa: E402
from core.middleware import log_action  # noqa: E402
from core.models import AcessoPagina, Notification  # noqa: E402

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


def pessoa(login, hierarquia='PADRAO'):
    u = User.objects.create_user(username=login, email=f'{login}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZ', last_name=login, hierarchy=hierarquia)
    c = Client()
    c.force_login(u)
    return u, c


chefe, c_chefe = pessoa('zz.rastro.super', 'SUPERADMIN')
ana, c_ana = pessoa('zz.rastro.ana')
bia, c_bia = pessoa('zz.rastro.bia')

print('\n# Middleware: o que vira acesso')
AcessoPagina.objects.all().delete()
c_ana.get('/bugs/?f=todos', HTTP_SEC_FETCH_MODE='navigate', HTTP_USER_AGENT='Navegador ZZ')
c_ana.get('/bugs/', HTTP_SEC_FETCH_MODE='cors')                        # fetch/polling
c_ana.get('/bugs/', HTTP_X_REQUESTED_WITH='XMLHttpRequest', HTTP_ACCEPT='text/html')
c_ana.get('/bugs/', HTTP_ACCEPT='text/html,application/xhtml+xml')     # WebView sem Sec-Fetch
c_ana.post('/bugs/relatar/', {'url': '/x/', 'descricao': 'curto'})     # envio (mesmo recusado)
c_ana.get('/static/css/qualquer.css', HTTP_SEC_FETCH_MODE='navigate')
Client().get('/bugs/', HTTP_SEC_FETCH_MODE='navigate')                 # sem login
acessos = list(AcessoPagina.objects.order_by('id'))
t('grava navegação, WebView e envio; ignora fetch, XHR, estático e anônimo',
  [(a.metodo, a.caminho) for a in acessos] == [('GET', '/bugs/'), ('GET', '/bugs/'), ('POST', '/bugs/relatar/')],
  [(a.metodo, a.caminho) for a in acessos])
a = acessos[0]
t('guarda parâmetros, rota, status, tempo e navegador', a.consulta == 'f=todos' and a.rota == 'bugs:lista'
  and a.status == 200 and a.duracao_ms is not None and a.navegador == 'Navegador ZZ' and a.user_id == ana.pk,
  (a.consulta, a.rota, a.status))
t('o corpo do POST não é guardado', 'curto' not in (acessos[2].consulta + acessos[2].caminho))
with mock.patch('core.models.AcessoPagina.objects.create', side_effect=RuntimeError('banco fora')):
    t('falha ao gravar não derruba a página', c_ana.get('/bugs/', HTTP_SEC_FETCH_MODE='navigate').status_code == 200)

print('\n# Varredura retroativa')
hoje = timezone.localdate()
ontem = hoje - timedelta(days=1)
RelatoBug.objects.create(autor=ana, url='/vendas/', descricao='Total errado na venda')
antigo = RelatoBug.objects.create(autor=ana, url='/ontem/', descricao='de ontem')
RelatoBug.objects.filter(pk=antigo.pk).update(
    criado_em=timezone.now() - timedelta(days=1), atualizado_em=timezone.now() - timedelta(days=1))
RelatoBug.objects.create(autor=bia, url='/bia/', descricao='da bia')
Notification.objects.create(user=ana, title='Aviso ZZ', message='m', notification_type='SYSTEM')
log_action(ana, 'TICKET_CREATE', 'Chamado #1 criado')
t('catálogo acha a tabela de bugs pelo autor e pela data', any(
  m._meta.label_lower == 'bugs.relatobug' and 'autor_id' in [x for x, _y in v]
  for m, v, d, _t in rastro.catalogo()))
t('prazo e vencimento não contam como acontecimento', not rastro._e_data_de_evento(
  types.SimpleNamespace(name='prazo_em', auto_now=False, auto_now_add=False))
  and rastro._e_data_de_evento(types.SimpleNamespace(name='lido_em', auto_now=False, auto_now_add=False)))

eventos, usuarios, avisos = rastro.montar(hoje, hoje, [ana.pk])
bugs_ana = [e for e in eventos if e['fonte'] == 'banco' and 'Bug relatado' in e['o_que']]
t('o bug de hoje da Ana aparece uma vez (criado e atualizado juntos viram um), com o texto', len(bugs_ana) == 1
  and 'Total errado' in bugs_ana[0]['detalhe'], bugs_ana)
t('o de ontem fica fora do dia', not any('de ontem' in e['detalhe'] for e in eventos))
t('a Bia não aparece quando só a Ana foi escolhida', all(e['user_id'] == ana.pk for e in eventos))
t('as três fontes entram', {'paginas', 'acoes', 'banco'} <= {e['fonte'] for e in eventos}, {e['fonte'] for e in eventos})
notif = [e for e in eventos if 'Notificação' in e['o_que']]
t('notificação recebida sai como automática', notif and all(e['automatico'] for e in notif), notif)
t('vínculo diz o papel da pessoa na linha', bugs_ana and bugs_ana[0]['vinculo'].lower() == 'autor', bugs_ana)
t('em ordem de hora', [e['em'] for e in eventos] == sorted(e['em'] for e in eventos))
todos, _u, _a = rastro.montar(hoje, hoje)
t('sem escolher pessoa vem todo mundo', {ana.pk, bia.pk} <= {e['user_id'] for e in todos})
ev_ontem, _u, _a = rastro.montar(ontem, ontem, [ana.pk])
t('dia de ontem traz o bug de ontem', any('de ontem' in e['detalhe'] for e in ev_ontem))
de, ate = rastro.periodo({'de': '2026-01-01', 'ate': '2026-12-31'})
t('período longo é cortado em 31 dias', (ate - de).days == 30)

print('\n# Telas e exportação')
r = c_chefe.get(f'/activity-history/rastro/?dia={hoje:%Y-%m-%d}&u={ana.pk}')
h = r.content.decode()
t('SUPERADMIN abre o rastro do dia da Ana', r.status_code == 200 and 'Rastro completo' in h and 'zz.rastro.ana' in h
  and 'Bug relatado' in h, r.status_code)
t('colaborador não abre', c_ana.get('/activity-history/rastro/').status_code == 302
  and c_ana.get('/activity-history/rastro/exportar/').status_code == 302)
x = c_chefe.get(f'/activity-history/rastro/exportar/?dia={hoje:%Y-%m-%d}&u={ana.pk}&u={bia.pk}')
from openpyxl import load_workbook  # noqa: E402
wb = load_workbook(io.BytesIO(x.content))
linhas = list(wb['Movimentações'].iter_rows(values_only=True))
t('Excel com Movimentações, Resumo e Observações', wb.sheetnames == ['Movimentações', 'Resumo por pessoa', 'Observações']
  and linhas[0][:3] == ('Data', 'Hora', 'Pessoa'), wb.sheetnames)
t('Excel traz Ana e Bia e só elas', {l[2] for l in linhas[1:]} == {'ZZ zz.rastro.ana', 'ZZ zz.rastro.bia'},
  {l[2] for l in linhas[1:]})
t('nome do arquivo diz o dia e quantas pessoas', f'rastro_{hoje:%Y%m%d}_2pessoas.xlsx' in x['Content-Disposition'])
csvr = c_chefe.get(f'/activity-history/rastro/exportar/?dia={hoje:%Y-%m-%d}&u={ana.pk}&formato=csv&auto=0')
texto = csvr.content.decode('utf-8-sig')
t('CSV com cabeçalho e sem os automáticos quando pedido', texto.startswith('Data;Hora;Pessoa') and 'Aviso ZZ' not in texto
  and 'Automático' not in texto.split('\n', 1)[1], texto[:300])
so_paginas = c_chefe.get(f'/activity-history/rastro/?dia={hoje:%Y-%m-%d}&u={ana.pk}&fonte=paginas').content.decode()
t('filtro de fonte (só páginas)', 'Abriu a página' in so_paginas and 'Bug relatado' not in so_paginas)
t('histórico mostra o cartão do rastro', 'Rastro completo do dia' in c_chefe.get('/activity-history/').content.decode())

print('\n# Guarda')
velho = AcessoPagina.objects.create(user=ana, metodo='GET', caminho='/velho/')
AcessoPagina.objects.filter(pk=velho.pk).update(criado_em=timezone.now() - timedelta(days=500))
from core.rastro_middleware import limpar_antigos  # noqa: E402
t('limpeza diária apaga só o que passou do prazo', limpar_antigos() == 1 and AcessoPagina.objects.filter(caminho='/bugs/').exists())

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
