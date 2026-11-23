"""Contestação da loja, fluxo invertido (23/11/2026): o gerente responde, o CN
dá o de acordo só nos Não.

Pedido: "O gerente faz a contestação e o CN dá o de acordo dele apenas nos que
o Gerente marcar como NÃO. O resto deve seguir a contestação." + prazo de 24 h
para o CN (passou: "De acordo automático por prazo expirado") + "quando tiver o
parecer do Gestor, o CN ter visão, mas quem pode questionar ao time de
conferência de venda é apenas o gerente". Respostas do dono: o CN pode
discordar (volta ao gerente) e o envio das Sim não espera os de acordo.

Cobre:

- o CN entra em /contestacao/ só com as vendas dele, sem Sim/Não (403 no servidor);
- o gerente responde: Não com justificativa abre 24 h para o CN; venda sem CN
  no portal não espera; mudar a justificativa reabre o prazo;
- o envio das Sim sai sem esperar os de acordo, no nome do CN;
- de acordo só do CN dono, uma vez; discordância com motivo volta ao gerente,
  que mantém o Não (com resposta) ou contesta (Sim troca o Não);
- prazo vencido: de acordo automático, uma vez, registrado pelo sistema;
- parecer do gestor: o CN vê, mas confirmar/negar é só do gerente da loja;
- a migração 0016 ajusta os Não do fluxo antigo; tela, JS e histórico.

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

import importlib
from datetime import timedelta

import requests
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

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
    return User.objects.create_user(username=f'zzeq.{u}', email=f'zzeq.{u}@exemplo-teste.local',
                                    password='S3nha!teste', first_name=primeiro, last_name=ultimo, **k)


# --- Cenário ---------------------------------------------------------------
loja = Sector.objects.create(name='Loja ZZ Equipe')
outra_loja = Sector.objects.create(name='Loja ZZ Outra')
chefe = novo('chefe', 'Chefe', 'Central', hierarchy='SUPERADMIN', is_superuser=True, is_staff=True)
gerentes, _ = CommunicationGroup.objects.get_or_create(name='GERENTES', defaults={'created_by': chefe})
gerente = novo('gerente', 'Gerson', 'Gerente', sector=loja)
gerente.communication_groups.add(gerentes)
gerente2 = novo('gerente2', 'Gilda', 'Subgerente', sector=loja)     # outro gerente da mesma loja
gerente2.communication_groups.add(gerentes)
gerente_fora = novo('gerentefora', 'Otto', 'Gerente', sector=outra_loja)
gerente_fora.communication_groups.add(gerentes)
ana = novo('ana', 'Ana', 'Vendedora', sector=loja)
bruno = novo('bruno', 'Bruno', 'Vendedor', sector=loja)
carlos = novo('carlos', 'Carlos', 'Saiu', sector=loja, is_active=False)
dora = novo('dora', 'Dora', 'Outra', sector=outra_loja)

lote = ExclusionSyncBatch.objects.create(record_type=TipoBase.EXCLUSAO, record_count=10, created_by=chefe)
ContestationHistory.objects.create(action='synced', user=chefe, notes='ZZ sync do teste')


def venda(numero, vendedor, filial='ZZ EQUIPE'):
    return ExclusionRecord.objects.create(
        sync_batch=lote, filial=filial, vendedor=vendedor, receita=100 + numero, pilar='MOVEL',
        numero_venda=f'ZZE{numero}', data_venda='01/11/2026', nome_cliente=f'ZZ Cliente {numero}',
        record_type=TipoBase.EXCLUSAO)


a1, a2, a3, a4 = (venda(1, 'ANA VENDEDORA'), venda(2, 'ANA VENDEDORA'), venda(3, 'ANA VENDEDORA'),
                  venda(9, 'ANA VENDEDORA'))
b1, b2 = venda(4, 'BRUNO VENDÊDOR'), venda(5, 'BRUNO VENDÊDOR')      # acento: o nome é normalizado
c1 = venda(6, 'CARLOS SAIU')
f1 = venda(7, 'FULANO SEM CADASTRO')
d1 = venda(8, 'DORA OUTRA', filial='ZZ OUTRA')

cc = Client(); cc.force_login(chefe)
cg = Client(); cg.force_login(gerente)
cg2 = Client(); cg2.force_login(gerente2)
cgf = Client(); cgf.force_login(gerente_fora)
ca = Client(); ca.force_login(ana)
cb = Client(); cb.force_login(bruno)

LISTA = '/contestacao/'
CONFERIR = '/contestacao/carrinho/conferir/'
ENVIAR = '/contestacao/contestar-lote/'
SALVAR = '/contestacao/carrinho/rascunho/salvar/'
SINCRONIZAR = '/contestacao/carrinho/rascunho/sincronizar/'


def nao(cliente, venda_, justificativa):
    return cliente.post(f'/contestacao/venda/{venda_.pk}/nao/', {'justificativa': justificativa})


def sim(cliente, venda_, motivo='ZZ motivo do teste', com_anexo=True, substituir=False):
    dados = {'exclusion_id': venda_.pk, 'reason': motivo}
    if com_anexo:
        dados['attachment'] = foto()
    if substituir:
        dados['substituir_nao'] = '1'
    return cliente.post(SALVAR, dados)


def acordo(cliente, venda_, acao, motivo=''):
    return cliente.post(f'/contestacao/venda/{venda_.pk}/nao/de-acordo/',
                        {'acao': acao, 'motivo': motivo, 'next': '/contestacao/'})


def manter(cliente, venda_, resposta):
    return cliente.post(f'/contestacao/venda/{venda_.pk}/nao/manter/', {'resposta': resposta, 'next': '/contestacao/'})


def enviar(cliente, ids):
    return cliente.post(ENVIAR, data=json.dumps({'ids': [v.pk for v in ids]}), content_type='application/json')


def conferir_loja(cliente):
    return cliente.post(CONFERIR, data=json.dumps({'loja': True, 'ids': []}), content_type='application/json')


def loja_estado():
    return equipe.estado_das_lojas(lote, ['ZZ EQUIPE'])['ZZ EQUIPE']


def resp(venda_):
    return RespostaNaoContesta.objects.filter(exclusion=venda_).first()


def ultimo(acao):
    return ContestationHistory.objects.filter(action=acao).order_by('-pk').first()


# --- A migração 0016 (antes de existir qualquer Não do fluxo novo) ----------
print('Ajuste dos Não antigos (migração 0016)')
mig = importlib.import_module('contestacao.migrations.0016_de_acordo_do_cn')
x1, x2, x3 = venda(21, 'ANA VENDEDORA'), venda(22, 'ANA VENDEDORA'), venda(23, 'FULANO SEM CADASTRO')
x_ids = [x1.pk, x2.pk, x3.pk]
velho1 = RespostaNaoContesta.objects.create(exclusion=x1, justificativa='Ana respondeu no fluxo antigo', cn=ana,
                                            preenchido_por=ana)
velho2 = RespostaNaoContesta.objects.create(exclusion=x2, justificativa='Gerente por ela no fluxo antigo', cn=ana,
                                            preenchido_por=gerente)
velho3 = RespostaNaoContesta.objects.create(exclusion=x3, justificativa='Sem CN no fluxo antigo', cn=None,
                                            preenchido_por=gerente)
mig.ajustar_respostas_antigas(django_apps, None)
for v in (velho1, velho2, velho3):
    v.refresh_from_db()
t('o Não que o próprio CN deu já conta como de acordo', velho1.situacao_cn == 'de_acordo'
  and velho1.cn_respondido_por_id == ana.pk)
t('o que o gerente deu por ele ganha 24 h da resposta', velho2.situacao_cn == 'aguardando'
  and abs(velho2.prazo_cn - velho2.atualizado_em - timedelta(hours=24)) < timedelta(seconds=2))
t('sem CN fica "sem CN"', velho3.situacao_cn == 'sem_cn')
RespostaNaoContesta.objects.all().delete()   # a migração roda uma vez; o resto do teste começa limpo
ExclusionRecord.objects.filter(pk__in=x_ids).delete()

# --- Quem é quem ------------------------------------------------------------
print('Quem é CN')
donos = equipe.donos_do_lote(lote)
t('Ana e Bruno viram CNs pelo nome (com acento normalizado)',
  donos[('ZZ EQUIPE', 'ANA VENDEDORA')] == ana.pk and donos[('ZZ EQUIPE', 'BRUNO VENDÊDOR')] == bruno.pk, donos)
t('quem saiu (inativo) e quem não tem cadastro ficam sem CN',
  donos[('ZZ EQUIPE', 'CARLOS SAIU')] is None and donos[('ZZ EQUIPE', 'FULANO SEM CADASTRO')] is None)
t('papéis', [equipe.papel(u) for u in (chefe, gerente, ana)] == ['central', 'gerente', 'cn'])

# --- O CN entra, vê só as dele e não responde --------------------------------
print('CN na tela: só vê')
r = ca.get(LISTA)
t('CN abre /contestacao/', r.status_code == 200, r.status_code)
ids_na_tela = {rec.pk for rec in r.context['records']} if r.status_code == 200 else set()
t('CN vê só as vendas dele', ids_na_tela == {a1.pk, a2.pk, a3.pk, a4.pk}, ids_na_tela)
html_ana = r.content.decode() if r.status_code == 200 else ''
t('painel do CN é o do de acordo', 'Seu de acordo neste ciclo' in html_ana
  and 'Quem responde as vendas é o gerente da loja' in html_ana)
t('CN não tem botões Sim/Não nas vendas', f'onclick="openReason({a1.pk})"' not in html_ana
  and f'onclick="markNotContestable({a1.pk})"' not in html_ana)
t('CN vê "Aguardando o gerente responder"', 'Aguardando o gerente responder' in html_ana)
t('menu mostra Contestação para o CN', r.context.get('can_access_contestacao_menu') is True)

print('CN não responde nem envia')
r = sim(ca, a1)
t('Sim do CN: 403', r.status_code == 403, r.status_code)
t('nenhum carrinho do CN', not ContestationCartDraft.objects.filter(user=ana).exists())
r = nao(ca, a2, 'nao vou contestar essa')
t('Não do CN: 403', r.status_code == 403, r.status_code)
t('envio do CN: 403', enviar(ca, [a1]).status_code == 403)
r = ca.post(SINCRONIZAR, data=json.dumps({'items': [{'exclusion_id': a1.pk, 'reason': 'carrinho velho'}]}),
            content_type='application/json')
t('carrinho antigo do CN não entra', not ContestationCartDraft.objects.filter(user=ana).exists(), r.content[:200])

# --- O gerente responde -------------------------------------------------------
print('Gerente responde o Não (justificativa obrigatória)')
r = nao(cg, a2, '')
t('Não sem justificativa: 400', r.status_code == 400 and r.json().get('campo') == 'justificativa', r.status_code)
t('Não curto demais: 400', nao(cg, a2, ' ok ').status_code == 400)
antes = timezone.now()
r = nao(cg, a2, 'Cliente cancelou de verdade')
ra2 = resp(a2)
t('Não grava: CN = Ana, respondido pelo gerente', r.status_code == 200 and ra2
  and ra2.cn_id == ana.pk and ra2.preenchido_por_id == gerente.pk, r.content[:200])
t('Não abre o prazo de 24 h do CN', ra2 and ra2.situacao_cn == 'aguardando'
  and abs((ra2.prazo_cn - antes) - timedelta(hours=24)) < timedelta(minutes=1), ra2 and ra2.prazo_cn)
j = r.json()
t('resposta JSON traz o prazo', j.get('situacao_cn') == 'aguardando' and j.get('prazo_cn'))
h = ultimo('nao_contesta')
t('histórico do Não: gerente fez, para a Ana', h.user_id == gerente.pk and h.extra_data['para_cn_id'] == ana.pk
  and h.extra_data['em_nome_do_cn'] is True and h.extra_data['prazo_cn'])
nao(cg, f1, 'Venda do Fulano não tem o que contestar')
nao(cg, c1, 'Carlos saiu, venda cancelada pelo cliente')
t('Não de venda sem CN no portal não espera de acordo',
  resp(f1).situacao_cn == 'sem_cn' and resp(c1).situacao_cn == 'sem_cn' and resp(f1).prazo_cn is None)
t('gerente não responde venda de outra loja', nao(cg, d1, 'Fora da loja dele').status_code == 404)

print('Envio das Sim não espera os de acordo')
sim(cg, a1, motivo='Gerente contestando a venda da Ana')
sim(cg, b1, motivo='Gerente contestando a venda do Bruno')
est = loja_estado()
t('loja: 2 Sim prontas, 1 Não esperando o CN', est['para_enviar'] == 2 and est['cn_aguardando'] == 1, est)
r = conferir_loja(cg)
j = r.json()
t('conferência da loja: a1 e b1 prontas, sem trava', r.status_code == 200
  and sorted(j['prontas']) == sorted([a1.pk, b1.pk]), j)
t('conferência avisa o que falta responder (sem travar)', j['lojas'] and j['lojas'][0]['faltam'] > 0, j.get('lojas'))
r = enviar(cg, [a1, b1])
j = r.json()
t('envio cria as 2 com o Não da a2 ainda esperando a Ana', sorted(j['criadas']) == sorted([a1.pk, b1.pk])
  and resp(a2).situacao_cn == 'aguardando', j)
ca1 = Contestation.objects.get(exclusion=a1)
cb1 = Contestation.objects.get(exclusion=b1)
t('contestação no nome do CN, preenchida e enviada pelo gerente',
  (ca1.requester_id, ca1.preenchido_por_id, ca1.enviado_por_id) == (ana.pk, gerente.pk, gerente.pk))
t('reenviar não duplica', enviar(cg, [a1, b1]).json()['criadas'] == [])

print('CN dá o de acordo no Não')
html_ana = ca.get(LISTA).content.decode()
t('Ana vê o Não do gerente com o prazo e os botões', 'Cliente cancelou de verdade' in html_ana
  and f'/contestacao/venda/{a2.pk}/nao/de-acordo/' in html_ana and 'esperando você' in html_ana)
r = acordo(cb, a2, 'de_acordo')
t('Bruno não dá de acordo na venda da Ana', resp(a2).situacao_cn == 'aguardando')
r = acordo(cg, a2, 'de_acordo')
t('o gerente também não (é do CN)', resp(a2).situacao_cn == 'aguardando')
r = acordo(ca, a2, 'de_acordo')
ra2 = resp(a2)
t('de acordo da Ana registrado', r.status_code == 302 and ra2.situacao_cn == 'de_acordo'
  and ra2.cn_respondido_por_id == ana.pk and ra2.cn_respondido_em)
h = ultimo('cn_de_acordo')
t('histórico: de acordo da Ana, com a justificativa do gerente', h and h.user_id == ana.pk
  and h.extra_data['justificativa_do_gerente'] == 'Cliente cancelou de verdade')
acordo(ca, a2, 'discorda', 'mudei de ideia depois')
t('responder de novo não muda nada', resp(a2).situacao_cn == 'de_acordo')

print('Mesmo texto mantém o de acordo; texto novo reabre o prazo')
nao(cg, a2, 'Cliente cancelou de verdade')
t('mesma justificativa: de acordo continua', resp(a2).situacao_cn == 'de_acordo')
nao(cg, a2, 'Cliente cancelou e pediu estorno')
t('justificativa nova: volta a esperar a Ana', resp(a2).situacao_cn == 'aguardando'
  and resp(a2).cn_respondido_em is None)
acordo(ca, a2, 'de_acordo')

print('CN discorda: volta para o gerente')
nao(cg, a3, 'Venda duplicada na base')
acordo(ca, a3, 'discorda', 'ruim')
t('discordância sem motivo de verdade: recusada', resp(a3).situacao_cn == 'aguardando')
acordo(ca, a3, 'discorda', 'Não é duplicada, são duas linhas diferentes')
ra3 = resp(a3)
t('discordância registrada com o motivo', ra3.situacao_cn == 'discorda'
  and ra3.cn_motivo == 'Não é duplicada, são duas linhas diferentes')
t('histórico da discordância', ultimo('cn_discorda').user_id == ana.pk)
html_ger = cg.get(LISTA).content.decode()
t('painel do gerente mostra a discordância para decidir', 'discordância do CN para você decidir' in html_ger
  and 'Não é duplicada, são duas linhas diferentes' in html_ger and f'?venda={a3.pk}' in html_ger)
t('estado da loja conta a discordância', loja_estado()['cn_discorda'] == 1)
r = cg.get(LISTA + f'?venda={a3.pk}')
t('"Abrir a venda" filtra só ela, com Contestar e Manter', [rec.pk for rec in r.context['records']] == [a3.pk]
  and f'/contestacao/venda/{a3.pk}/nao/manter/' in r.content.decode())
manter(ca, a3, 'Eu mesma mantenho')
t('CN não decide a discordância', resp(a3).situacao_cn == 'discorda')
manter(cgf, a3, 'Gerente de outra loja')
t('gerente de outra loja também não', resp(a3).situacao_cn == 'discorda')
manter(cg, a3, 'ok')
t('manter sem resposta de verdade: recusado', resp(a3).situacao_cn == 'discorda')
manter(cg, a3, 'Conferi no sistema: é duplicada sim')
ra3 = resp(a3)
t('gerente manteve o Não, com a resposta', ra3.situacao_cn == 'mantido' and ra3.mantido_por_id == gerente.pk
  and ra3.gerente_resposta == 'Conferi no sistema: é duplicada sim')
t('histórico do manter', ultimo('nao_mantido').user_id == gerente.pk)
t('Ana vê a resposta do gerente', 'Conferi no sistema: é duplicada sim' in ca.get(LISTA).content.decode())

print('Discordância vira contestação')
nao(cg, b2, 'Bruno disse que o cliente desistiu')
acordo(cb, b2, 'discorda', 'Cliente não desistiu, tenho o print da conversa')
r = sim(cg, b2, motivo='CN mostrou o print: contestar', substituir=True)
t('Sim do gerente troca o Não (com histórico)', r.status_code == 200 and resp(b2) is None
  and ultimo('nao_desfeito').user_id == gerente.pk)
j = enviar(cg, [b2]).json()
t('e a venda vai na contestação', j['criadas'] == [b2.pk], j)

print('24 h sem resposta: de acordo automático')
nao(cg, a4, 'Venda trocada por outra linha')
ra4 = resp(a4)
prazo = timezone.now() - timedelta(minutes=5)
RespostaNaoContesta.objects.filter(pk=ra4.pk).update(prazo_cn=prazo)
html_ana = ca.get(LISTA).content.decode()
ra4 = resp(a4)
t('passou o prazo: de acordo automático', ra4.situacao_cn == 'automatico'
  and abs(ra4.cn_respondido_em - prazo) < timedelta(seconds=1) and ra4.cn_respondido_por_id is None)
h = ultimo('cn_de_acordo_auto')
t('histórico: "De acordo automático por prazo expirado", pelo sistema', h and h.user_id is None
  and h.notes == 'De acordo automático por prazo expirado' and h.extra_data['exclusion_id'] == a4.pk)
t('a tela sinaliza "De acordo automático por prazo expirado"', 'De acordo automático por prazo expirado' in html_ana)
acordo(ca, a4, 'discorda', 'Agora quero discordar')
t('depois do prazo o CN não muda mais', resp(a4).situacao_cn == 'automatico')
t('expira uma vez só', ContestationHistory.objects.filter(action='cn_de_acordo_auto').count() == 1)
cg.get(LISTA)
t('outra tela não duplica o automático', ContestationHistory.objects.filter(action='cn_de_acordo_auto').count() == 1)
est = loja_estado()
ana_p = next(p for p in est['participantes'] if p['vendedor'] == 'ANA VENDEDORA')
t('estado da Ana: 1 de acordo, 1 automático, 1 mantido', (ana_p['cn_de_acordo'], ana_p['cn_automatico'],
  ana_p['cn_mantido']) == (1, 1, 1), ana_p)

print('Parecer do gestor: CN vê, só o gerente responde')
ca1.reject(chefe, 'ZZ rejeitada pelo gestor')
r = ca.get('/contestacao/minhas/')
t('Ana vê a contestação dela e o parecer', [c.pk for c in r.context['contestations']] == [ca1.pk]
  and 'quem dá o de acordo ou questiona o time de conferência de vendas é o gerente da loja' in r.content.decode())
t('Ana não ganha os botões de de acordo', f'/contestacao/{ca1.pk}/confirmar/' not in r.content.decode())
r = ca.get(f'/contestacao/{ca1.pk}/')
t('detalhe: Ana vê o parecer sem poder responder', r.status_code == 200
  and 'questiona o time de conferência de vendas' in r.content.decode()
  and f'/contestacao/{ca1.pk}/negar/' not in r.content.decode())
ca.post(f'/contestacao/{ca1.pk}/negar/', {'confirmation_notes': 'Discordo do gestor'})
ca1.refresh_from_db()
t('Ana não questiona (servidor)', ca1.status == 'rejected' and ca1.confirmed_by_id is None)
ca.post(f'/contestacao/{ca1.pk}/confirmar/', {'confirmation_notes': 'ok'})
ca1.refresh_from_db()
t('Ana não dá de acordo (servidor)', ca1.confirmed_by_id is None)
cgf.post(f'/contestacao/{ca1.pk}/negar/', {'confirmation_notes': 'outra loja'})
ca1.refresh_from_db()
t('gerente de outra loja também não', ca1.confirmed_by_id is None)
r = cg.get('/contestacao/minhas/')
t('o gerente que enviou vê os botões', f'/contestacao/{ca1.pk}/negar/' in r.content.decode())
cg.post(f'/contestacao/{ca1.pk}/negar/', {'confirmation_notes': 'Questionando a conferência'})
ca1.refresh_from_db()
t('gerente questiona (Negar) o parecer', ca1.status == 'denied' and ca1.confirmed_by_id == gerente.pk)
cb1.reject(chefe, 'ZZ rejeitada')
cg2.post(f'/contestacao/{cb1.pk}/confirmar/', {'confirmation_notes': 'ok'})
cb1.refresh_from_db()
t('outro gerente da mesma loja também pode (quem enviou pode ter saído)', cb1.confirmed_by_id == gerente2.pk)

print('Gestão central')
ContestationCartDraft.objects.create(user=chefe, exclusion=d1, reason='ZZ central').attachment.save(
    'd1.jpg', foto(), save=True)
r = enviar(cc, [d1])
cd1 = Contestation.objects.filter(exclusion=d1).first()
t('SUPERADMIN envia, no nome da Dora', r.json()['criadas'] == [d1.pk] and cd1
  and cd1.requester_id == dora.pk and cd1.enviado_por_id == chefe.pk, r.json())

print('Tela e JS')
r = cg.get(LISTA)
html = r.content.decode()
t('gerente: tela abre com as vendas da loja', r.status_code == 200 and len(r.context['records']) == 8, len(r.context['records']))
t('painel do gerente: loja e de acordo dos CNs', 'Loja neste ciclo' in html and 'De acordo do CN nos Não' in html
  and 'Enviar contestações da loja' in html)
scripts = re.findall(r'<script>(.*?)</script>', html, re.S)
js = next((sc for sc in scripts if 'window.pendingReasons' in sc), '')
arq = PASTA / 'tela.js'
arq.write_text(js, encoding='utf-8')
try:
    chk = subprocess.run(['node', '--check', str(arq)], capture_output=True, text=True, timeout=30)
    t('JS da tela passa no node --check', chk.returncode == 0, chk.stderr[:400])
except FileNotFoundError:
    print('  (node não encontrado: pulei o --check)')
t('JS tem o Não com prazo e o envio da loja sem trava',
  'window.confirmarNao' in js and 'window.enviarDaLoja' in js and 'aguardando_cns' not in js
  and "corpo.situacao_cn === 'aguardando'" in js)
r = cg.get(LISTA + '?vendedor=BRUNO%20VEND%C3%8ADOR')
t('"Ver vendas" do gerente filtra pelo CN', {rec.pk for rec in r.context['records']} == {b1.pk, b2.pk})
hist = cc.get('/contestacao/historico/').content.decode()
t('histórico mostra os registros novos', 'CN de acordo' in hist and 'CN discordou' in hist
  and 'Não mantido' in hist and 'De acordo automático por prazo expirado' in hist)
t('rotas do "pronto" saíram', cg.post('/contestacao/ciclo/pronto/', {}).status_code == 404)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
