"""/vendas/orcamentos/ (03/11/2026): orçamento direto para o cliente no WhatsApp.

Cobre o modelo da mensagem, a validação, o envio (com o WhatsApp falso), a falha que fica para
tentar de novo, o clique duplo que não manda duas vezes e quem envia e quem vê o quê.

Roda num sqlite descartável; nenhuma mensagem sai.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-orcamentos-'))
(PASTA / 'zz_orcamentos_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-orc'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-orc-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_orcamentos_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import datetime

import requests
from django.utils import timezone

from core import evolution
from users.models import Sector
from vendas.models import AcessoVendas, Orcamento

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

enviados, resposta = [], [(True, 'ok')]


def whatsapp_falso(numero, texto, **k):
    enviados.append((numero, texto))
    return resposta[0]


mock.patch.object(evolution, 'enviar_texto', whatsapp_falso).start()
loja = Sector.objects.create(name='Loja Glória')
esc = Sector.objects.create(name='Financeiro')


def usuario(nome, chaves=None, hierarquia='PADRAO', setor=loja):
    u = User.objects.create_user(username=f'zzo.{nome}', email=f'zzo.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZO', last_name=nome.upper(), sector=setor, hierarchy=hierarquia)
    if chaves:
        AcessoVendas.objects.create(user=u, permissoes=chaves, ativo=True)
    c = Client(); c.force_login(u)
    return u, c


vend, cv = usuario('vendedor', ['modulo.ver', 'vendas.lancar'])
outro, co = usuario('outro', ['modulo.ver', 'vendas.lancar'])
rede, cr = usuario('rede', ['modulo.ver', 'rede.ver'], setor=esc)
so_ver, csv_ = usuario('sover', ['modulo.ver'])
URL = '/vendas/orcamentos/'


def campos(**extra):
    d = {'cliente_nome': 'Maria Souza', 'pilar': 'SMARTPHONES', 'numero': '(27) 99876-5432', 'texto': 'Galaxy A15: R$ 1.300,00 em 10x'}
    d.update(extra)
    return d


print('Acesso')
t('só ver o módulo não abre', csv_.get(URL).status_code == 302 and 'href="/vendas/orcamentos/"' not in csv_.get('/vendas/').content.decode())
h = cv.get(URL).content.decode()
t('quem lança vê a aba e o formulário', 'href="/vendas/orcamentos/"' in h and 'name="cliente_nome"' in h and 'name="pilar"' in h and 'name="numero"' in h and 'name="texto"' in h)
t('os pilares estão todos', all(p in h for p in ('Móvel', 'Fixa', 'Smartphones', 'Eletrônicos', 'Essenciais', 'Seguros', 'SVA')))
t('a prévia já traz vendedor e loja', 'Zzo Vendedor' in h and 'Rede Confian' in h and 'Loja Gl' in h)
h = cr.get(URL).content.decode()
t('quem só vê a rede não tem formulário', 'name="cliente_nome"' not in h)
r = cr.post(URL, campos())
t('e o POST não envia', not enviados and not Orcamento.objects.exists())

print('Validação')
for nome_caso, extra, msg in (('sem nome', {'cliente_nome': '  '}, 'nome do cliente'), ('sem pilar', {'pilar': ''}, 'pilar'),
                              ('pilar inventado', {'pilar': 'XPTO'}, 'pilar'), ('sem texto', {'texto': ' '}, 'Escreva'),
                              ('texto gigante', {'texto': 'x' * 2001}, 'passa de'), ('sem DDD', {'numero': '99876-5432'}, 'Número'),
                              ('número fictício', {'numero': '(27) 99999-9999'}, 'Número')):
    h = cv.post(URL, campos(**extra)).content.decode()
    t(f'{nome_caso}: recusa e não envia', msg in h and not enviados and not Orcamento.objects.exists())
h = cv.post(URL, campos(numero='123')).content.decode()
t('o que foi digitado continua no formulário', 'Maria Souza' in h and 'Galaxy A15' in h)

print('Envio')
r = cv.post(URL, campos(), follow=True)
o = Orcamento.objects.get()
t('sai uma mensagem, para o número com DDI', len(enviados) == 1 and enviados[0][0] == '5527998765432', enviados)
m = enviados[0][1]
t('modelo: cliente, vendedor, loja, pilar e texto', 'Olá, *Maria Souza*!' in m and '*Zzo Vendedor*' in m and 'Rede Confiança · Loja Glória' in m
  and '*Orçamento · Smartphones*' in m and 'Galaxy A15: R$ 1.300,00 em 10x' in m, m)
t('registro completo', o.status == 'ENVIADO' and o.vendedor == vend and o.loja == loja and o.numero == '5527998765432' and o.enviado_em
  and o.tentativas == 1 and o.mensagem == m and o.pilar == 'SMARTPHONES')
t('avisa e lista', 'Orçamento enviado para Maria Souza' in r.content.decode() and '(27) 99876-5432' in r.content.decode())
cv.post(URL, campos())
t('clique duplo não manda de novo', len(enviados) == 1 and Orcamento.objects.count() == 1)
Orcamento.objects.update(criado_em=timezone.now() - datetime.timedelta(minutes=3))
cv.post(URL, campos())
t('passados 2 minutos, manda (pode ser de propósito)', len(enviados) == 2)
cv.post(URL, campos(texto='Outro orçamento'))
t('texto diferente manda', len(enviados) == 3)
n = len(enviados)
cv.post(URL, campos(cliente_nome='{texto} *x*', texto='<script>alert(1)</script>', numero='27998765433'))
h = cv.get(URL).content.decode()
t('texto do vendedor é escapado na lista', '<script>alert(1)</script>' not in h and '&lt;script&gt;' in h)

print('Falha e nova tentativa')
Orcamento.objects.all().delete(); enviados.clear()
resposta[0] = (False, 'HTTP 500 no WhatsApp')
r = cv.post(URL, campos(), follow=True)
o = Orcamento.objects.get()
h = r.content.decode()
t('falha: fica registrada e o vendedor vê o motivo', o.status == 'FALHA' and 'HTTP 500' in o.erro and 'Não consegui enviar' in h and 'Tentar de novo' in h)
resposta[0] = (True, 'ok')
cv.post(URL, campos())
t('depois de falhar, pode enviar de novo pelo formulário', Orcamento.objects.filter(status='ENVIADO').count() == 1)
Orcamento.objects.filter(status='ENVIADO').delete()
enviados.clear()
t('outro vendedor não reenvia o que não é dele', co.post(f'{URL}{o.pk}/reenviar/').status_code == 404 and not enviados)
t('quem só vê a rede não reenvia', cr.post(f'{URL}{o.pk}/reenviar/').status_code == 302 and not enviados)
cv.post(f'{URL}{o.pk}/reenviar/')
o.refresh_from_db()
t('tentar de novo envia e marca', len(enviados) == 1 and o.status == 'ENVIADO' and o.erro == '' and o.tentativas == 2)
cv.post(f'{URL}{o.pk}/reenviar/')
t('já enviado não reenvia', len(enviados) == 1)
with mock.patch.object(evolution, 'enviar_texto', side_effect=OSError('rede caiu')):
    r = cv.post(URL, campos(texto='mais um'))
t('WhatsApp que explode vira falha, não erro 500', r.status_code == 302 and Orcamento.objects.filter(status='FALHA', erro__contains='rede caiu').exists())

print('Quem vê o quê')
t('vendedor vê os dele', 'Maria Souza' in cv.get(URL).content.decode())
t('outro vendedor não vê', 'Maria Souza' not in co.get(URL).content.decode())
t('quem vê a rede vê todos', 'Maria Souza' in cr.get(URL).content.decode())
t('busca por nome e número', 'Maria Souza' in cv.get(URL, {'q': 'maria'}).content.decode()
  and 'Maria Souza' in cv.get(URL, {'q': '99876'}).content.decode() and 'Maria Souza' not in cv.get(URL, {'q': 'joao'}).content.decode())

print(f'\n{ok} OK, {fail} falha(s)')
