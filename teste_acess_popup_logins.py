"""Acess: popup "login com erro" (10/11/2026). Banco descartável; nada sai para a rede."""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco descartável: as tabelas novas só existem no Postgres compartilhado depois do migrate.
# O schema aqui sai direto dos models (`--run-syncdb`) — nada toca o banco de verdade nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-acess-popup-'))
(PASTA / 'zz_acess_popup_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-acpop'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-acpop-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_acess_popup_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import json
import re
from datetime import timedelta

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache, caches
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import resolve
from django.utils import timezone

from acess import popup_login as pl
from acess.models import (SISTEMAS, AcessoAcess, CampanhaLoginErro, ItemLoginErro, RespostaLoginErro,
                          Solicitacao)
from portal_popups.context_processors import portal_popup_gate
from portal_popups.models import PortalPopup
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

POOL = next(s for s in SISTEMAS if pl.e_pool(s))
loja_a = Sector.objects.create(name='Loja ZZ Alfa')
loja_b = Sector.objects.create(name='Loja ZZ Beta')


def pessoa(nome, setor=None, hier='PADRAO', **kw):
    return User.objects.create_user(username=f'zzp.{nome}', email=f'zzp.{nome}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=nome.title(), sector=setor, hierarchy=hier, **kw)


chefe = pessoa('chefe', hier='SUPERADMIN', is_superuser=True)
gestor = pessoa('gestor', loja_b, 'ADMIN')
AcessoAcess.objects.create(user=gestor, permissoes=['popups.gerenciar'])
outro_acess = pessoa('outroacess', loja_b, 'ADMIN')
AcessoAcess.objects.create(user=outro_acess, permissoes=['colaboradores.ver'])
vend_a = pessoa('venda', loja_a, 'PADRAO')
sup_a = pessoa('supa', loja_a, 'SUPERVISOR')
vend_b = pessoa('vendb', loja_b, 'PADRAO')
sem_setor = pessoa('semsetor', None, 'PADRAO')
# M2M de escritório com a loja A, mas setor principal B: não entra (vale o setor principal).
escritorio = pessoa('escritorio', loja_b, 'PADRAO')
escritorio.sectors.add(loja_a)


def cliente(u):
    c = Client()
    c.force_login(u)
    return c


def limpar_caches():
    cache.clear()
    caches['local'].clear()


rf = RequestFactory()


def gate(u, caminho='/'):
    req = rf.get(caminho)
    req.user = u
    req.resolver_match = resolve(caminho)
    return portal_popup_gate(req)


def pendente(u):
    return gate(u).get('acess_popup_login')


# ── permissões ───────────────────────────────────────────────────────────────
print('quem gerencia')
for u, esperado in ((chefe, 200), (gestor, 200), (outro_acess, 302), (vend_a, 302)):
    r = cliente(u).get('/acess/popups/')
    t(f'/acess/popups/ para {u.first_name}: {esperado}', r.status_code == esperado, r.status_code)
r = cliente(outro_acess).post('/acess/popups/novo/', {'titulo': 'X', 'ativo': 'on'})
t('sem popups.gerenciar não cria', r.status_code == 302 and not CampanhaLoginErro.objects.exists())
r = cliente(vend_a).post('/acess/popups/novo/', {'titulo': 'X', 'ativo': 'on'})
t('usuário comum não cria', not CampanhaLoginErro.objects.exists())
t('chave aparece na tela de Acessos', 'popups.gerenciar' in cliente(chefe).get('/acess/acessos/').content.decode())
t('aba Popups só para quem gerencia',
  '/acess/popups/' in cliente(gestor).get('/acess/').content.decode()
  and '/acess/popups/' not in cliente(outro_acess).get('/acess/').content.decode())

print('criar e editar')
cg = cliente(gestor)
r = cg.get('/acess/popups/novo/')
t('formulário abre', r.status_code == 200 and 'name="hierarquias"' in r.content.decode())
r = cg.post('/acess/popups/novo/', {'titulo': '', 'ativo': 'on'})
t('título obrigatório', r.status_code == 200 and not CampanhaLoginErro.objects.exists())
r = cg.post('/acess/popups/novo/', {'titulo': 'T', 'inicio': '2026-11-20T10:00', 'fim': '2026-11-19T10:00'})
t('fim antes do início recusado', r.status_code == 200 and not CampanhaLoginErro.objects.exists())
r = cg.post('/acess/popups/novo/', {'titulo': 'Logins da loja A', 'pergunta': 'Algum login com erro?', 'ativo': 'on',
                                    'setores': [loja_a.id], 'hierarquias': ['PADRAO', 'INVENTADA']})
camp = CampanhaLoginErro.objects.first()
t('campanha criada', camp is not None and r.status_code == 302, r.status_code)
t('setor e hierarquia gravados (hierarquia inválida descartada)',
  list(camp.setores.values_list('id', flat=True)) == [loja_a.id] and camp.hierarquias == ['PADRAO'])
t('criado_por', camp.criado_por_id == gestor.id)

print('público')
limpar_caches()
t('vendedor da loja A vê', pendente(vend_a) is not None and pendente(vend_a)['id'] == camp.id)
t('supervisor da loja A não vê (hierarquia)', pendente(sup_a) is None)
t('vendedor da loja B não vê (setor)', pendente(vend_b) is None)
t('sem setor não vê', pendente(sem_setor) is None)
t('M2M com a loja A mas setor principal B não vê', pendente(escritorio) is None)
t('fora da home não aparece', gate(vend_a, '/acess/').get('acess_popup_login') is None)
t('público na tela de respostas = 1', pl.publico_qs(camp).count() == 1)
p = pendente(vend_a)
t('sistemas da ficha no popup, com o Pool marcado',
  [s['nome'] for s in p['sistemas']] == SISTEMAS and [s['nome'] for s in p['sistemas'] if s['pool']] == [POOL])

# Página real da home
r = cliente(vend_a).get('/')
html = r.content.decode()
t('home traz o popup', r.status_code == 200 and 'acess-login-modal' in html and 'Logins da loja A' in html, r.status_code)
t('home de quem não está no público não traz', 'acess-login-modal' not in cliente(vend_b).get('/').content.decode())

print('popup geral vem primeiro')
pp = PortalPopup.objects.create(title='Comunicado geral', message='x', target_all=True)
limpar_caches()
g = gate(vend_a)
t('com popup de /popups/ pendente, o do Acess espera', g.get('portal_popup') == pp and not g.get('acess_popup_login'))
pp.mark_completed(vend_a)
g = gate(vend_a)
t('depois de concluir o geral, o do Acess aparece', g.get('portal_popup') is None and g.get('acess_popup_login'))
pp.delete()

print('custo do gate')
limpar_caches()
camp.ativo = False
camp.save()
gate(vend_a)
with CaptureQueriesContext(connection) as base:
    gate(vend_a)
camp.ativo = True
camp.save()
# Só os caches do popup (a decisão do ponto, que o gate já consultava antes, fica quente).
pl.limpar_cache_campanhas()
cache.delete(pl.chave_situacao(vend_a.id))
gate(chefe)  # aquece a lista de campanhas (vale para todos)
with CaptureQueriesContext(connection) as frio:
    gate(vend_a)
with CaptureQueriesContext(connection) as quente:
    gate(vend_a)
t(f'primeira vez da pessoa: no máximo 1 consulta a mais ({len(base)} → {len(frio)})', len(frio) - len(base) <= 1)
t(f'depois: nenhuma consulta a mais ({len(base)} → {len(quente)})', len(quente) - len(base) <= 0)
with CaptureQueriesContext(connection) as fora:
    gate(vend_a, '/acess/')
t('fora da home: nenhuma consulta do Acess', not any('acess_' in q['sql'] for q in fora.captured_queries))


def responder(u, corpo, pk=None):
    return cliente(u).post(f'/acess/popup-login/{pk or camp.id}/responder/', json.dumps(corpo),
                           content_type='application/json')


print('validação da resposta')
r = responder(vend_b, {'resposta': 'nao'})
t('fora do público não responde', r.status_code == 400 and not RespostaLoginErro.objects.exists())
r = responder(vend_a, {'resposta': 'talvez'})
t('resposta inválida', r.status_code == 400)
r = responder(vend_a, {'resposta': 'sim', 'itens': []})
t('sim sem sistema', r.status_code == 400 and 'sistema' in r.json()['erro'])
r = responder(vend_a, {'resposta': 'sim', 'itens': [{'sistema': 'INVENTADO', 'descricao': 'nao entra'}]})
t('sistema fora da lista', r.status_code == 400)
r = responder(vend_a, {'resposta': 'sim', 'itens': [{'sistema': 'SAP', 'descricao': 'oi'}]})
t('outros sistemas exigem descrição', r.status_code == 400 and 'SAP' in r.json()['erro'])
r = responder(vend_a, {'resposta': 'sim', 'itens': [{'sistema': POOL, 'descricao': 'sem telefone e e-mail'}]})
t('Pool exige telefone', r.status_code == 400 and 'telefone' in r.json()['erro'])
r = responder(vend_a, {'resposta': 'sim', 'itens': [{'sistema': POOL, 'telefone': '123', 'email': 'a@b.com'}]})
t('Pool: telefone curto recusado', r.status_code == 400 and 'telefone' in r.json()['erro'])
r = responder(vend_a, {'resposta': 'sim', 'itens': [{'sistema': POOL, 'telefone': '(11) 98765-4321', 'email': 'nada'}]})
t('Pool: e-mail inválido recusado', r.status_code == 400 and 'e-mail' in r.json()['erro'])
r = responder(vend_a, {'resposta': 'sim', 'itens': [{'sistema': 'SAP', 'descricao': 'Senha expirada'},
                                                     {'sistema': 'SAP', 'descricao': 'De novo aqui'}]})
t('sistema repetido recusado', r.status_code == 400)
t('nada gravado nas recusas', not RespostaLoginErro.objects.exists() and not Solicitacao.objects.exists())

print('lembrar depois')
r = responder(vend_a, {'resposta': 'depois'})
t('adiar grava ADIADO para amanhã', r.status_code == 200 and RespostaLoginErro.objects.get(usuario=vend_a).situacao == 'ADIADO'
  and RespostaLoginErro.objects.get(usuario=vend_a).adiado_ate == timezone.localdate() + timedelta(days=1))
t('adiado não aparece hoje', pendente(vend_a) is None)
RespostaLoginErro.objects.filter(usuario=vend_a).update(adiado_ate=timezone.localdate())
cache.delete(pl.chave_situacao(vend_a.id))
t('adiamento vencido: volta a aparecer', pendente(vend_a) is not None)

print('resposta com erro')
r = responder(vend_a, {'resposta': 'sim', 'itens': [
    {'sistema': POOL, 'telefone': '+55 (11) 98765-4321', 'email': 'vend.a@exemplo-teste.local'},
    {'sistema': 'SAP', 'descricao': 'Senha expirada desde ontem'}]})
t('sim com Pool + SAP aceito', r.status_code == 200 and r.json()['ok'], r.content[:200])
resp = RespostaLoginErro.objects.get(usuario=vend_a)
itens = list(resp.itens.all())
t('situação COM_ERRO com setor/hierarquia do momento', resp.situacao == 'COM_ERRO' and resp.setor_id == loja_a.id
  and resp.hierarquia == 'PADRAO' and resp.adiado_ate is None)
t('dois itens: Pool com telefone/e-mail, SAP com descrição',
  [(i.sistema, i.telefone, i.email, i.descricao) for i in itens]
  == [(POOL, '11987654321', 'vend.a@exemplo-teste.local', ''), ('SAP', '', '', 'Senha expirada desde ontem')])
sol = resp.solicitacao
t('virou pedido em Solicitações', sol is not None and sol.motivo == 'FALHA' and sol.sistemas == [POOL, 'SAP']
  and sol.contato == '11987654321' and 'Senha expirada' in sol.descricao and sol.solicitante_id == vend_a.id)
t('mensagem cita o pedido', sol is not None and sol.numero in r.json()['msg'])
t('depois de responder não aparece mais', pendente(vend_a) is None)
r = responder(vend_a, {'resposta': 'nao'})
t('segunda resposta recusada', r.status_code == 400 and RespostaLoginErro.objects.get(usuario=vend_a).situacao == 'COM_ERRO')

print('resposta sem erro')
camp2 = CampanhaLoginErro.objects.create(titulo='Todos', criado_por=gestor)
pl.limpar_cache_campanhas()
t('campanha sem filtro alcança a loja B', pendente(vend_b) is not None and pendente(vend_b)['id'] == camp2.id)
r = responder(vend_b, {'resposta': 'nao'}, camp2.id)
t('não → SEM_ERRO sem pedido', r.status_code == 200
  and RespostaLoginErro.objects.get(usuario=vend_b, campanha=camp2).situacao == 'SEM_ERRO'
  and RespostaLoginErro.objects.get(usuario=vend_b, campanha=camp2).solicitacao_id is None)
t('e não aparece mais', pendente(vend_b) is None)
t('a mais antiga vem primeiro para quem tem as duas', pendente(sup_a)['id'] == camp2.id)

print('janela e ativo')
camp3 = CampanhaLoginErro.objects.create(titulo='Futura', inicio=timezone.now() + timedelta(days=2))
camp4 = CampanhaLoginErro.objects.create(titulo='Vencida', fim=timezone.now() - timedelta(minutes=1))
camp5 = CampanhaLoginErro.objects.create(titulo='Desligada', ativo=False)
pl.limpar_cache_campanhas()
t('futura, vencida e desligada não aparecem', pendente(vend_b) is None)
r = responder(vend_b, {'resposta': 'nao'}, camp5.id)
t('desligada não aceita resposta', r.status_code == 400)
r = cg.post(f'/acess/popups/{camp2.id}/alternar/')
camp2.refresh_from_db()
t('gestor desativa', r.status_code == 302 and not camp2.ativo)
t('desativada some na hora (cache limpo)', pendente(sup_a) is None or pendente(sup_a)['id'] != camp2.id)
r = cg.post(f'/acess/popups/{camp3.id}/editar/', {'titulo': 'Futura agora', 'ativo': 'on', 'hierarquias': ['SUPERVISOR']})
t('editar tira a janela e muda o público', r.status_code == 302 and pendente(sup_a) and pendente(sup_a)['id'] == camp3.id
  and pendente(vend_b) is None)

print('telas de gestão')
r = cg.get('/acess/popups/')
html = r.content.decode()
t('lista mostra as campanhas', r.status_code == 200 and 'Logins da loja A' in html and 'Futura agora' in html)
r = cg.get(f'/acess/popups/{camp.id}/')
html = r.content.decode()
t('respostas com telefone, e-mail e descrição', r.status_code == 200 and '(11) 98765-4321' in html
  and 'vend.a@exemplo-teste.local' in html and 'Senha expirada desde ontem' in html and sol.numero in html)
r = cg.get(f'/acess/popups/{camp.id}/', {'situacao': 'COM_ERRO', 'sistema': 'SAP'})
t('filtro por sistema', [x.usuario_id for x in r.context['linhas']] == [vend_a.id])
r = cg.get(f'/acess/popups/{camp.id}/', {'situacao': 'SEM_ERRO'})
t('filtro por situação', r.status_code == 200 and list(r.context['linhas']) == [])
r = cg.get(f'/acess/popups/{camp2.id}/', {'situacao': 'PENDENTE'})
html = r.content.decode()
t('sem resposta lista quem falta (e não quem respondeu)', 'zzp.supa@' in html and 'zzp.vendb@' not in html)
r = cg.get(f'/acess/popups/{camp.id}/', {'exportar': 'xlsx'})
t('Excel', r.status_code == 200 and r['Content-Type'].startswith('application/vnd.openxml'))
r = cg.get(f'/acess/popups/{camp.id}/', {'exportar': 'csv'})
csv_txt = r.content.decode('utf-8-sig')
t('CSV com uma linha por sistema', r.status_code == 200 and csv_txt.count('\n') == 3 and 'Senha expirada' in csv_txt)
r = cliente(outro_acess).get(f'/acess/popups/{camp.id}/')
t('respostas fechadas para quem não gerencia', r.status_code == 302)
r = cliente(vend_a).get(f'/acess/popups/{camp.id}/', {'exportar': 'csv'})
t('export fechado para usuário comum', r.status_code == 302)

print('higiene dos templates')
novos = ['templates/acess/_popup_login_erro.html', 'templates/acess/popups.html', 'templates/acess/popup_form.html',
         'templates/acess/popup_respostas.html', 'templates/portal_popups/_popup_gate.html']
for caminho in novos:
    texto = pathlib.Path(caminho).read_text()
    abertos = [ln for ln in texto.splitlines() if '{#' in ln and '#}' not in ln.split('{#', 1)[1]]
    t(f'{caminho}: comentário {{# #}} numa linha só', not abertos, abertos[:1])

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
