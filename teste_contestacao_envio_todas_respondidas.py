"""Contestação: o gerente só envia com TODAS as vendas da loja respondidas.

Pedido (23/11/2026): "O gerente na hora de enviar deve enviar todas
obrigatoriamente com Sim ou Não, enquanto todas não estiverem no carrinho deve
estar travado".

"A responder" é o mesmo universo que a tela mostra ao gerente: as vendas do
lote atual no escopo dele, e "respondida" é o mesmo critério do "Faltam" do
painel (``equipe.RESPONDIDAS``: Sim com motivo e evidência, Não com
justificativa, ou já contestada). A regra mora em ``equipe.trava_do_envio``.

Cobre:

- o que conta como pendente (sem resposta e Sim sem evidência) e o que não
  conta (já contestada — até decidida —, venda de lote antigo, Não com
  discordância do CN);
- a tela: botão travado, quantas e quais faltam, link para cada uma, filtro
  ``?sem_resposta=1`` e o quadro redesenhado sem recarregar (/envio/trava/);
- o servidor recusa: envio em lote (JSON e FormData), conferência (pessoal e
  da loja) e o caminho antigo de uma venda só;
- destrava quando a última venda é respondida (Sim do próprio pedido conta);
- trava por loja (supervisor com duas lojas) e gestão central sem trava;
- o custo da trava não cresce com o número de vendas.

Banco descartável (sqlite, schema direto dos models), como o
teste_contestacao_gerente_envia.py. Nada sai para a rede.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-ct-trava-'))
(PASTA / 'zz_ct_trava_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-trava'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-trava-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_ct_trava_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext

from communications.models import CommunicationGroup
from contestacao import equipe
from contestacao.models import (Contestation, ContestationCartDraft, ContestationHistory, ExclusionRecord,
                                ExclusionSyncBatch, RespostaNaoContesta, TipoBase)
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


def foto(nome='evidencia.jpg'):
    return SimpleUploadedFile(nome, b'\xff\xd8\xff\xe0zz-evidencia' + bytes(range(64)), content_type='image/jpeg')


def novo(u, primeiro, ultimo, **k):
    k.setdefault('hierarchy', 'PADRAO')
    return User.objects.create_user(username=f'zztr.{u}', email=f'zztr.{u}@exemplo-teste.local',
                                    password='S3nha!teste', first_name=primeiro, last_name=ultimo, **k)


# --- Cenário ---------------------------------------------------------------
# Nomes que não contêm um ao outro: o escopo casa setor e filial por pedaço do nome.
alfa = Sector.objects.create(name='Loja ZZ Alfa')
beta = Sector.objects.create(name='Loja ZZ Beta')
gama = Sector.objects.create(name='Loja ZZ Gama')
delta = Sector.objects.create(name='Loja ZZ Delta')
chefe = novo('chefe', 'Chefe', 'Central', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
gerentes, _ = CommunicationGroup.objects.get_or_create(name='GERENTES', defaults={'created_by': chefe})
gerente = novo('gerente', 'Gerson', 'Gerente', sector=alfa)
gerente.communication_groups.add(gerentes)
supervisor = novo('supervisor', 'Silvia', 'Supervisora', hierarchy='SUPERVISOR', sector=gama)
supervisor.sectors.add(gama, delta)
ana = novo('ana', 'Ana', 'Vendedora', sector=alfa)

lote_velho = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=1, created_by=chefe)
velha = ExclusionRecord.objects.create(
    sync_batch=lote_velho, filial='ZZ ALFA', vendedor='ANA VENDEDORA', receita=50, pilar='MOVEL',
    numero_venda='ZZT0', data_venda='01/10/2026', nome_cliente='ZZ Cliente velho', record_type=TipoBase.EXCLUSAO)
lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=10, created_by=chefe)
ContestationHistory.objects.create(action='synced', user=chefe, notes='ZZ sync do teste')


def venda(numero, filial='ZZ ALFA', vendedor='ANA VENDEDORA'):
    return ExclusionRecord.objects.create(
        sync_batch=lote, filial=filial, vendedor=vendedor, receita=100 + numero, pilar='MOVEL',
        numero_venda=f'ZZT{numero}', data_venda='01/11/2026', nome_cliente=f'ZZ Cliente {numero}',
        record_type=TipoBase.EXCLUSAO)


v1, v2, v3, v4 = venda(1), venda(2), venda(3), venda(4, vendedor='FULANO SEM CADASTRO')
v5 = venda(5)                                   # já contestada e decidida antes
Contestation.objects.create(exclusion=v5, requester=ana, reason='ZZ decidida antes', status='denied')
b1, b2 = venda(11, 'ZZ BETA', 'BETO VENDEDOR'), venda(12, 'ZZ BETA', 'BETO VENDEDOR')
g1 = venda(21, 'ZZ GAMA', 'GIL VENDEDOR')
d1, d2 = venda(31, 'ZZ DELTA', 'DITO VENDEDOR'), venda(32, 'ZZ DELTA', 'DITO VENDEDOR')

cc = Client(); cc.force_login(chefe)
cg = Client(); cg.force_login(gerente)
cs = Client(); cs.force_login(supervisor)
ca = Client(); ca.force_login(ana)

LISTA = '/contestacao/'
CONFERIR = '/contestacao/carrinho/conferir/'
ENVIAR = '/contestacao/contestar-lote/'
SALVAR = '/contestacao/carrinho/rascunho/salvar/'
TRAVA = '/contestacao/envio/trava/'


def nao(cliente, venda_, justificativa):
    return cliente.post(f'/contestacao/venda/{venda_.pk}/nao/', {'justificativa': justificativa})


def sim(cliente, venda_, motivo='ZZ motivo do teste', com_anexo=True):
    dados = {'exclusion_id': venda_.pk, 'reason': motivo}
    if com_anexo:
        dados['attachment'] = foto()
    return cliente.post(SALVAR, dados)


def enviar(cliente, vendas):
    return cliente.post(ENVIAR, data=json.dumps({'ids': [v.pk for v in vendas]}), content_type='application/json')


def conferir(cliente, vendas=(), loja=False):
    return cliente.post(CONFERIR, data=json.dumps({'loja': loja, 'ids': [v.pk for v in vendas]}),
                        content_type='application/json')


def motivos(resposta):
    return {i['id']: i['motivo'] for i in resposta.json().get('ignoradas', [])}


def trava_de(user):
    return equipe.trava_do_envio(user, lote)


def contestada(v):
    return Contestation.objects.filter(exclusion=v).exists()


# --- O que conta como "a responder" ------------------------------------------
print('O universo da trava')
tr = trava_de(gerente)
pend = set(tr['lojas'][0]['ids']) if tr['lojas'] else set()
t('gerente travado: 4 vendas sem resposta na ZZ ALFA', tr['travado'] and tr['faltam'] == 4
  and [l['filial'] for l in tr['lojas']] == ['ZZ ALFA'], tr)
t('as 4 são as do lote atual sem resposta', pend == {v1.pk, v2.pk, v3.pk, v4.pk}, pend)
t('já contestada (mesmo decidida) não conta', v5.pk not in pend)
t('venda de lote antigo não conta', velha.pk not in pend)
t('venda sem CN no portal conta (o gerente responde todas)', v4.pk in pend)
est = equipe.estado_das_lojas(lote, ['ZZ ALFA'])['ZZ ALFA']
t('é o mesmo "Faltam" do painel', est['faltam'] == 4 and sorted(est['ids_faltam']) == sorted(pend))
t('gestão central é isenta', trava_de(chefe)['isento'] and not trava_de(chefe)['travado'])

print('Tela do gerente: travado, quantas e quais')
r = cg.get(LISTA)
html = r.content.decode()
t('tela abre', r.status_code == 200, r.status_code)
t('quadro "Envio travado: 4 vendas sem resposta"', 'Envio travado: 4 vendas sem resposta' in html)
botao = re.search(r'<button[^>]*id="btnEnviarLoja"[^>]*>', html, re.S)
t('botão de enviar da loja desabilitado', bool(botao) and 'disabled' in botao.group(0), botao and botao.group(0))
t('botão diz que está travado', 'Envio travado: 4 sem resposta' in html)
t('cada venda que falta tem link para responder',
  all(f'?venda={v.pk}"' in html for v in (v1, v2, v3, v4)))
t('e o atalho "Ver só as que faltam"', '?sem_resposta=1' in html and 'Ver só as que faltam' in html)
t('estado para o JS', 'data-travado="1"' in html and 'data-faltam="4"' in html and 'data-gerente="1"' in html)
t('painel não fala mais em envio sem trava',
  'O envio só libera com <strong>todas</strong> as vendas da loja respondidas' in html)

r = cg.get(LISTA + '?sem_resposta=1')
ids = {rec.pk for rec in r.context['records']}
t('?sem_resposta=1 mostra só as que faltam', ids == pend, ids)
t('com o aviso no topo', 'Mostrando só as vendas <strong>sem resposta</strong>' in r.content.decode())
r = cg.get(LISTA + f'?venda={v3.pk}')
t('"Responder" abre a venda', [rec.pk for rec in r.context['records']] == [v3.pk])

# --- Respondendo aos poucos ---------------------------------------------------
print('Respostas parciais não destravam')
t('Sim completo', sim(cg, v1).status_code == 200)
t('Não com justificativa', nao(cg, v2, 'Cliente cancelou de verdade').status_code == 200)
t('Sim sem evidência', sim(cg, v3, com_anexo=False).status_code == 200)
tr = equipe.detalhar_trava(trava_de(gerente))
itens = {i['id']: i for i in tr['lojas'][0]['itens']}
t('faltam 2: o Sim sem evidência e a sem resposta', tr['faltam'] == 2 and set(itens) == {v3.pk, v4.pk}, tr)
t('cada uma diz o que falta', itens[v3.pk]['rotulo'] == 'Sim, falta motivo ou evidência'
  and itens[v4.pk]['rotulo'] == 'Sem resposta', itens)

print('Servidor recusa o envio')
r = enviar(cg, [v1])
t('envio em lote: nada criado, motivo loja_com_pendencia', r.status_code == 200 and r.json()['created'] == 0
  and motivos(r) == {v1.pk: 'loja_com_pendencia'} and not contestada(v1), r.content[:300])
t('a resposta traz a trava (quantas e quais)', r.json().get('trava', {}).get('faltam') == 2
  and {i['id'] for i in r.json()['trava']['lojas'][0]['itens']} == {v3.pk, v4.pk})
t('o Sim continua no carrinho', ContestationCartDraft.objects.filter(exclusion=v1).exists())
r = enviar(cg, [v3])
t('item com problema próprio mantém o motivo dele', motivos(r) == {v3.pk: 'sem_evidencia'}, r.content[:200])

j = conferir(cg, [v1]).json()
t('conferência do carrinho: a pronta vira loja_com_pendencia', j['prontas'] == []
  and j['itens'][0]['situacao'] == 'loja_com_pendencia', j)
t('e a trava vem com as que faltam', j['trava']['travado'] and j['trava']['faltam'] == 2
  and {i['id'] for i in j['trava']['lojas'][0]['itens']} == {v3.pk, v4.pk}, j.get('trava'))
j = conferir(cg, loja=True).json()
t('conferência da loja: nada pronto, loja marcada travada', j['prontas'] == []
  and j['lojas'][0]['travada'] is True and j['trava']['faltam'] == 2, j)

r = cg.post(ENVIAR, {'count': 1, 'exclusion_id_0': v4.pk, 'reason_0': 'ZZ pelo formulário', 'file_0': foto('f.jpg')})
t('FormData também recusa (v3 ainda sem evidência)', r.json()['created'] == 0
  and motivos(r) == {v4.pk: 'loja_com_pendencia'} and not contestada(v4), r.content[:200])

r = cg.post(f'/contestacao/contestar/{v1.pk}/', {'reason': 'ZZ caminho antigo', 'attachment': foto('a.jpg')})
t('caminho antigo de uma venda: recusa e leva às que faltam', r.status_code == 302
  and r['Location'].endswith('?sem_resposta=1') and not contestada(v1), (r.status_code, r.get('Location')))
html = cg.get(r['Location']).content.decode() if r.status_code == 302 else ''
t('e diz por quê', 'Envio travado: a loja ZZ ALFA ainda tem 2 venda(s) sem resposta' in html)

print('Desfazer o Não volta a travar; o quadro se redesenha')
nao(cg, v3, 'Venda duplicada na base')          # o Não troca o Sim incompleto
t('Não no lugar do Sim incompleto', RespostaNaoContesta.objects.filter(exclusion=v3).exists())
t('falta só 1', trava_de(gerente)['faltam'] == 1)
cg.post(f'/contestacao/venda/{v2.pk}/nao/desfazer/')
t('desfazer o Não: volta a faltar 2', trava_de(gerente)['faltam'] == 2)
frag = cg.get(TRAVA).content.decode()
t('fragmento /envio/trava/ mostra a trava atual', 'data-travado="1"' in frag and 'data-faltam="2"' in frag
  and f'?venda={v2.pk}"' in frag and '<html' not in frag.lower(), frag[:300])
nao(cg, v2, 'Cliente cancelou de verdade')

print('A última resposta pode vir no próprio envio')
r = cg.post(ENVIAR, {'count': 1, 'exclusion_id_0': v4.pk, 'reason_0': 'ZZ pelo formulário', 'file_0': foto('f.jpg')})
t('FormData com motivo e evidência da última que faltava: cria', r.json()['created'] == 1 and contestada(v4),
  r.content[:200])
tr = trava_de(gerente)
t('loja toda respondida: destravou', not tr['travado'] and tr['faltam'] == 0, tr)
frag = cg.get(TRAVA).content.decode()
t('quadro: envio liberado', 'data-travado="0"' in frag and 'envio liberado' in frag)
j = conferir(cg, loja=True).json()
t('conferência da loja: v1 pronta', j['prontas'] == [v1.pk] and not j['lojas'][0]['travada'], j)
r = enviar(cg, [v1])
t('envio do Sim sai', r.json()['criadas'] == [v1.pk] and 'trava' not in r.json(), r.content[:200])
html = cg.get(LISTA).content.decode()
sem_js = re.sub(r'<script.*?</script>', '', html, flags=re.S)
t('tela sem quadro vermelho', 'Envio travado' not in sem_js and 'data-travado="0"' in html
  and 'envio liberado' in sem_js, [sem_js[max(0, m.start() - 200):m.start() + 60]
                                     for m in re.finditer('Envio travado', sem_js)][:2])

print('Discordância do CN não trava (o Não continua lá)')
ca.post(f'/contestacao/venda/{v2.pk}/nao/de-acordo/',
        {'acao': 'discorda', 'motivo': 'Não foi cancelada, tenho o print', 'next': '/contestacao/'})
t('Ana discordou', RespostaNaoContesta.objects.get(exclusion=v2).situacao_cn == 'discorda')
t('a loja continua destravada', not trava_de(gerente)['travado'])

print('Venda nova no lote trava de novo')
v6 = venda(6)
r = sim(cg, v6)
t('Sim da nova', r.status_code == 200)
v7 = venda(7)
r = enviar(cg, [v6])
t('com a v7 sem resposta, a v6 não sai', motivos(r) == {v6.pk: 'loja_com_pendencia'})

# --- Por loja e gestão central -------------------------------------------------
print('Supervisor com duas lojas: trava por loja')
sim(cs, g1)
sim(cs, d1)
tr = trava_de(supervisor)
t('só a ZZ DELTA travada (d2 sem resposta)', tr['filiais'] == {'ZZ DELTA'} and tr['faltam'] == 1, tr)
r = cs.get(LISTA)
eq = r.context['equipe']
t('painel: 1 loja livre, 1 Sim liberado para enviar', eq['lojas_livres'] == 1 and eq['para_enviar_liberado'] == 1
  and eq['para_enviar'] == 2, (eq['lojas_livres'], eq['para_enviar_liberado'], eq['para_enviar']))
botao = re.search(r'<button[^>]*id="btnEnviarLoja"[^>]*>', r.content.decode(), re.S)
t('botão habilitado (há loja livre)', bool(botao) and 'disabled' not in botao.group(0))
j = conferir(cs, loja=True).json()
t('conferência da loja: só a da loja livre', j['prontas'] == [g1.pk], j)
r = enviar(cs, [g1, d1])
t('envio: ZZ GAMA sai, ZZ DELTA fica', r.json()['criadas'] == [g1.pk]
  and motivos(r) == {d1.pk: 'loja_com_pendencia'}, r.content[:300])

print('Gestão central envia sem trava')
ContestationCartDraft.objects.create(user=chefe, exclusion=b1, reason='ZZ central').attachment.save(
    'b1.jpg', foto(), save=True)
r = enviar(cc, [b1])
t('SUPERADMIN envia com b2 sem resposta', r.json()['criadas'] == [b1.pk], r.content[:200])
j = conferir(cc, [b1]).json()
t('conferência da central: trava isenta', j['trava']['isento'] and not j['trava']['travado'])
t('central não tem quadro de trava', 'data-gerente="1"' not in cc.get(LISTA).content.decode())
t('CN não tem quadro de trava', 'data-gerente="1"' not in ca.get(LISTA).content.decode())

# --- Custo -----------------------------------------------------------------------
print('Custo da trava')
with CaptureQueriesContext(connection) as poucas:
    trava_de(gerente)
for n in range(40, 80):
    venda(n)
with CaptureQueriesContext(connection) as muitas:
    tr = trava_de(gerente)
t('trava com 40 vendas a mais: mesmas consultas', len(poucas) == len(muitas), (len(poucas), len(muitas)))
t('e poucas', len(muitas) <= 15, len(muitas))
print(f'  (trava: {len(muitas)} consultas)')
with CaptureQueriesContext(connection) as det:
    equipe.detalhar_trava(tr)
t('detalhar a trava: 1 consulta', len(det) == 1, len(det))

# --- JS --------------------------------------------------------------------------
print('JS')
html = cg.get(LISTA).content.decode()
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
js = next((sc for sc in scripts if 'window.pendingReasons' in sc), '')
arq = PASTA / 'tela.js'
arq.write_text(js, encoding='utf-8')
try:
    chk = subprocess.run(['node', '--check', str(arq)], capture_output=True, text=True, timeout=30)
    t('JS da tela passa no node --check', chk.returncode == 0, chk.stderr[:400])
except FileNotFoundError:
    print('  (node não encontrado: pulei o --check)')
t('JS trava os botões e redesenha a trava', 'window.aplicarTrava' in js and 'window.atualizarTrava' in js
  and TRAVA in js and 'window.desenharTravaNaConferencia' in js)
t('JS conhece o motivo loja_com_pendencia', "loja_com_pendencia: 'A loja ainda tem venda sem resposta" in js)
t('Sim, Não e desfazer pedem a trava de novo', js.count('window.atualizarTrava(') >= 6, js.count('window.atualizarTrava('))

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
