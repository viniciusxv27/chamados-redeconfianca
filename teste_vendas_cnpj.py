"""/vendas/nova (29/10/2026): cliente por CPF **ou CNPJ**; empresa nova vem preenchida da Receita.
/vendas (29/10/2026): o valor dos serviços aparece embaixo do valor da venda.

Cobre:
- validação e máscara do CNPJ (verificadores), CPF continua igual;
- a consulta pública: BrasilAPI recusando (429) passa para a próxima, "não existe" (404) para na
  hora, tudo fora do ar devolve aviso, o resultado fica no cache, e os 3 formatos de resposta;
- a busca, o cadastro e a venda com CNPJ (Cliente guarda os 14 números);
- a sincronização do Vivo GO passa a trazer cliente PJ;
- a lista de vendas mostra o valor dos serviços (/mês).

Roda num sqlite descartável; nenhuma API de verdade é chamada (requests.get é falso).
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-vendas-cnpj-'))
(PASTA / 'zz_vendas_cnpj_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cnpj'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cnpj-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_vendas_cnpj_settings'
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

import requests
from django.core.cache import cache

from vendas import cnpj, slv, vivogo
from vendas.models import Cliente, CompraVivoGo, Plano, Venda


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


mock.patch.object(requests, 'post', sem_rede).start()
mock.patch.object(requests, 'get', sem_rede).start()

print('Validação')
t('CNPJ válido (Telefônica)', slv.cnpj_valido('02.558.157/0001-62'))
t('CNPJ válido (exemplo da Receita)', slv.cnpj_valido('11222333000181'))
t('verificador errado não passa', not slv.cnpj_valido('02558157000163'))
t('números repetidos não passam', not slv.cnpj_valido('11111111111111'))
t('CPF continua valendo como documento', slv.documento_valido('529.982.247-25') and not slv.documento_valido('52998224724'))
t('formata o CNPJ', slv.formatar_cpf('02558157000162') == '02.558.157/0001-62')
t('formata o CPF como antes', slv.formatar_cpf('52998224725') == '529.982.247-25')


class Resp:
    def __init__(self, status, dados=None):
        self.status_code, self._dados = status, dados

    def json(self):
        if self._dados is None:
            raise ValueError('sem json')
        return self._dados


RECEITA = {'razao_social': 'ZZ EMPRESA TESTE LTDA', 'nome_fantasia': 'ZZ FANTASIA', 'descricao_situacao_cadastral': 'ATIVA',
           'ddd_telefone_1': '2733334444', 'cep': '29100000', 'descricao_tipo_de_logradouro': 'AVENIDA',
           'logradouro': 'DAS FLORES', 'numero': '10', 'complemento': 'SALA 2', 'bairro': 'CENTRO',
           'municipio': 'VILA VELHA', 'uf': 'es', 'email': 'CONTATO@ZZ.TESTE'}
pedidos = []


def falso(respostas):
    def get(url, **k):
        pedidos.append(url)
        r = respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return get


print('Consulta pública')
CNPJ = '11222333000181'
with mock.patch.object(cnpj.requests, 'get', falso([Resp(429), Resp(200, RECEITA)])):
    r = cnpj.consultar('11.222.333/0001-81')
t('BrasilAPI recusou (429): a Minha Receita respondeu', r['ok'] and r['fonte'] == 'Minha Receita', r)
e = r.get('empresa', {})
t('razão social, telefone, CEP e cidade', e.get('razao_social') == 'ZZ EMPRESA TESTE LTDA' and e.get('telefone') == '2733334444'
  and e.get('cep') == '29100000' and e.get('cidade') == 'VILA VELHA' and e.get('uf') == 'ES', e)
t('logradouro junta o tipo', e.get('logradouro') == 'AVENIDA DAS FLORES', e.get('logradouro'))
n = len(pedidos)
r2 = cnpj.consultar(CNPJ)
t('segunda consulta vem do cache (sem rede)', r2 == r and len(pedidos) == n)

cache.clear(); pedidos.clear()
with mock.patch.object(cnpj.requests, 'get', falso([Resp(404)])):
    r = cnpj.consultar('02558157000162')
t('CNPJ que não existe para na primeira API', not r['ok'] and r.get('nao_encontrado') and len(pedidos) == 1, (r, pedidos))

cache.clear(); pedidos.clear()
with mock.patch.object(cnpj.requests, 'get', falso([requests.Timeout(), Resp(503), Resp(200, None),
                                                    requests.ConnectionError()])):
    r = cnpj.consultar('02558157000162')
t('tudo fora do ar: aviso para preencher à mão', not r['ok'] and 'à mão' in r['erro'] and len(pedidos) == 4, r)
t('falha não vai para o cache', cache.get('vendas:cnpj:02558157000162') is None)

cache.clear()
WS = {'razao_social': 'ZZ WS LTDA', 'estabelecimento': {'nome_fantasia': None, 'situacao_cadastral': 'Baixada', 'ddd1': '27',
      'telefone1': '30001000', 'cep': '29000000', 'tipo_logradouro': 'RUA', 'logradouro': 'UM', 'numero': '5',
      'complemento': None, 'bairro': 'B', 'cidade': {'nome': 'Vitória'}, 'estado': {'sigla': 'ES'}, 'email': None}}
with mock.patch.object(cnpj.requests, 'get', falso([Resp(429), Resp(429), Resp(200, WS)])):
    r = cnpj.consultar('02558157000162')
t('formato do CNPJ.ws', r['ok'] and r['fonte'] == 'CNPJ.ws' and r['empresa']['situacao'] == 'BAIXADA'
  and r['empresa']['telefone'] == '2730001000' and r['empresa']['cidade'] == 'Vitória' and r['empresa']['logradouro'] == 'RUA UM', r)

cache.clear()
RWS = {'nome': 'ZZ RWS LTDA', 'fantasia': '', 'situacao': 'ATIVA', 'telefone': '(27) 3000-2000 / (27) 9999-0000',
       'cep': '29.100-000', 'logradouro': 'RUA DOIS', 'numero': '7', 'complemento': '', 'bairro': 'C', 'municipio': 'SERRA', 'uf': 'ES'}
with mock.patch.object(cnpj.requests, 'get', falso([Resp(429), Resp(429), Resp(429), Resp(200, RWS)])):
    r = cnpj.consultar('02558157000162')
t('formato da ReceitaWS (1º telefone, CEP sem pontos)', r['ok'] and r['fonte'] == 'ReceitaWS'
  and r['empresa']['telefone'] == '2730002000' and r['empresa']['cep'] == '29100000', r)
cache.clear()
with mock.patch.object(cnpj.requests, 'get', falso([Resp(429), Resp(429), Resp(429), Resp(200, {'status': 'ERROR', 'message': 'CNPJ inválido'})])):
    r = cnpj.consultar('02558157000162')
t('ReceitaWS dizendo "erro" conta como não encontrado', not r['ok'] and r.get('nao_encontrado'), r)
t('CNPJ inválido nem consulta', cnpj.consultar('123') == {'ok': False, 'erro': 'CNPJ inválido.'})

print('Tela da venda')
loja = Sector.objects.create(name='ZZ LOJA CNPJ')
chefe = User.objects.create_user(username='zzcnpj.super', email='zzcnpj.super@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZCNPJ', last_name='Super', sector=loja, hierarchy='SUPERADMIN')
cs = Client(); cs.force_login(chefe)
padrao = User.objects.create_user(username='zzcnpj.padrao', email='zzcnpj.padrao@exemplo-teste.local', password='S3nha!teste',
                                  first_name='ZZCNPJ', last_name='Padrao', sector=loja, hierarchy='PADRAO')
cp = Client(); cp.force_login(padrao)
t('quem não lança venda não consulta CNPJ', cp.get('/vendas/api/cnpj/', {'cnpj': CNPJ}).status_code == 403)
cache.clear()
with mock.patch.object(cnpj.requests, 'get', falso([Resp(200, RECEITA)])):
    j = cs.get('/vendas/api/cnpj/', {'cnpj': '11.222.333/0001-81'}).json()
t('API do portal devolve a empresa', j.get('ok') and j['empresa']['razao_social'] == 'ZZ EMPRESA TESTE LTDA', j)

j = cs.get('/vendas/api/cliente/', {'cpf': '11.222.333/0001-81'}).json()
t('busca por CNPJ: não encontrado, já formatado', j.get('ok') and j['encontrado'] is False and j['cpf'] == CNPJ
  and j['cpf_formatado'] == '11.222.333/0001-81', j)
j = cs.get('/vendas/api/cliente/', {'cpf': '11222333000182'}).json()
t('CNPJ com verificador errado: erro do CNPJ', not j['ok'] and 'CNPJ inválido' in j['erro'], j)
j = cs.post('/vendas/api/cliente/salvar/', json.dumps({'cpf': CNPJ, 'nome': 'ZZ EMPRESA TESTE LTDA', 'telefone': '2733334444',
            'cep': '29100-000', 'cidade': 'VILA VELHA', 'uf': 'ES'}), content_type='application/json').json()
t('cadastra a empresa', j.get('ok') and j['criado'] and j['cliente']['cpf'] == CNPJ
  and j['cliente']['cpf_formatado'] == '11.222.333/0001-81', j)
t('Cliente guarda os 14 números', Cliente.objects.filter(cpf=CNPJ).exists() and Cliente.objects.get(cpf=CNPJ).e_empresa)
j = cs.get('/vendas/api/cliente/', {'cpf': CNPJ}).json()
t('na segunda vez é encontrada', j.get('encontrado') and j['cliente']['nome'] == 'ZZ EMPRESA TESTE LTDA', j)
t('a ficha do cliente abre pelo CNPJ', cs.get(f'/vendas/clientes/{CNPJ}/').status_code == 200)

pos = Plano.objects.create(nome='ZZ Pós 40GB', segmentacao='POS', valor=D('130.00'))
r = cs.post('/vendas/nova/', json.dumps({'cliente': {'cpf': CNPJ, 'nome': 'ZZ EMPRESA TESTE LTDA'}, 'linhas': ['27998765432'],
            'servicos': [{'tipo': 'ALTA', 'plano_id': pos.id}]}), content_type='application/json').json()
t('venda com CNPJ fecha', r.get('ok'), r)
v = Venda.objects.filter(pk=r.get('id')).first()
t('a venda guarda o CNPJ formatado', v and v.cliente_cpf == '11.222.333/0001-81', v and v.cliente_cpf)
r = cs.post('/vendas/nova/', json.dumps({'cliente': {'cpf': '11222333000100', 'nome': 'X'}, 'linhas': ['27998765432'],
            'servicos': [{'tipo': 'ALTA', 'plano_id': pos.id}]}), content_type='application/json').json()
t('venda com CNPJ inválido não passa', not r.get('ok') and any('CPF/CNPJ do cliente inválido' in e for e in r['erros']), r)

h = cs.get('/vendas/nova/').content.decode()
t('a tela aceita CNPJ (máscara, verificador e Receita)', 'CPF ou CNPJ do cliente' in h and 'function mascaraDoc' in h
  and 'function cnpjValido' in h and '/vendas/api/cnpj/' in h and 'maxlength="18"' in h)

print('Lista de vendas')
h = cs.get('/vendas/').content.decode()
t('o valor dos serviços aparece embaixo (R$ 130,00/mês)', 'R$ 130,00<span class="font-normal text-pink-500">/mês</span>' in h)

print('Vivo GO')
CompraVivoGo.objects.create(chave='zz1', tipo='S', cpf='02558157000162', nome_cliente='ZZ PJ DO VIVOGO', data_venda=date(2026, 9, 1),
                            pdv='ZZ LOJA CNPJ', receita=D('99.00'))
CompraVivoGo.objects.create(chave='zz2', tipo='S', cpf='123', nome_cliente='lixo', data_venda=date(2026, 9, 1))
vivogo.atualizar_clientes(['02558157000162', '123'])
c = Cliente.objects.filter(cpf='02558157000162').first()
t('a sincronização traz o cliente PJ', c and c.nome == 'ZZ PJ DO VIVOGO' and c.origem == 'VIVOGO')
t('e continua ignorando documento torto', not Cliente.objects.filter(cpf='123').exists())

print(f'\n{ok} OK, {fail} falha(s)')
