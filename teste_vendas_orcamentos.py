"""/orcamentos/ (03/11/2026; saiu de /vendas/orcamentos/ em 06/11/2026): orçamento direto para o cliente no WhatsApp.

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
so_ver, csv_ = usuario('sover')                 # lotado na loja, sem acesso a Vendas
URL = '/orcamentos/'


def campos(**extra):
    d = {'cliente_nome': 'Maria Souza', 'pilar': 'SMARTPHONES', 'numero': '(27) 99876-5432', 'texto': 'Galaxy A15: R$ 1.300,00 em 10x'}
    d.update(extra)
    return d


print('Acesso')
h = csv_.get(URL).content.decode()
t('lotado em loja usa, mesmo sem acesso a Vendas', 'name="cliente_nome"' in h)
t('Vendas não tem mais a aba', 'href="/vendas/orcamentos/"' not in cv.get('/vendas/').content.decode())
r = cv.get('/vendas/orcamentos/')
t('o endereço antigo leva ao módulo novo', r.status_code == 302 and r['Location'] == URL)
h = cv.get(URL).content.decode()
t('o menu e o formulário', f'href="{URL}"' in h and 'name="cliente_nome"' in h and 'name="pilar"' in h and 'name="numero"' in h and 'name="texto"' in h)
t('os pilares estão todos', all(p in h for p in ('Móvel', 'Fixa', 'Smartphones', 'Eletrônicos', 'Essenciais', 'Seguros', 'SVA')))
t('a prévia já traz vendedor e loja', 'Zzo Vendedor' in h and 'Rede Confian' in h and 'Loja Gl' in h)
r = cr.get(URL)
t('fora de loja (sem liberação) não entra', r.status_code == 302 and f'href="{URL}"' not in cr.get('/vendas/').content.decode())
r = cr.post(URL, campos())
t('e o POST não grava', not enviados and not Orcamento.objects.exists())

print('Validação')
for nome_caso, extra, msg in (('sem nome', {'cliente_nome': '  '}, 'nome do cliente'), ('sem pilar', {'pilar': ''}, 'pilar'),
                              ('pilar inventado', {'pilar': 'XPTO'}, 'pilar'), ('sem texto', {'texto': ' '}, 'Escreva'),
                              ('texto gigante', {'texto': 'x' * 2001}, 'passa de'), ('sem DDD', {'numero': '99876-5432'}, 'Número'),
                              ('número fictício', {'numero': '(27) 99999-9999'}, 'Número')):
    h = cv.post(URL, campos(**extra)).content.decode()
    t(f'{nome_caso}: recusa e não envia', msg in h and not enviados and not Orcamento.objects.exists())
h = cv.post(URL, campos(numero='123')).content.decode()
t('o que foi digitado continua no formulário', 'Maria Souza' in h and 'Galaxy A15' in h)

print('Abre no WhatsApp Web do vendedor (06/11/2026)')
from urllib.parse import unquote
XHR = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
r = cv.post(URL, campos(), **XHR)
j = r.json()
o = Orcamento.objects.get()
m = o.mensagem
t('nada sai pelo número do portal', not enviados)
t('devolve o link do WhatsApp Web com número (DDI) e mensagem', j['ok'] and j['web'].startswith('https://web.whatsapp.com/send?phone=5527998765432&text=')
  and unquote(j['web'].split('&text=', 1)[1]) == m, j)
t('e o do aplicativo para o celular', j['app'].startswith('https://wa.me/5527998765432?text=') and unquote(j['app'].split('?text=', 1)[1]) == m)
t('modelo: cliente, vendedor, loja, pilar e texto', 'Olá, *Maria Souza*!' in m and '*Zzo Vendedor*' in m and 'Rede Confiança · Loja Glória' in m
  and '*Orçamento · Smartphones*' in m and 'Galaxy A15: R$ 1.300,00 em 10x' in m, m)
t('registro completo, como "aberto no WhatsApp"', o.status == 'ABERTO' and o.vendedor == vend and o.loja == loja and o.numero == '5527998765432'
  and o.pilar == 'SMARTPHONES' and o.tentativas == 0)
h = cv.get(URL).content.decode()
t('a lista mostra e oferece abrir de novo', 'Aberto no WhatsApp' in h and 'class="orc-abrir' in h and 'https://web.whatsapp.com/send?phone=5527998765432' in h)
t('a tela diz como funciona', 'Abrir no WhatsApp do cliente' in h and 'seu WhatsApp Web' in h and 'window.open' in h)
j2 = cv.post(URL, campos(), **XHR).json()
t('clique duplo reabre o mesmo, sem gravar outro', j2['id'] == o.pk and Orcamento.objects.count() == 1)
Orcamento.objects.update(criado_em=timezone.now() - datetime.timedelta(minutes=3))
cv.post(URL, campos(), **XHR)
t('passados 2 minutos, grava outro (pode ser de propósito)', Orcamento.objects.count() == 2)
r = cv.post(URL, campos(numero='(27) 99999-9999'), **XHR)
t('erro volta em JSON para a tela mostrar', r.status_code == 400 and not r.json()['ok'] and any('Número' in e for e in r.json()['erros']))
r = cv.post(URL, campos(texto='sem javascript'), follow=True)
t('sem JavaScript: grava e manda usar o botão da lista', 'Use &quot;Abrir no WhatsApp&quot;' in r.content.decode() and Orcamento.objects.filter(texto='sem javascript').exists())
cv.post(URL, campos(cliente_nome='{texto} *x*', texto='<script>alert(1)</script>', numero='27998765433'), **XHR)
h = cv.get(URL).content.decode()
t('texto do vendedor é escapado na lista', '<script>alert(1)</script>' not in h and '&lt;script&gt;' in h)
r = cv.post(f'{URL}{o.pk}/abrir/')
t('"abrir de novo" leva à conversa, sem mandar pelo portal', r.status_code == 302 and r['Location'].startswith('https://web.whatsapp.com/send') and not enviados)
t('outro vendedor não abre o que não é dele', co.post(f'{URL}{o.pk}/abrir/').status_code == 404)
t('fora de loja não abre', cr.post(f'{URL}{o.pk}/abrir/').status_code == 404 and not enviados)
r = cv.post(f'/vendas/orcamentos/{o.pk}/reenviar/')
t('o antigo "tentar de novo" leva ao módulo', r.status_code == 302 and r['Location'] == URL)
antes = Orcamento.objects.count()
r = cr.post(URL, campos(), **XHR)
t('fora de loja não grava pelo fetch', r.status_code == 302 and Orcamento.objects.count() == antes)

print('Quem vê o quê')
t('vendedor vê os dele', 'Maria Souza' in cv.get(URL).content.decode())
t('outro vendedor não vê', 'Maria Souza' not in co.get(URL).content.decode())
t('a lista é só dos próprios (o geral fica no painel)', 'Maria Souza' not in csv_.get(URL).content.decode())
t('busca por nome e número', 'Maria Souza' in cv.get(URL, {'q': 'maria'}).content.decode()
  and 'Maria Souza' in cv.get(URL, {'q': '99876'}).content.decode() and 'Maria Souza' not in cv.get(URL, {'q': 'joao'}).content.decode())

print(f'\n{ok} OK, {fail} falha(s)')
