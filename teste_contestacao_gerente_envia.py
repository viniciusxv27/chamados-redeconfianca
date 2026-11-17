"""Contestação por equipe (17/11/2026): cada CN faz a sua, só o gerente envia.

Pedido: "Cada CN (Vendedor) faz a sua, mas só o gerente envia e ele só envia
quando todos fizerem, além disso se o cara marcar em não ele tem que dar uma
justificativa. Porém o Gerente pode fazer pelo CN (Deixando sempre o histórico
de forma correta com quem fez)."

Cobre:

- o CN entra em /contestacao/ só com as vendas dele e não envia (403);
- o "Não" exige justificativa (servidor) e fica registrado com quem respondeu;
- "pronto" só passa com toda venda respondida, e cai se a pessoa mexer depois;
- o gerente fica travado (aguardando_cns) até todo CN ativo marcar pronto —
  vendedor sem cadastro ativo no portal não trava;
- o gerente faz pelo CN: o histórico guarda quem fez e para quem;
- o envio cria cada contestação uma vez só, no nome do CN, com quem preencheu
  e quem enviou; reenviar não duplica;
- a gestão central (SUPERADMIN) envia sem trava, como antes;
- carrinho antigo não ressuscita um Não; o Sim clicado troca o Não;
- de acordo: quem enviou (gerente) continua podendo responder;
- a tela: painel do CN x painel do gerente, botão Enviar some para o CN, JS válido.

Banco descartável (sqlite, schema direto dos models): as tabelas novas só
existem no Postgres compartilhado depois do migrate, e o migrate é dele.
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

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-ct-equipe-'))
(PASTA / 'zz_ct_equipe_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-equipe'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ct-equipe-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_ct_equipe_settings'
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

from communications.models import CommunicationGroup
from contestacao import equipe
from contestacao.models import (Contestation, ContestationCartDraft, ContestationHistory, ExclusionRecord,
                                ExclusionSyncBatch, ParteDoCN, RespostaNaoContesta, TipoBase)
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
    return User.objects.create_user(username=f'zzeq.{u}', email=f'zzeq.{u}@exemplo-teste.local',
                                    password='S3nha!teste', first_name=primeiro, last_name=ultimo, **k)


# --- Cenário ---------------------------------------------------------------
loja = Sector.objects.create(name='Loja ZZ Equipe')
outra_loja = Sector.objects.create(name='Loja ZZ Outra')
chefe = novo('chefe', 'Chefe', 'Central', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
gerentes, _ = CommunicationGroup.objects.get_or_create(name='GERENTES', defaults={'created_by': chefe})
gerente = novo('gerente', 'Gerson', 'Gerente', sector=loja)
gerente.communication_groups.add(gerentes)
ana = novo('ana', 'Ana', 'Vendedora', sector=loja)
bruno = novo('bruno', 'Bruno', 'Vendedor', sector=loja)
carlos = novo('carlos', 'Carlos', 'Saiu', sector=loja, is_active=False)   # saiu: não trava
dora = novo('dora', 'Dora', 'Outra', sector=outra_loja)

lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=9, created_by=chefe)
ContestationHistory.objects.create(action='synced', user=chefe, notes='ZZ sync do teste')


def venda(numero, vendedor, filial='ZZ EQUIPE'):
    return ExclusionRecord.objects.create(
        sync_batch=lote, filial=filial, vendedor=vendedor, receita=100 + numero, pilar='MOVEL',
        numero_venda=f'ZZE{numero}', data_venda='01/11/2026', nome_cliente=f'ZZ Cliente {numero}',
        record_type=TipoBase.EXCLUSAO)


a1, a2, a3 = venda(1, 'ANA VENDEDORA'), venda(2, 'ANA VENDEDORA'), venda(3, 'ANA VENDEDORA')
b1, b2 = venda(4, 'BRUNO VENDÊDOR'), venda(5, 'BRUNO VENDÊDOR')      # acento: o nome é normalizado
c1 = venda(6, 'CARLOS SAIU')
f1 = venda(7, 'FULANO SEM CADASTRO')
d1 = venda(8, 'DORA OUTRA', filial='ZZ OUTRA')

cc = Client(); cc.force_login(chefe)
cg = Client(); cg.force_login(gerente)
ca = Client(); ca.force_login(ana)
cb = Client(); cb.force_login(bruno)

LISTA = '/contestacao/'
CONFERIR = '/contestacao/carrinho/conferir/'
ENVIAR = '/contestacao/contestar-lote/'
SALVAR = '/contestacao/carrinho/rascunho/salvar/'
SINCRONIZAR = '/contestacao/carrinho/rascunho/sincronizar/'
PRONTO = '/contestacao/ciclo/pronto/'
REABRIR = '/contestacao/ciclo/reabrir/'


def nao(cliente, venda_, justificativa):
    return cliente.post(f'/contestacao/venda/{venda_.pk}/nao/', {'justificativa': justificativa})


def sim(cliente, venda_, motivo='ZZ motivo do teste', com_anexo=True, substituir=False):
    dados = {'exclusion_id': venda_.pk, 'reason': motivo}
    if com_anexo:
        dados['attachment'] = foto()
    if substituir:
        dados['substituir_nao'] = '1'
    return cliente.post(SALVAR, dados)


def pronto(cliente, vendedor, filial='ZZ EQUIPE'):
    return cliente.post(PRONTO, {'filial': filial, 'vendedor': vendedor})


def enviar(cliente, ids):
    return cliente.post(ENVIAR, data=json.dumps({'ids': [v.pk for v in ids]}), content_type='application/json')


def conferir_loja(cliente):
    return cliente.post(CONFERIR, data=json.dumps({'loja': True, 'ids': []}), content_type='application/json')


def loja_estado():
    return equipe.estado_das_lojas(lote, ['ZZ EQUIPE'])['ZZ EQUIPE']


# --- Quem é quem ------------------------------------------------------------
print('Quem é CN')
donos = equipe.donos_do_lote(lote)
t('Ana e Bruno viram CNs pelo nome (com acento normalizado)',
  donos[('ZZ EQUIPE', 'ANA VENDEDORA')] == ana.pk and donos[('ZZ EQUIPE', 'BRUNO VENDÊDOR')] == bruno.pk, donos)
t('quem saiu (inativo) e quem não tem cadastro ficam sem CN',
  donos[('ZZ EQUIPE', 'CARLOS SAIU')] is None and donos[('ZZ EQUIPE', 'FULANO SEM CADASTRO')] is None)
t('papéis', [equipe.papel(u) for u in (chefe, gerente, ana)] == ['central', 'gerente', 'cn'])

# --- O CN entra e vê só as dele ---------------------------------------------
print('CN na tela')
r = ca.get(LISTA)
t('CN abre /contestacao/ (antes só gerente)', r.status_code == 200, r.status_code)
ids_na_tela = {rec.pk for rec in r.context['records']} if r.status_code == 200 else set()
t('CN vê só as vendas dele', ids_na_tela == {a1.pk, a2.pk, a3.pk}, ids_na_tela)
html_ana = r.content.decode() if r.status_code == 200 else ''
t('painel "Sua parte" e sem botão Enviar do carrinho',
  'Sua parte neste ciclo' in html_ana and 'id="btnSubmitAll"' not in html_ana and 'O gerente envia' in html_ana)
t('menu mostra Contestação para o CN', r.context.get('can_access_contestacao_menu') is True)
t('CN não entra na contestação de valores', ca.get('/contestacao/valores/').status_code == 302)

print('CN não envia')
sim(ca, a1)
r = enviar(ca, [a1])
t('envio do CN: 403', r.status_code == 403, r.status_code)
t('conferência do CN: 403', ca.post(CONFERIR, data=json.dumps({'ids': [a1.pk]}),
                                    content_type='application/json').status_code == 403)
t('nenhuma contestação criada', not Contestation.objects.exists())
t('CN não mexe em venda de outro CN (Sim)', sim(ca, b1).status_code == 404)
t('CN não responde Não por outro CN', nao(ca, b2, 'nao vou contestar essa').status_code == 404)

print('Não exige justificativa')
r = nao(ca, a2, '')
t('Não sem justificativa: 400', r.status_code == 400 and r.json().get('campo') == 'justificativa', r.status_code)
r = nao(ca, a2, ' ok ')
t('Não com justificativa curta demais: 400', r.status_code == 400)
t('nada gravado', not RespostaNaoContesta.objects.exists())
r = nao(ca, a2, 'Cliente cancelou de verdade')
resp_a2 = RespostaNaoContesta.objects.filter(exclusion=a2).first()
t('Não com justificativa: grava (CN e quem respondeu)', r.status_code == 200 and resp_a2
  and resp_a2.cn_id == ana.pk and resp_a2.preenchido_por_id == ana.pk, r.content[:200])
h = ContestationHistory.objects.filter(action='nao_contesta').order_by('-pk').first()
t('histórico do Não: Ana fez por ela mesma', h and h.user_id == ana.pk and h.extra_data['para_cn_id'] == ana.pk
  and h.extra_data['em_nome_do_cn'] is False)

print('Pronto só com tudo respondido')
r = pronto(ca, 'ANA VENDEDORA')
t('pronto com a3 sem resposta: recusado', not ParteDoCN.objects.exists())
nao(ca, a3, 'Venda duplicada na base')
r = pronto(ca, 'ANA VENDEDORA')
parte_ana = ParteDoCN.objects.filter(lote=lote, vendedor='ANA VENDEDORA').first()
t('pronto da Ana gravado por ela', parte_ana and parte_ana.cn_id == ana.pk and parte_ana.marcado_por_id == ana.pk)
t('CN não marca pronto por outro CN', pronto(ca, 'BRUNO VENDÊDOR') and
  not ParteDoCN.objects.filter(vendedor='BRUNO VENDÊDOR').exists())

print('Gerente travado até todos fazerem')
t('loja ainda travada (Bruno pendente)', loja_estado()['liberada'] is False)
r = conferir_loja(cg)
j = r.json()
sit = {i['id']: i['situacao'] for i in j.get('itens', [])}
t('conferência da loja: Sim da Ana aparece esperando os CNs', r.status_code == 200
  and sit.get(a1.pk) == 'aguardando_cns' and j['prontas'] == [], j)
t('conferência diz quem falta', any('Bruno' in ' '.join(l['faltando']) for l in j['lojas']), j.get('lojas'))
r = enviar(cg, [a1])
j = r.json()
t('envio barrado no servidor (não só no botão)', j['criadas'] == []
  and j['ignoradas'] == [{'id': a1.pk, 'motivo': 'aguardando_cns'}], j)
t('nada criado', not Contestation.objects.exists())
html_ger = cg.get(LISTA).content.decode()
t('painel do gerente mostra a equipe e o envio da loja',
  'Equipe neste ciclo' in html_ger and 'Enviar contestações da loja' in html_ger and 'BRUNO VEND' in html_ger)
t('gerente vê, na linha, o Sim que está no carrinho da Ana', 'no carrinho de Ana Vendedora' in html_ger)

print('Gerente faz pelo CN')
r = sim(cg, b1, motivo='Gerente contestando pelo Bruno')
t('gerente põe Sim na venda do Bruno', r.status_code == 200, r.content[:200])
r = nao(cg, b2, 'Bruno confirmou que cliente desistiu')
resp_b2 = RespostaNaoContesta.objects.get(exclusion=b2)
t('Não do gerente: CN = Bruno, respondido por = gerente',
  resp_b2.cn_id == bruno.pk and resp_b2.preenchido_por_id == gerente.pk)
h = ContestationHistory.objects.filter(action='nao_contesta').order_by('-pk').first()
t('histórico: quem fez (gerente) e para quem (Bruno)', h.user_id == gerente.pk
  and h.extra_data['para_cn_id'] == bruno.pk and h.extra_data['em_nome_do_cn'] is True)
sim(cg, f1, motivo='Venda do Fulano, que não tem cadastro')
pronto(cg, 'BRUNO VENDÊDOR')
parte_b = ParteDoCN.objects.filter(vendedor='BRUNO VENDÊDOR').first()
t('pronto do Bruno feito pelo gerente', parte_b and parte_b.cn_id == bruno.pk and parte_b.marcado_por_id == gerente.pk)
h = ContestationHistory.objects.filter(action='cn_pronto').order_by('-pk').first()
t('histórico do pronto: gerente em nome do Bruno', h.user_id == gerente.pk and h.extra_data['para_cn_id'] == bruno.pk)
est = loja_estado()
t('loja liberada (Carlos inativo e Fulano sem cadastro não travam)', est['liberada'] is True
  and est['cns'] == 2 and est['sem_cadastro'] == 2, (est['cns'], est['sem_cadastro']))

print('Envio do gerente')
r = conferir_loja(cg)
j = r.json()
t('conferência da loja: a1 (carrinho da Ana), b1 e f1 (do gerente) prontas',
  sorted(j['prontas']) == sorted([a1.pk, b1.pk, f1.pk]), j)
r = enviar(cg, [a1, b1, f1])
j = r.json()
t('envio cria as 3', sorted(j['criadas']) == sorted([a1.pk, b1.pk, f1.pk]), j)
ca1 = Contestation.objects.get(exclusion=a1)
cb1 = Contestation.objects.get(exclusion=b1)
cf1 = Contestation.objects.get(exclusion=f1)
t('a1: do CN (Ana), preenchida pela Ana, enviada pelo gerente',
  (ca1.requester_id, ca1.preenchido_por_id, ca1.enviado_por_id) == (ana.pk, ana.pk, gerente.pk))
t('b1: do CN (Bruno), preenchida pelo gerente, enviada pelo gerente',
  (cb1.requester_id, cb1.preenchido_por_id, cb1.enviado_por_id) == (bruno.pk, gerente.pk, gerente.pk))
t('f1 (sem cadastro): fica com quem preencheu, como antes', cf1.requester_id == gerente.pk)
hc = ContestationHistory.objects.get(contestation=cb1, action='created')
t('histórico da criação: quem enviou, quem preencheu, para quem', hc.user_id == gerente.pk
  and hc.extra_data['enviado_por_id'] == gerente.pk and hc.extra_data['feito_por_id'] == gerente.pk
  and hc.extra_data['para_cn_id'] == bruno.pk)
t('carrinhos esvaziados (o da Ana também)', not ContestationCartDraft.objects.filter(
  exclusion_id__in=[a1.pk, b1.pk, f1.pk]).exists())
r = enviar(cg, [a1, b1, f1])
j = r.json()
t('reenviar não duplica', j['criadas'] == [] and Contestation.objects.filter(
  exclusion_id__in=[a1.pk, b1.pk, f1.pk]).count() == 3, j)

print('Telas depois do envio')
t('Ana vê a contestação dela em "Minhas"', [c.pk for c in ca.get('/contestacao/minhas/').context['contestations']] == [ca1.pk])
t('Ana não abre a do Bruno', ca.get(f'/contestacao/{cb1.pk}/').status_code == 302)
r = cg.get(f'/contestacao/{cb1.pk}/')
t('gerente abre a que enviou e vê quem preencheu', r.status_code == 200 and 'enviada por' in r.content.decode())
cb1.reject(chefe, 'ZZ rejeitada')
r = cg.post(f'/contestacao/{cb1.pk}/confirmar/', {'confirmation_notes': 'ok'})
cb1.refresh_from_db()
t('de acordo: o gerente que enviou ainda responde', cb1.confirmed_by_id == gerente.pk)
t('histórico mostra os novos registros', 'Respondida Não' in cc.get('/contestacao/historico/').content.decode())

print('Não x Sim e carrinho antigo')
r = sim(ca, a3, motivo='Mudei de ideia', com_anexo=False)
t('Sim automático não desfaz o Não (409)', r.status_code == 409 and r.json().get('respondida_nao'))
t('Não segue lá', RespostaNaoContesta.objects.filter(exclusion=a3).exists())
r = sim(ca, a3, motivo='Mudei de ideia', com_anexo=False, substituir=True)
t('clique em Sim troca o Não (com histórico)', r.status_code == 200
  and not RespostaNaoContesta.objects.filter(exclusion=a3).exists()
  and ContestationHistory.objects.filter(action='nao_desfeito', user=ana).exists())
est = loja_estado()
ana_p = next(p for p in est['participantes'] if p['vendedor'] == 'ANA VENDEDORA')
t('pronto cai quando a venda fica incompleta (Sim sem evidência)', ana_p['pronto'] is False
  and ana_p['mudou_depois'] is True and est['liberada'] is False)
nao(ca, a3, 'Voltei atrás, não contesto')
t('Não tira a venda do carrinho', not ContestationCartDraft.objects.filter(exclusion=a3).exists())
r = ca.post(SINCRONIZAR, data=json.dumps({'items': [{'exclusion_id': a3.pk, 'reason': 'carrinho velho'}]}),
            content_type='application/json')
t('carrinho antigo não ressuscita o Não', r.json()['unresolved'] == [{'local_id': a3.pk, 'motivo': 'respondida_nao'}]
  and not ContestationCartDraft.objects.filter(exclusion=a3).exists(), r.json())
t('loja volta a liberar', loja_estado()['liberada'] is True)
r = ca.post(REABRIR, {'filial': 'ZZ EQUIPE', 'vendedor': 'ANA VENDEDORA'})
t('reabrir trava de novo', loja_estado()['liberada'] is False
  and ContestationHistory.objects.filter(action='cn_reaberto', user=ana).exists())
pronto(ca, 'ANA VENDEDORA')

print('Gestão central sem trava')
ContestationCartDraft.objects.create(user=chefe, exclusion=d1, reason='ZZ central').attachment.save(
    'd1.jpg', foto(), save=True)
t('loja da Dora travada (Dora não fez)', equipe.estado_das_lojas(lote, ['ZZ OUTRA'])['ZZ OUTRA']['liberada'] is False)
r = enviar(cc, [d1])
cd1 = Contestation.objects.filter(exclusion=d1).first()
t('SUPERADMIN envia mesmo assim, no nome da Dora', r.json()['criadas'] == [d1.pk] and cd1
  and cd1.requester_id == dora.pk and cd1.enviado_por_id == chefe.pk, r.json())

print('Tela e JS')
r = cg.get(LISTA)
html = r.content.decode()
t('gerente: tela abre com as vendas da loja', r.status_code == 200 and len(r.context['records']) == 7)
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
js = next((sc for sc in scripts if 'window.pendingReasons' in sc), '')
arq = PASTA / 'tela.js'
arq.write_text(js)
try:
    chk = subprocess.run(['node', '--check', str(arq)], capture_output=True, text=True, timeout=30)
    t('JS da tela passa no node --check', chk.returncode == 0, chk.stderr[:400])
except FileNotFoundError:
    print('  (node não encontrado: pulei o --check)')
t('JS tem o Não com justificativa e o envio da loja',
  'window.confirmarNao' in js and 'window.enviarDaLoja' in js and "formData.append('substituir_nao', '1')" in js)
r = cg.get(LISTA + '?vendedor=BRUNO%20VEND%C3%8ADOR')
t('"Ver vendas" do gerente filtra pelo CN', {rec.pk for rec in r.context['records']} == {b1.pk, b2.pk})
t('a justificativa do Não aparece na linha', 'Bruno confirmou que cliente desistiu' in r.content.decode())

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
