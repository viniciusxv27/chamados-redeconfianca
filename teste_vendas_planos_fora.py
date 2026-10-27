"""/vendas (27/10/2026): "Planos que não têm no portfólio".

Pedido: campo na Parametrização, que só aparece na venda de serviço de Reativação, Migração e
Troca de plano; a lista sobe do VivoGo (vendas_servicos até 27/10/2026, nome e valor), sem o
que já existe como plano normal de qualquer segmentação.

Cobre:
- a importação junta grafias do mesmo plano, classifica a segmentação, pula SVA e plano normal
  (sem acento/caixa) e, rodando de novo, só atualiza;
- a Parametrização cadastra à mão, recusa nome de plano normal e grava no histórico;
- a busca acha sem acento e esconde inativos do vendedor;
- na venda, o plano fora do portfólio vira o plano anterior (nome, valor e delta) em Troca de
  plano, Migração e Reativação — e é ignorado na Alta.

Roda num sqlite descartável; nada consulta o VivoGo.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-planos-fora-'))
(PASTA / 'zz_planos_fora_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pf'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-pf-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_planos_fora_settings'
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


import json
from datetime import date
from decimal import Decimal as D
from unittest import mock as _mock

import requests

from vendas import planos_fora
from vendas.models import Plano, PlanoForaPortfolio, RegistroAlteracao, VendaServico


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


_mock.patch.object(requests, 'post', sem_rede).start()
_mock.patch.object(requests, 'get', sem_rede).start()

loja = Sector.objects.create(name='ZZ LOJA PLANOS')
chefe = User.objects.create_user(username='zzpf.super', email='zzpf.super@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZPF', last_name='Super', sector=loja, hierarchy='SUPERADMIN')
cs = Client(); cs.force_login(chefe)
pos = Plano.objects.create(nome='Vivo Pós Essencial 40GB', segmentacao='POS', valor=D('130.00'))
ctrl = Plano.objects.create(nome='Controle 15GB', segmentacao='CONTROLE', valor=D('62.00'))

print('Importação do VivoGo')
linhas = [
    {'nome': 'Vivo Controle Fatura 12GB (9GB + 3GB)', 'valor': D('59.00'), 'tipo': 'Controle', 'vendas': 10, 'ultima': date(2026, 2, 10)},
    {'nome': 'VIVO CONTROLE FATURA 12GB (9GB + 3GB)', 'valor': D('59.00'), 'tipo': 'Controle', 'vendas': 3, 'ultima': date(2026, 5, 1)},
    {'nome': 'Vivo Controle Fatura 12GB (9GB + 3GB)', 'valor': D('55.00'), 'tipo': 'Controle', 'vendas': 7, 'ultima': date(2025, 1, 1)},
    {'nome': 'Vivo Total 500 Mbps (Total Essencial)', 'valor': D('85.00'), 'tipo': 'Fixa Massiva + Premium', 'vendas': 4, 'ultima': None},
    {'nome': 'Plano Pós Antigo 30GB', 'valor': D('99.00'), 'tipo': 'Pós', 'vendas': 2, 'ultima': date(2024, 3, 3)},
    {'nome': 'vivo pos essencial  40gb', 'valor': D('120.00'), 'tipo': 'Pós', 'vendas': 5, 'ultima': None},   # já é normal
]
r = planos_fora.importar_do_vivogo(linhas=linhas)
t('sobe 4, pula o que já é plano normal (sem caixa/espaço)', (r['novos'], r['ja_normais']) == (4, 1), r)
f59 = PlanoForaPortfolio.objects.get(nome__iexact='Vivo Controle Fatura 12GB (9GB + 3GB)', valor=D('59.00'))
t('grafias diferentes viram um só, com a mais usada e a soma das vendas',
  f59.nome == 'Vivo Controle Fatura 12GB (9GB + 3GB)' and f59.vendas == 13 and f59.ultima_venda == date(2026, 5, 1))
t('mesmo nome com outro valor é outro item', PlanoForaPortfolio.objects.filter(valor=D('55.00')).count() == 1)
t('segmentação vem do tipo do plano (Fixa → Fibra)',
  PlanoForaPortfolio.objects.get(valor=D('85.00')).segmentacao == 'FIBRA' and f59.segmentacao == 'CONTROLE')
r = planos_fora.importar_do_vivogo(linhas=linhas[:1])
f59.refresh_from_db()
t('rodar de novo só atualiza', r['novos'] == 0 and r['atualizados'] == 1 and f59.vendas == 10)

print('Parametrização')
h = cs.get('/vendas/parametros/').content.decode()
t('a seção aparece com a contagem', 'Planos que não têm no portfólio' in h and 'Atualizar do VivoGo' in h)
r = cs.post('/vendas/parametros/fora-do-portfolio/', {'nome': 'Controle  15GB', 'valor': '50,00'}, follow=True)
t('nome de plano normal é recusado', 'já é plano normal' in r.content.decode()
  and not PlanoForaPortfolio.objects.filter(nome__icontains='Controle 15GB').exists())
cs.post('/vendas/parametros/fora-do-portfolio/', {'nome': 'Plano Família Antigo', 'valor': '210,00', 'segmentacao': 'POS'})
manual = PlanoForaPortfolio.objects.filter(nome='Plano Família Antigo', origem='MANUAL').first()
t('cadastro manual grava e vai para o histórico', manual and manual.valor == D('210.00')
  and RegistroAlteracao.objects.filter(tipo='PLANO_FORA', objeto_id=manual.id).exists())
antigo = PlanoForaPortfolio.objects.get(nome='Plano Pós Antigo 30GB')
cs.post('/vendas/parametros/fora-do-portfolio/', {'id': antigo.id, 'nome': antigo.nome, 'valor': '99,00', 'segmentacao': 'POS'})
antigo.refresh_from_db()
t('desmarcar "Ativo" inativa', not antigo.ativo)

print('Busca')
j = cs.get('/vendas/api/planos-fora/', {'q': 'familia'}).json()['itens']
t('acha sem acento', [i['nome'] for i in j] == ['Plano Família Antigo'], j)
t('o vendedor não vê inativo', not cs.get('/vendas/api/planos-fora/', {'q': 'pos antigo'}).json()['itens'])
t('a Parametrização vê inativo', cs.get('/vendas/api/planos-fora/', {'q': 'pos antigo', 'admin': '1'}).json()['itens'][0]['ativo'] is False)

print('Venda de serviço')
CPF = '52998224725'


def vender(servicos):
    r = cs.post('/vendas/nova/', json.dumps({'cliente': {'cpf': CPF, 'nome': 'Zz Cliente Planos'}, 'linhas': ['27998765432'],
                                              'plano_anterior': {'id': ctrl.id}, 'servicos': servicos}),
                content_type='application/json')
    return r.json()


j = vender([{'tipo': 'TROCA_PLANO', 'plano_id': pos.id, 'plano_fora_id': f59.id},
            {'tipo': 'MIGRACAO', 'plano_id': pos.id, 'plano_anterior_id': ctrl.id, 'plano_fora_id': manual.id},
            {'tipo': 'REATIVACAO', 'plano_id': pos.id, 'plano_fora_id': f59.id},
            {'tipo': 'ALTA', 'plano_id': pos.id, 'plano_fora_id': f59.id}])
t('a venda fecha', j.get('ok'), j)
sv = {s.tipo_servico: s for s in VendaServico.objects.filter(venda_id=j.get('id'))}
tp = sv.get('TROCA_PLANO')
t('Troca de plano: o plano fora vira o anterior (nome, valor e delta)', tp and tp.plano_anterior_fora_id == f59.id
  and tp.plano_anterior_nome == f59.nome and tp.valor_anterior == D('59.00') and tp.delta == D('71.00')
  and tp.plano_anterior_id is None, tp and (tp.plano_anterior_nome, tp.valor_anterior, tp.delta))
mg = sv.get('MIGRACAO')
t('Migração: vale mais que o plano anterior escolhido na lista', mg and mg.plano_anterior_fora_id == manual.id
  and mg.valor_anterior == D('210.00'))
rv = sv.get('REATIVACAO')
t('Reativação também aceita', rv and rv.plano_anterior_fora_id == f59.id and rv.valor_anterior == D('59.00'))
al = sv.get('ALTA')
t('Alta ignora o plano fora do portfólio', al and al.plano_anterior_fora_id is None and al.plano_anterior_nome == ctrl.nome)
h = cs.get(f'/vendas/{j.get("id")}/').content.decode()
t('a ficha marca "fora do portfólio"', 'fora do portfólio' in h and f59.nome in h)
j = vender([{'tipo': 'TROCA_PLANO', 'plano_id': pos.id, 'plano_fora_id': 999999}])
t('plano fora que não existe não passa', not j.get('ok') and any('não existe mais' in e for e in j['erros']), j)

h = cs.get('/vendas/nova/').content.decode()
t('a tela da venda tem a busca do plano fora (só nos três serviços)', 'campoPlanoFora()' in h
  and "if (t === 'ALTA') html = seletorPlano('Segmentação e plano *', 'novo', todos);" in h)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
