"""Auditoria D-1 (/auditoria-d1/): a esteira portada do sistema Flask (11/11/2026).

Cobre: regras da tabela de correções (ação e receita validada), pontos de atenção (número fake, documento fake com
dígito verificador, documento com outro nome, duplicidade), gravação/reabrir/contestação procedente, improcedente e
desfazer (com os mesmos eventos do log do sistema antigo e o nome do usuário do portal como autor), Exportar mês no
layout do modelo_importacao_ged.csv (cp1252, ';'), planilha .xlsx, cruzamento SVA e Fixa com bases pequenas montadas
aqui (prévia, aplicar, reverter), WhatsApp (desligado não chama o envio; ligado manda só para o grupo de teste),
permissões por papel em telas e APIs (sem liberação ninguém entra; GESTOR não vê acessos/configuração) e
comentários de template.

O MySQL é um dublê: um SQLite no diretório temporário fazendo as vezes do banco vivogo (o SQL do módulo roda
nele com três traduções mecânicas: %s → ?, ON DUPLICATE KEY → ON CONFLICT, sem FOR UPDATE). pymysql.connect é
trocado por uma função que falha: nada toca o MySQL real. core.evolution.enviar_texto é um mock.
"""
import os
import pathlib
import re
import sys
import tempfile

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-auditoria-d1-'))
(PASTA / 'zz_auditoria_d1_settings.py').write_text(f"""
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
VIVOGO_MYSQL_URL = 'mysql://dublê:x@mysql-falso.invalid:3306/vivogo'
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-d1'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-d1-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_auditoria_d1_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import csv  # noqa: E402
import datetime as dt  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402
from decimal import Decimal  # noqa: E402
from unittest import mock  # noqa: E402
from urllib.parse import unquote  # noqa: E402

import openpyxl  # noqa: E402
import pymysql  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.core.cache import cache  # noqa: E402
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402

from auditoria_d1 import categorias, importacao_vivogo, regras, whatsapp_lojas  # noqa: E402
from auditoria_d1 import mysql as d1mysql  # noqa: E402
from auditoria_d1.models import AcessoAuditoriaD1, ConfiguracaoAuditoriaD1, DisparoWhatsappD1  # noqa: E402
from auditoria_d1.permissoes import pode_ver  # noqa: E402
from cursos.models import GrupoWhatsappLoja  # noqa: E402
from users.models import Sector  # noqa: E402

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


# ---------------------------------------------------------------- dublê do MySQL (SQLite)
def _proibido(*a, **k):
    raise AssertionError('teste tentou abrir conexão com o MySQL real')


pymysql.connect = _proibido

sqlite3.register_adapter(dt.date, lambda d: d.isoformat())
sqlite3.register_adapter(dt.datetime, lambda d: d.isoformat(sep=' '))
sqlite3.register_adapter(Decimal, float)
sqlite3.register_converter('DATE', lambda b: dt.date.fromisoformat(b.decode()[:10]))
sqlite3.register_converter('TIMESTAMP', lambda b: dt.datetime.fromisoformat(b.decode()))

BANCO_FALSO = sqlite3.connect(str(PASTA / 'vivogo.sqlite3'), detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
BANCO_FALSO.row_factory = lambda c, r: {col[0]: r[i] for i, col in enumerate(c.description)}
COLS_VENDA = ['ID_do_serviço', 'ID_da_venda', 'data_da_venda:DATE', 'PDV', 'Serviço_Técnico', 'Serviço', 'pilar',
              'Nome_do_vendedor', 'Nome_do_cliente', 'CPF_do_cliente', 'Documento_do_cliente', 'Plano_novo',
              'Plano_antigo', 'Nº_acesso', 'Nº_portabilidade', 'Valor_do_plano_novo:REAL', 'Valor_do_plano_antigo:REAL',
              'Receita:REAL', 'Status_do_Serviço', 'Status_BKO', 'Data_da_última_ativação:DATE', 'Data_de_ativação:DATE',
              'Cidade_do_cliente', 'UF_do_cliente', 'endereço_do_cliente', 'Observação', 'Grupo_do_tipo_de_plano',
              'Dia_de_vencimento', 'Data_da_instalação:DATE', 'iccid_3_g', 'Conta_Digital', 'Fidelizado',
              'Zerar_remuneração', 'Nº_protocolo', 'Nº_Protocolo_GED', 'Motivo_de_cancelamento', 'Status_da_Instalação',
              'Data_de_cancelamento_do_protocolo:DATE', 'usuário_cancelamento_do_protocolo']
BANCO_FALSO.executescript(
    'CREATE TABLE vendas_servicos (' + ', '.join(
        '`{}` {}'.format(c.split(':')[0], c.split(':')[1] if ':' in c else 'TEXT') for c in COLS_VENDA) + ');'
    + """
CREATE TABLE verificacao_vendas (
  id_servico TEXT PRIMARY KEY, id_venda TEXT, data_venda DATE, pdv TEXT, status TEXT NOT NULL,
  correcao_vivogo TEXT, tipo_penalidade TEXT, acao_necessaria TEXT, receita_original REAL, receita_correta REAL,
  plano_correto TEXT, linha_correta TEXT, documento_correto TEXT, receita_validada REAL,
  alterar_vivogo INTEGER NOT NULL DEFAULT 0, observacao TEXT, verificado_por TEXT, verificado_em TIMESTAMP,
  atualizado_em TIMESTAMP, origem TEXT NOT NULL DEFAULT 'MANUAL', confianca TEXT, cruzamento_anomes TEXT,
  importacao_id INTEGER, verificacao_anterior TEXT, contestacao_decisao TEXT, contestacao_solicitante TEXT,
  contestacao_motivo TEXT, contestacao_por TEXT, contestacao_em TIMESTAMP, divergencia_contestada TEXT);
CREATE TABLE verificacao_vendas_log (id INTEGER PRIMARY KEY AUTOINCREMENT, id_servico TEXT NOT NULL,
  evento TEXT NOT NULL, dados TEXT, usuario TEXT, em TIMESTAMP NOT NULL);
CREATE TABLE cruzamento_importacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, origem TEXT NOT NULL, anomes TEXT NOT NULL,
  arquivo TEXT, base_de DATE, base_ate DATE, linhas INTEGER NOT NULL DEFAULT 0, aplicadas INTEGER NOT NULL DEFAULT 0,
  sugestoes INTEGER NOT NULL DEFAULT 0, revertidas INTEGER NOT NULL DEFAULT 0,
  manuais_sobrescritas INTEGER NOT NULL DEFAULT 0, nao_existe INTEGER NOT NULL DEFAULT 0, resultado TEXT,
  usuario TEXT, em TIMESTAMP NOT NULL, meses TEXT);
CREATE TABLE cruzamento_sugestoes (id INTEGER PRIMARY KEY AUTOINCREMENT, origem TEXT NOT NULL, anomes TEXT NOT NULL,
  importacao_id INTEGER NOT NULL, id_venda TEXT NOT NULL, id_servico TEXT, data_venda DATE, servico_base TEXT,
  confianca TEXT, motivo TEXT, correcao TEXT, penalidade TEXT);
""")


def traduzir(sql):
    sql = sql.replace('%s', '?').replace(' FOR UPDATE', '')
    sql = sql.replace('ON DUPLICATE KEY UPDATE', 'ON CONFLICT(id_servico) DO UPDATE SET')
    sql = re.sub(r'VALUES\((\w+)\)', r'excluded.\1', sql)
    sql = sql.replace('IN ((?, ?)', 'IN (VALUES (?, ?)')
    return sql


SQL_EXECUTADO = []


class CursorFalso:
    def __init__(self):
        self.c = BANCO_FALSO.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.c.close()

    def execute(self, sql, params=()):
        SQL_EXECUTADO.append(sql)
        self.c.execute(traduzir(sql), list(params))

    def executemany(self, sql, linhas):
        SQL_EXECUTADO.append(sql)
        linhas = [list(x) for x in linhas]
        if linhas:
            self.c.executemany(traduzir(sql), linhas)

    def fetchall(self):
        return self.c.fetchall()

    def fetchone(self):
        return self.c.fetchone()

    @property
    def lastrowid(self):
        return self.c.lastrowid

    @property
    def rowcount(self):
        return self.c.rowcount


class ConexaoFalsa:
    def cursor(self):
        return CursorFalso()

    def commit(self):
        BANCO_FALSO.commit()

    def rollback(self):
        BANCO_FALSO.rollback()

    def close(self):
        pass


d1mysql.conectar = lambda: ConexaoFalsa()


def q(sql, params=()):
    return BANCO_FALSO.execute(sql, params).fetchall()


def venda(id_servico, id_venda, data, pdv='LARANJEIRAS', servico='Alta Controle', grupo='Alta', pilar='Móvel',
          cliente='MARIA DA SILVA', cpf='52998224725', documento=None, plano='VIVO CONTROLE 10GB', acesso='27998761234',
          receita=59.9, valor=59.9, valor_antigo=None, status='Ativo', bko='Não avaliado', vendedor='JOAO VENDEDOR',
          protocolo=None, endereco='Rua A, 10, Bairro Centro, CEP 29000-000', **extra):
    dados = {'ID_do_serviço': id_servico, 'ID_da_venda': id_venda, 'data_da_venda': data, 'PDV': pdv,
             'Serviço_Técnico': servico, 'Serviço': grupo, 'pilar': pilar, 'Nome_do_vendedor': vendedor,
             'Nome_do_cliente': cliente, 'CPF_do_cliente': cpf, 'Documento_do_cliente': documento or cpf,
             'Plano_novo': plano, 'Nº_acesso': acesso, 'Receita': receita, 'Valor_do_plano_novo': valor,
             'Valor_do_plano_antigo': valor_antigo, 'Status_do_Serviço': status, 'Status_BKO': bko,
             'Nº_protocolo': protocolo, 'endereço_do_cliente': endereco, **extra}
    cols = list(dados)
    BANCO_FALSO.execute(f"INSERT INTO vendas_servicos ({', '.join('`' + c + '`' for c in cols)}) "
                        f"VALUES ({', '.join('?' * len(cols))})", [dados[c] for c in cols])
    BANCO_FALSO.commit()


# ---------------------------------------------------------------- pessoas
loja = Sector.objects.create(name='LARANJEIRAS')
loja2 = Sector.objects.create(name='Loja Glória')


def pessoa(nome, **kw):
    return User.objects.create_user(username=nome, email=f'{nome}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=nome.split('.')[-1].title(), last_name='Teste', sector=loja, **kw)


superadm = pessoa('zz.d1.super', hierarchy='SUPERADMIN')
consulta = pessoa('zz.d1.consulta')
auditor = pessoa('zz.d1.auditor')
gestor = pessoa('zz.d1.gestor')
semacesso = pessoa('zz.d1.nada', hierarchy='ADMIN')
AcessoAuditoriaD1.objects.create(user=consulta, papel='CONSULTA')
AcessoAuditoriaD1.objects.create(user=auditor, papel='AUDITOR')
AcessoAuditoriaD1.objects.create(user=gestor, papel='GESTOR')
cs, cc, ca, cg, cn = Client(), Client(), Client(), Client(), Client()
cs.force_login(superadm)
cc.force_login(consulta)
ca.force_login(auditor)
cg.force_login(gestor)
cn.force_login(semacesso)
NOME_AUDITOR = 'Auditor Teste'


def jpost(cliente, url, dados):
    return cliente.post(url, json.dumps(dados), content_type='application/json')


# =====================================================================================================
print('== REGRAS DA TABELA DE CORREÇÕES ==')
t('categorias na ordem da Pasta1.xlsx (atalhos 1–7)', categorias.CATEGORIAS['correcoes'] == [
    'VENDA INEXISTENTE', 'VALORES INCORRETOS', 'PLANO INCORRETO', 'LINHA INCORRETA', 'DIVERGÊNCIA CADASTRAL',
    'MOVIMENTO INELEGÍVEL', 'VENDA EM DUPLICIDADE'])
t('22 penalidades, SVA NÃO ATIVADO e FIBRA CANCELADA entre elas', len(categorias.CATEGORIAS['penalidades']) == 22
  and {'SVA NÃO ATIVADO', 'FIBRA CANCELADA'} <= set(categorias.CATEGORIAS['penalidades']))
ORIGEM = {'id_servico': '1', 'id_venda': '10', 'data_venda': dt.date(2026, 10, 5), 'pdv': 'LARANJEIRAS', 'receita': Decimal('59.90')}


def reg(**p):
    return regras.registro(p, ORIGEM)


r = reg(status='CONFORME')
t('Conforme: sem ação, receita validada = receita, não altera VivoGo',
  (r['acao_necessaria'], r['receita_validada'], r['alterar_vivogo']) == (None, 59.9, 0))
r = reg(status='DIVERGENTE', correcao='VENDA INEXISTENTE')
t('Venda inexistente: EXCLUIR VENDA, receita 0, altera', (r['acao_necessaria'], r['receita_validada'], r['alterar_vivogo']) == ('EXCLUIR VENDA', 0.0, 1))
try:
    reg(status='DIVERGENTE', correcao='MOVIMENTO INELEGÍVEL')
    t('Movimento inelegível exige penalidade', False)
except ValueError as e:
    t('Movimento inelegível exige penalidade', 'penalidade' in str(e))
r = reg(status='DIVERGENTE', correcao='MOVIMENTO INELEGÍVEL', penalidade='REALTA')
t('Movimento inelegível: EXCLUIR VENDA, receita 0, penalidade gravada',
  (r['acao_necessaria'], r['receita_validada'], r['tipo_penalidade']) == ('EXCLUIR VENDA', 0.0, 'REALTA'))
try:
    reg(status='DIVERGENTE', correcao='VALORES INCORRETOS')
    t('Valores incorretos exige receita', False)
except ValueError:
    t('Valores incorretos exige receita', True)
r = reg(status='DIVERGENTE', correcao='VALORES INCORRETOS', receita_correta='R$ 1.234,50')
t('Valores incorretos: CORRIGIR RECEITA, receita validada = correta (1.234,50)',
  (r['acao_necessaria'], r['receita_validada']) == ('CORRIGIR RECEITA', 1234.5))
r = reg(status='DIVERGENTE', correcao='PLANO INCORRETO', plano_correto='VIVO POS 50GB')
t('Plano incorreto sem receita: CORRIGIR PLANO, receita da venda', (r['acao_necessaria'], r['receita_validada'], r['plano_correto']) == ('CORRIGIR PLANO', 59.9, 'VIVO POS 50GB'))
r = reg(status='DIVERGENTE', correcao='PLANO INCORRETO', plano_correto='VIVO POS 50GB', receita_correta='99,90')
t('Plano incorreto com receita: receita validada = correta', r['receita_validada'] == 99.9)
try:
    reg(status='DIVERGENTE', correcao='LINHA INCORRETA', linha_correta='99999')
    t('Linha incorreta exige DDD (10+ dígitos)', False)
except ValueError:
    t('Linha incorreta exige DDD (10+ dígitos)', True)
r = reg(status='DIVERGENTE', correcao='LINHA INCORRETA', linha_correta='(27) 99999-1111')
t('Linha incorreta: ALTERAR Nº ACESSO, receita da venda, só dígitos', (r['acao_necessaria'], r['receita_validada'], r['linha_correta']) == ('ALTERAR Nº ACESSO', 59.9, '27999991111'))
r = reg(status='DIVERGENTE', correcao='DIVERGÊNCIA CADASTRAL', documento_correto='111.444.777-35')
t('Divergência cadastral: sem ação, receita da venda, altera', (r['acao_necessaria'], r['receita_validada'], r['alterar_vivogo'], r['documento_correto']) == (None, 59.9, 1, '11144477735'))
r = reg(status='DIVERGENTE', correcao='VENDA EM DUPLICIDADE')
t('Venda em duplicidade: sem ação, receita da venda', (r['acao_necessaria'], r['receita_validada']) == (None, 59.9))
try:
    reg(status='DIVERGENTE', correcao='OUTRA COISA')
    t('correção fora da lista é recusada', False)
except ValueError:
    t('correção fora da lista é recusada', True)

print('== PONTOS DE ATENÇÃO ==')
t('CPF válido passa no dígito verificador', regras.doc_valido('529.982.247-25'))
t('CPF com dígito errado é inválido', not regras.doc_valido('529.982.247-24'))
t('CNPJ válido passa', regras.doc_valido('11.222.333/0001-81'))
t('CNPJ com dígito errado é inválido', not regras.doc_valido('11.222.333/0001-80'))
t('documento suspeito: dígitos repetidos', regras.documento_suspeito('11111111111'))
t('documento suspeito: sequência 123456789', regras.documento_suspeito('12345678909'))
t('documento comum não é suspeito', not regras.documento_suspeito('52998224725'))
t('número suspeito: repetido', regras.numero_suspeito('27999999999'))
t('número suspeito: sequência', regras.numero_suspeito('27912345678'))
t('número suspeito: curto', regras.numero_suspeito('12345'))
t('número comum não é suspeito', not regras.numero_suspeito('27998761234'))

D = dt.date(2026, 10, 5)
venda('S1', '100', D)                                                       # normal
venda('S2', '101', D, cliente='JOSE PEREIRA', cpf='11144477735', acesso='27998761234')   # acesso de outro cliente
venda('S3', '102', D, cliente='CARLOS', cpf='11111111111', acesso='27999999999')          # doc e número fake
venda('S4', '103', D, cliente='ANA', cpf='52998224724', acesso='27981112222')             # DV inválido
venda('S5', '104', D, cliente='PEDRO SOUZA', cpf='39053344705', acesso='27981113333', plano='VIVO POS')
venda('S6', '105', D, cliente='PEDRO SOUZA', cpf='39053344705', acesso='27981113333', plano='VIVO POS')  # duplicidade de S5
venda('S7', '106', D, cliente='PAULO MENDES', cpf='52998224725', acesso='27981114444')    # mesmo doc com outro nome que S1
venda('S8', '107', D, servico='Troca Fidelizada')                                         # fora do escopo
venda('S9', '108', D, status='Cancelado', receita=0, acesso=None)
venda('S10', '109', dt.date(2026, 10, 6), servico='Downgrade de Plano')                    # fora do escopo
cache.clear()

r = ca.get('/auditoria-d1/api/vendas?data=2026-10-05')
t('API vendas responde', r.status_code == 200, r.content[:300])
itens = {i['id_servico']: i for i in r.json()['itens']}
t('escopo: fora Troca Fidelizada e Downgrade', 'S8' not in itens and 'S1' in itens)
alertas = {k: ' | '.join(a['txt'] for a in v['alertas']) for k, v in itens.items()}
t('número fake: acesso lançado para outro cliente', 'Número de acesso lançado para outro cliente' in alertas['S2'])
t('mesmo acesso do MESMO cliente não alerta (S1 x S7 não compartilham acesso)', 'outro cliente' not in alertas['S7'])
t('número fake: dígitos repetidos', 'Número de acesso suspeito' in alertas['S3'])
t('documento fake: dígitos repetidos', 'Documento fake: dígitos repetidos' in alertas['S3'])
t('documento fake: dígito verificador inválido', 'dígito verificador inválido' in alertas['S4'])
t('documento lançado com outro nome no mês', 'Documento lançado com outro nome no mês: PAULO MENDES' in alertas['S1'])
s6 = [a for a in itens['S6']['alertas'] if a.get('sugere') == 'VENDA EM DUPLICIDADE']
t('duplicidade: a cópia (maior nº) recebe o alerta com o botão', bool(s6) and 'do serviço S5' in s6[0]['txt'])
t('duplicidade: o 1º lançamento só recebe o aviso baixo', any(a['nivel'] == 'baixo' and 'este é o 1º' in a['txt'] for a in itens['S5']['alertas']))
t('serviço cancelado sugere Venda inexistente', any(a.get('sugere') == 'VENDA INEXISTENTE' for a in itens['S9']['alertas']))
t('sem acesso e receita zerada', 'Venda sem número de acesso' in alertas['S9'] and 'Receita zerada' in alertas['S9'])
dias = cc.get('/auditoria-d1/api/dias?mes=2026-10').json()['dias']
t('dias do mês com totais', {d['dia']: d['total'] for d in dias} == {'2026-10-05': 8})

print('== GRAVAR, REABRIR, CONTESTAR ==')
r = jpost(ca, '/auditoria-d1/api/verificacoes', {'itens': [
    {'id_servico': 'S1', 'data_venda': '2026-10-05', 'status': 'CONFORME'},
    {'id_servico': 'S9', 'data_venda': '2026-10-05', 'status': 'DIVERGENTE', 'correcao': 'VENDA INEXISTENTE'},
    {'id_servico': 'S2', 'data_venda': '2026-10-05', 'status': 'DIVERGENTE', 'correcao': 'VALORES INCORRETOS', 'receita_correta': '30,00'},
    {'id_servico': 'S3', 'data_venda': '2026-10-05', 'status': 'DIVERGENTE', 'correcao': 'LINHA INCORRETA', 'linha_correta': '27988887777'},
]})
t('salvar 4 verificações', r.status_code == 200 and r.json()['salvos'] == 4, r.content[:300])
linhas = {x['id_servico']: x for x in q('SELECT * FROM verificacao_vendas')}
t('verificado_por = nome do usuário do portal', linhas['S1']['verificado_por'] == NOME_AUDITOR)
t('S9 Venda inexistente: EXCLUIR, 0, alterar 1', (linhas['S9']['acao_necessaria'], linhas['S9']['receita_validada'], linhas['S9']['alterar_vivogo']) == ('EXCLUIR VENDA', 0, 1))
t('S2 valores: receita validada 30', linhas['S2']['receita_validada'] == 30 and linhas['S2']['receita_original'] == 59.9)
t('origem MANUAL (default da tabela)', linhas['S1']['origem'] == 'MANUAL')
log = q("SELECT * FROM verificacao_vendas_log WHERE evento = 'SALVAR'")
t('log SALVAR por serviço, com usuário do portal e JSON', len(log) == 4 and all(x['usuario'] == NOME_AUDITOR for x in log)
  and json.loads(log[0]['dados'])['status'] in ('CONFORME', 'DIVERGENTE'))
r = jpost(ca, '/auditoria-d1/api/verificacoes', {'itens': [{'id_servico': 'S4', 'data_venda': '2026-10-05', 'status': 'DIVERGENTE', 'correcao': 'MOVIMENTO INELEGÍVEL'}]})
t('validação do servidor (sem penalidade) → 400 com mensagem', r.status_code == 400 and 'penalidade' in r.json()['erro'])
r = jpost(ca, '/auditoria-d1/api/verificacoes', {'itens': [{'id_servico': 'S8', 'data_venda': '2026-10-05', 'status': 'CONFORME'}]})
t('serviço fora do escopo → 404', r.status_code == 404)
r = ca.delete('/auditoria-d1/api/verificacoes/S1')
t('reabrir apaga a verificação e grava REABRIR', r.status_code == 200 and not q("SELECT 1 FROM verificacao_vendas WHERE id_servico='S1'")
  and q("SELECT usuario FROM verificacao_vendas_log WHERE evento='REABRIR' AND id_servico='S1'")[0]['usuario'] == NOME_AUDITOR)

r = jpost(ca, '/auditoria-d1/api/contestacoes', {'id_servico': 'S1', 'decisao': 'PROCEDENTE', 'solicitante': 'Gerente', 'motivo': 'x'})
t('só venda divergente se contesta', r.status_code == 400)
r = jpost(ca, '/auditoria-d1/api/contestacoes', {'id_servico': 'S9', 'decisao': 'PROCEDENTE', 'solicitante': '', 'motivo': 'x'})
t('contestação exige quem contestou', r.status_code == 400)
r = jpost(ca, '/auditoria-d1/api/contestacoes', {'id_servico': 'S9', 'decisao': 'PROCEDENTE', 'solicitante': 'Gerente Loja', 'motivo': 'Cliente existe'})
v = q("SELECT * FROM verificacao_vendas WHERE id_servico='S9'")[0]
t('procedente: CONTESTADA, VOLTAR VENDA, receita original, origem CONTESTACAO', r.status_code == 200 and
  (v['status'], v['acao_necessaria'], v['receita_validada'], v['origem'], v['alterar_vivogo']) == ('CONTESTADA', 'VOLTAR VENDA', 0, 'CONTESTACAO', 1), r.content[:300])
t('procedente guarda a divergência e quem analisou', json.loads(v['divergencia_contestada'])['correcao_vivogo'] == 'VENDA INEXISTENTE'
  and v['contestacao_por'] == NOME_AUDITOR and v['contestacao_decisao'] == 'PROCEDENTE')
t('log CONTESTACAO', len(q("SELECT 1 FROM verificacao_vendas_log WHERE evento='CONTESTACAO' AND id_servico='S9'")) == 1)
# exportar com a contestada (antes de desfazer)
r = ca.get('/auditoria-d1/api/exportar?mes=2026-10')
t('exportar responde CSV', r.status_code == 200 and r['Content-Type'].startswith('text/csv'), r.content[:200])
texto = r.content.decode('cp1252')
modelo = (pathlib.Path(importacao_vivogo.MODELO).read_bytes().decode('cp1252').splitlines()[0])
t('cabeçalho do CSV = modelo_importacao_ged.csv', texto.splitlines()[0] == modelo)
rows = list(csv.reader(io.StringIO(texto), delimiter=';'))
cab = rows[0]
por_venda = {x[cab.index('ID Venda')]: dict(zip(cab, x)) for x in rows[1:]}
t('contestada procedente sai como Procedente sem motivo', por_venda.get('108', {}).get('Status BKO(*)(**)') == 'Procedente'
  and por_venda['108']['Motivo Reprovação(**)'] == '')
t('valores incorretos: Valor Plano ajustado para 30,00', por_venda.get('101', {}).get('Valor Plano(**)') == '30,00')
t('Serviço(*) sai como o Serviço_Técnico da venda', por_venda['101']['Serviço(*)'] == 'Alta Controle')
resumo = json.loads(unquote(r['X-Resumo']))
t('linha incorreta fica fora do arquivo e vai para os manuais', '102' not in por_venda and any(m['id_venda'] == '102' for m in resumo['manuais']))
t('resumo marca a contestada', any(x.get('contestada') for x in resumo['incluidas']))

r = ca.delete('/auditoria-d1/api/contestacoes/S9')
v = q("SELECT * FROM verificacao_vendas WHERE id_servico='S9'")[0]
t('desfazer procedente: volta DIVERGENTE / Venda inexistente / receita 0',
  r.status_code == 200 and (v['status'], v['correcao_vivogo'], v['receita_validada'], v['contestacao_decisao']) == ('DIVERGENTE', 'VENDA INEXISTENTE', 0, None))
t('log DESF_CONTEST', len(q("SELECT 1 FROM verificacao_vendas_log WHERE evento='DESF_CONTEST'")) == 1)
r = ca.get('/auditoria-d1/api/exportar?mes=2026-10')
rows = list(csv.reader(io.StringIO(r.content.decode('cp1252')), delimiter=';'))
por_venda = {x[rows[0].index('ID Venda')]: dict(zip(rows[0], x)) for x in rows[1:]}
t('venda inexistente sai Improcedente + motivo', por_venda['108']['Status BKO(*)(**)'] == 'Improcedente'
  and por_venda['108']['Motivo Reprovação(**)'] == 'Lançamento incorreto no Vivo GO')
r = jpost(ca, '/auditoria-d1/api/contestacoes', {'id_servico': 'S2', 'decisao': 'IMPROCEDENTE', 'solicitante': 'Vendedor', 'motivo': 'alegou'})
v = q("SELECT * FROM verificacao_vendas WHERE id_servico='S2'")[0]
t('improcedente: continua DIVERGENTE com a contestação registrada', (v['status'], v['contestacao_decisao'], v['receita_validada']) == ('DIVERGENTE', 'IMPROCEDENTE', 30))
r = ca.delete('/auditoria-d1/api/contestacoes/S2')
v = q("SELECT * FROM verificacao_vendas WHERE id_servico='S2'")[0]
t('apagar registro improcedente: limpa só a contestação', v['status'] == 'DIVERGENTE' and v['contestacao_decisao'] is None)
h = ca.get('/auditoria-d1/api/historico/S9').json()
t('histórico do serviço em ordem decrescente', [x['evento'] for x in h][:3] == ['DESF_CONTEST', 'CONTESTACAO', 'SALVAR'])

print('== PLANILHA .XLSX ==')
r = cc.get('/auditoria-d1/api/exportar-planilha?mes=2026-10')
t('planilha baixa para CONSULTA', r.status_code == 200 and r['Content-Type'].startswith('application/vnd.openxml'))
wb = openpyxl.load_workbook(io.BytesIO(r.content))
ws = wb['base']
cab = [c.value for c in ws[1]]
t('cabeçalho da aba base', cab == regras.CABECALHO_PLANILHA)
lin = {str(row[cab.index('Nº da Venda')]): row for row in ws.iter_rows(min_row=2, values_only=True)}
t('VENDA DUPLICADA: cópia DUPLICADO, original UNICO', lin['105'][cab.index('VENDA DUPLICADA')] == 'DUPLICADO'
  and lin['104'][cab.index('VENDA DUPLICADA')] == 'UNICO')
t('Status e receita validada', lin['101'][cab.index('Status')] == 'Divergente' and lin['101'][cab.index('RECEITA VALIDADA')] == 30)

print('== CRUZAMENTO SVA ==')
DS = dt.date(2026, 10, 10)
venda('SV1', '200', DS, servico='SVA Móvel', grupo='SVA', pilar='SVA', plano='NETFLIX PADRAO', acesso=None,
      cliente='LUCAS LIMA', cpf='39053344705', receita=20, valor=20)
venda('SV1b', '200', DS, servico='Alta Controle', grupo='Alta', pilar='Móvel', plano='VIVO CONTROLE', acesso='27981230001',
      cliente='LUCAS LIMA', cpf='39053344705')
venda('SV2', '201', DS, servico='SVA Móvel', grupo='SVA', pilar='SVA', plano='SPOTIFY PREMIUM', acesso='27981230002',
      cliente='BIA', cpf='11144477735', receita=15, valor=15)
cache.clear()
# verificação manual em SV2 antes (a automação prevalece e guarda)
jpost(ca, '/auditoria-d1/api/verificacoes', {'itens': [{'id_servico': 'SV2', 'data_venda': '2026-10-10', 'status': 'CONFORME'}]})


def base_sva(linhas):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['AnoMes', 'DT_MVMT', 'FLAG_VNCL', 'Documento', 'NU_TLFN', 'NOME', 'DS_SRVC', 'NO_SRVC', 'DS_CLSF_SVA', 'Nome Loja'])
    for x in linhas:
        ws.append(x)
    ws.append(['Filtros aplicados: ...'])
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


arq = base_sva([
    ['202610', dt.datetime(2026, 10, 10), '0', '39053344705', '27981230001', 'JOAO VENDEDOR', 'NETFLIX PADRAO', 'NETFLIX', 'SVA AVULSO', 'LARANJEIRAS'],
    ['202610', dt.datetime(2026, 10, 11), '0', '11144477735', '27981230002', 'JOAO VENDEDOR', 'SPOTIFY', 'SPOTIFY', 'SVA AVULSO', 'LARANJEIRAS'],
    ['202610', dt.datetime(2026, 10, 9), '1', '00000000191', '27900000000', 'X', 'CONTROLE 10GB', 'NETFLIX', 'PLANO CONTROLE', 'LARANJEIRAS'],
])
r = ca.post('/auditoria-d1/api/sva/previa', {'base': SimpleUploadedFile('base.xlsx', arq)})
t('AUDITOR não importa base SVA (403)', r.status_code == 403)
r = cg.post('/auditoria-d1/api/sva/previa', {'base': SimpleUploadedFile('base.xlsx', arq)})
j = r.json()
t('prévia SVA: 2 a zerar (Exata), embutido só informação', r.status_code == 200 and j['aplicar'] == 2 and j['plano'] == 1 and j['anomes'] == '202610', j)
t('prévia avisa a verificação manual que será substituída', j['manuais_sobrescritas'] == 1)
t('prévia não grava nada', not q("SELECT 1 FROM cruzamento_importacoes"))
r = cg.post('/auditoria-d1/api/sva/previa', {'base': SimpleUploadedFile('ruim.xlsx', b'nao e xlsx')})
t('arquivo ruim → 400 com mensagem', r.status_code == 400 and 'xlsx' in r.json()['erro'])
r = jpost(cg, '/auditoria-d1/api/sva/aplicar', {'token': j['token']})
t('aplicar SVA', r.status_code == 200 and r.json()['aplicadas'] == 2, r.content[:300])
sv = {x['id_servico']: x for x in q("SELECT * FROM verificacao_vendas WHERE origem='CRUZAMENTO_SVA'")}
t('serviço SVA marcado (não o plano móvel da mesma venda)', set(sv) == {'SV1', 'SV2'})
t('marcação: Movimento inelegível · SVA NÃO ATIVADO, receita 0, Automação SVA',
  (sv['SV1']['correcao_vivogo'], sv['SV1']['tipo_penalidade'], sv['SV1']['receita_validada'], sv['SV1']['verificado_por'], sv['SV1']['cruzamento_anomes'])
  == ('MOVIMENTO INELEGÍVEL', 'SVA NÃO ATIVADO', 0, 'Automação SVA', '202610'))
t('verificação manual guardada em verificacao_anterior', json.loads(sv['SV2']['verificacao_anterior'])['status'] == 'CONFORME')
imp = q("SELECT * FROM cruzamento_importacoes")[0]
t('cruzamento_importacoes com usuário do portal e resultado', imp['usuario'] == 'Gestor Teste' and imp['aplicadas'] == 2
  and imp['meses'] == '202610' and len(json.loads(imp['resultado'])) >= 3)
t('log CRUZAMENTO_SVA', len(q("SELECT 1 FROM verificacao_vendas_log WHERE evento='CRUZAMENTO_SVA'")) == 2)
r = jpost(cg, '/auditoria-d1/api/sva/aplicar', {'token': j['token']})
t('token usado não aplica de novo', r.status_code == 404)
r = cg.get(f"/auditoria-d1/api/sva/importacoes/{imp['id']}/planilha")
t('planilha do resultado com as abas', r.status_code == 200 and openpyxl.load_workbook(io.BytesIO(r.content)).sheetnames[0] == 'Zeradas automaticamente')
# base nova sem o SPOTIFY: desfaz a marcação e volta a verificação manual
arq2 = base_sva([['202610', dt.datetime(2026, 10, 10), '0', '39053344705', '27981230001', 'JOAO VENDEDOR', 'NETFLIX PADRAO', 'NETFLIX', 'SVA AVULSO', 'LARANJEIRAS']])
j2 = cg.post('/auditoria-d1/api/sva/previa', {'base': SimpleUploadedFile('base2.xlsx', arq2)}).json()
t('prévia da base nova: 1 marcação seria desfeita', j2['revertidas'] == 1 and j2['ultima']['anomes'] == '202610')
jpost(cg, '/auditoria-d1/api/sva/aplicar', {'token': j2['token']})
v = q("SELECT * FROM verificacao_vendas WHERE id_servico='SV2'")[0]
t('base nova substitui: SV2 volta à verificação manual (CONFORME)', (v['status'], v['origem']) == ('CONFORME', 'MANUAL'))
t('log REVERTER', len(q("SELECT 1 FROM verificacao_vendas_log WHERE evento='REVERTER'")) == 1)
r = ca.get('/auditoria-d1/api/vendas?data=2026-10-10')
it = {i['id_servico']: i for i in r.json()['itens']}
t('esteira mostra a marcação automática', it['SV1']['verificacao']['origem'] == 'CRUZAMENTO_SVA')

print('== CRUZAMENTO FIXA ==')
DF = dt.date(2026, 10, 15)
venda('F1', '300', DF, servico='Alta Banda Larga', grupo='Alta', pilar='Fixa', plano='FIBRA 500', protocolo="'7001234567",
      cliente='RITA', cpf='52998224725', receita=100, valor=100)
venda('F1tv', '300', DF, servico='Alta TV', grupo='Alta', pilar='Fixa', plano='TV', protocolo="'7001234567",
      cliente='RITA', cpf='52998224725', receita=50, valor=50)
venda('F2', '301', DF, servico='Alta Banda Larga', grupo='Alta', pilar='Fixa', plano='FIBRA 300', protocolo='7009990000',
      cliente='TONI', cpf='11144477735', receita=80, valor=80)
venda('F3', '302', DF, servico='Alta Banda Larga', grupo='Alta', pilar='Fixa', plano='FIBRA 300', protocolo='7005550000',
      cliente='LIA', cpf='39053344705', receita=80, valor=80)
cache.clear()


def base_fixa(linhas):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['ANOMES', 'DATA_CRIAÇÃO', 'ETAPA INSTALACAO', 'STATUS INSTALACAO', 'MOTIVO', 'ORDEM', 'CPF', 'PRODUTO',
               'ADABAS', 'NOME VENDEDOR', 'VENDA COMISSIONADA', 'SERVICO'])
    for x in linhas:
        ws.append(x)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


arqf = base_fixa([
    ['202610', dt.datetime(2026, 10, 15), 'CANCELADA', 'CANCELADA', 'DESISTENCIA', 7001234567, '529.982.247-25', 'FIBRA', 'ESLA013-001', 'JOAO VENDEDOR', 'VENDA COMISSIONADA', 'FTTH'],
    ['202610', dt.datetime(2026, 10, 15), 'INSTALADA', 'INSTALADA', '', 7009990000, '111.444.777-35', 'FIBRA', 'ESLA013-001', 'JOAO VENDEDOR', 'VENDA COMISSIONADA', 'FTTH'],
])
r = cg.post('/auditoria-d1/api/fixa/previa', {'base': SimpleUploadedFile('analitico.xlsx', arqf)})
jf = r.json()
t('prévia Fixa', r.status_code == 200 and jf['meses'] == ['202610'], r.content[:400])
t('prévia: 1 pedido cancelado com vínculo Alta → zera só a banda larga', jf.get('aplicar_pedidos') == 1 and jf.get('aplicar_servicos') == 1)
t('prévia: pedido sem ORDEM na Vivo vira sugestão (venda inexistente?)', any(s['id_venda'] == '302' for s in jf.get('sugestoes_lista', [])))
t('prévia: instalada fica OK', jf.get('ok') == 1)
r = jpost(cg, '/auditoria-d1/api/fixa/aplicar', {'token': jf['token']})
t('aplicar Fixa', r.status_code == 200 and r.json()['aplicadas'] == 1, r.content[:300])
fx = {x['id_servico']: x for x in q("SELECT * FROM verificacao_vendas WHERE origem='CRUZAMENTO_FIXA'")}
t('F1 (FTTH) zerado com FIBRA CANCELADA; TV do combo intacta', set(fx) == {'F1'} and fx['F1']['tipo_penalidade'] == 'FIBRA CANCELADA'
  and fx['F1']['confianca'] == 'Fixa Alta' and fx['F1']['verificado_por'] == 'Automação Fixa')
sug = q("SELECT * FROM cruzamento_sugestoes WHERE origem='CRUZAMENTO_FIXA'")
t('sugestão gravada para o pedido não localizado', any(s['id_venda'] == '302' and s['correcao'] == 'VENDA INEXISTENTE' for s in sug))
it = {i['id_servico']: i for i in ca.get('/auditoria-d1/api/vendas?data=2026-10-15').json()['itens']}
t('sugestão vira alerta na esteira com o botão', any(a.get('sugere') == 'VENDA INEXISTENTE' and 'Cruzamento Fixa' in a['txt'] for a in it['F3']['alertas']))
# contestação procedente: a automação não remarca
jpost(ca, '/auditoria-d1/api/contestacoes', {'id_servico': 'F1', 'decisao': 'PROCEDENTE', 'solicitante': 'Gerente', 'motivo': 'instalou'})
jf2 = cg.post('/auditoria-d1/api/fixa/previa', {'base': SimpleUploadedFile('analitico.xlsx', arqf)}).json()
t('prévia avisa contestada mantida', jf2['contestadas_mantidas'] == 1)
jpost(cg, '/auditoria-d1/api/fixa/aplicar', {'token': jf2['token']})
t('venda contestada não é remarcada pela automação', q("SELECT status FROM verificacao_vendas WHERE id_servico='F1'")[0]['status'] == 'CONTESTADA')
t('lista de importações Fixa', cg.get('/auditoria-d1/api/fixa/importacoes').json()[0]['meses'] == '202610')

print('== WHATSAPP DAS LOJAS ==')
GrupoWhatsappLoja.objects.create(setor=loja, grupo='120363000000000001')
imp_sva = q("SELECT id FROM cruzamento_importacoes WHERE origem='CRUZAMENTO_SVA' ORDER BY id DESC")[0]['id']


class ThreadNaHora:
    def __init__(self, target, args=(), **k):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


whatsapp_lojas.PAUSA_LOJAS = 0
whatsapp_lojas.PAUSA_MSGS = 0
with mock.patch('core.evolution.enviar_texto', return_value=(True, 'ok')) as envio, \
        mock.patch.object(whatsapp_lojas.threading, 'Thread', ThreadNaHora):
    t('configuração nasce com o WhatsApp DESLIGADO', ConfiguracaoAuditoriaD1.get().whatsapp_ativo is False)
    lj = cg.get(f'/auditoria-d1/api/sva/lojas?importacao_id={imp_sva}').json()
    t('lojas a sinalizar casadas com o grupo da loja', lj['lojas'] == ['LARANJEIRAS'] and lj['ligado'] is False)
    r = jpost(cg, '/auditoria-d1/api/sva/whatsapp', {'importacao_id': imp_sva, 'teste': True})
    t('desligado: teste recusado', r.status_code == 400 and 'desligado' in r.json()['erro'])
    r = jpost(cg, '/auditoria-d1/api/sva/whatsapp', {'importacao_id': imp_sva, 'teste': False})
    t('desligado: disparo recusado', r.status_code == 400)
    t('desligado: enviar_texto NUNCA chamado', envio.call_count == 0 and not DisparoWhatsappD1.objects.exists())
    r = cg.post('/auditoria-d1/configuracao/', {'whatsapp_ativo': '1', 'grupo_teste': '120363999'})
    t('GESTOR não liga o WhatsApp', r.status_code == 302 and ConfiguracaoAuditoriaD1.get().whatsapp_ativo is False)
    r = cs.post('/auditoria-d1/configuracao/', {'whatsapp_ativo': '1', 'grupo_teste': ''})
    t('SUPERADMIN liga', ConfiguracaoAuditoriaD1.get().whatsapp_ativo is True)
    r = jpost(cg, '/auditoria-d1/api/sva/whatsapp', {'importacao_id': imp_sva, 'teste': True})
    t('teste sem grupo de teste configurado é recusado', r.status_code == 400 and 'grupo de teste' in r.json()['erro'] and envio.call_count == 0)
    cs.post('/auditoria-d1/configuracao/', {'whatsapp_ativo': '1', 'grupo_teste': '120363999'})
    r = jpost(cg, '/auditoria-d1/api/sva/whatsapp', {'importacao_id': imp_sva, 'teste': True})
    t('teste ligado: responde estado', r.status_code == 200, r.content[:300])
    t('teste vai só para o grupo de teste, com o aviso', envio.call_count == 1 and envio.call_args[0][0] == '120363999@g.us'
      and envio.call_args[0][1].startswith('[TESTE — iria para 120363000000000001@g.us]'))
    msg = envio.call_args[0][1]
    t('mesma mensagem do sistema antigo', '*LARANJEIRAS · SVA não ativado*' in msg and 'Outubro/2026' in msg and '*Nº:* 200' in msg)
    d = DisparoWhatsappD1.objects.get()
    t('disparo registrado (teste, enviado)', d.teste and d.enviado is True and d.loja == 'LARANJEIRAS')
    est = cg.get('/auditoria-d1/api/sva/whatsapp').json()
    t('andamento: concluído 1 de 1', est['rodando'] is False and est['enviadas'] == 1 and est['total'] == 1)
    cs.post('/auditoria-d1/configuracao/', {'grupo_teste': '120363999'})
    t('desligar de novo', ConfiguracaoAuditoriaD1.get().whatsapp_ativo is False)
    jpost(cg, '/auditoria-d1/api/sva/whatsapp', {'importacao_id': imp_sva, 'teste': False})
    t('desligado de novo: nada mais sai', envio.call_count == 1)

print('== PERMISSÕES ==')
PAGINAS = ['/auditoria-d1/', '/auditoria-d1/acessos/', '/auditoria-d1/configuracao/']
APIS_GET = ['/auditoria-d1/api/meta', '/auditoria-d1/api/dias?mes=2026-10', '/auditoria-d1/api/vendas?data=2026-10-05',
            '/auditoria-d1/api/historico/S1', '/auditoria-d1/api/exportar-planilha?mes=2026-10', '/auditoria-d1/api/exportar?mes=2026-10',
            '/auditoria-d1/api/sva/importacoes', f'/auditoria-d1/api/sva/importacoes/{imp_sva}/planilha',
            f'/auditoria-d1/api/sva/lojas?importacao_id={imp_sva}', '/auditoria-d1/api/sva/whatsapp',
            '/auditoria-d1/api/fixa/importacoes', f'/auditoria-d1/api/fixa/importacoes/{imp_sva}/planilha']
APIS_POST = ['/auditoria-d1/api/verificacoes', '/auditoria-d1/api/contestacoes', '/auditoria-d1/api/sva/previa',
             '/auditoria-d1/api/sva/aplicar', '/auditoria-d1/api/sva/whatsapp', '/auditoria-d1/api/sva/whatsapp/cancelar',
             '/auditoria-d1/api/fixa/previa', '/auditoria-d1/api/fixa/aplicar']
APIS_DELETE = ['/auditoria-d1/api/verificacoes/S1', '/auditoria-d1/api/contestacoes/S1']
antes = q('SELECT COUNT(*) n FROM verificacao_vendas_log')[0]['n']
t('sem liberação (até ADMIN): páginas redirecionam', all(cn.get(u).status_code == 302 for u in PAGINAS))
t('sem liberação: todas as APIs GET → 403', all(cn.get(u).status_code == 403 for u in APIS_GET), [u for u in APIS_GET if cn.get(u).status_code != 403])
t('sem liberação: todas as APIs POST → 403', all(jpost(cn, u, {}).status_code == 403 for u in APIS_POST))
t('sem liberação: DELETE → 403', all(cn.delete(u).status_code == 403 for u in APIS_DELETE))
t('sem login: API → 401 e página → login', Client().get('/auditoria-d1/api/meta').status_code == 401
  and Client().get('/auditoria-d1/').status_code == 302)
t('nada gravado pelas tentativas barradas', q('SELECT COUNT(*) n FROM verificacao_vendas_log')[0]['n'] == antes)
t('pode_ver: SUPERADMIN, consulta, auditor e gestor sim; sem liberação não',
  all(pode_ver(u) for u in (superadm, consulta, auditor, gestor)) and not pode_ver(semacesso))
pc = cc.get('/auditoria-d1/').content.decode()
t('CONSULTA vê a esteira, sem Exportar/Base SVA/Fixa/contestação', cc.get('/auditoria-d1/').status_code == 200
  and 'id="btnExportar"' not in pc and 'id="btnSva"' not in pc and 'id="modalContest"' not in pc and 'id="btnPlanilha"' in pc)
t('CONSULTA lê a API', cc.get('/auditoria-d1/api/vendas?data=2026-10-05').status_code == 200)
t('CONSULTA não grava (403)', jpost(cc, '/auditoria-d1/api/verificacoes', {'itens': [{'id_servico': 'S5', 'data_venda': '2026-10-05', 'status': 'CONFORME'}]}).status_code == 403)
t('CONSULTA não reabre nem contesta', cc.delete('/auditoria-d1/api/verificacoes/S2').status_code == 403
  and jpost(cc, '/auditoria-d1/api/contestacoes', {}).status_code == 403)
t('CONSULTA não exporta o CSV do VivoGo', cc.get('/auditoria-d1/api/exportar?mes=2026-10').status_code == 403)
pa = ca.get('/auditoria-d1/').content.decode()
t('AUDITOR vê Exportar e contestação, não as bases', 'id="btnExportar"' in pa and 'id="modalContest"' in pa and 'id="btnSva"' not in pa)
t('AUDITOR não usa cruzamentos nem WhatsApp', all(ca.get(u).status_code == 403 for u in ('/auditoria-d1/api/sva/importacoes', '/auditoria-d1/api/sva/whatsapp'))
  and jpost(ca, '/auditoria-d1/api/fixa/aplicar', {}).status_code == 403)
pg = cg.get('/auditoria-d1/').content.decode()
t('GESTOR vê Base SVA/Fixa', 'id="btnSva"' in pg and 'id="btnFixa"' in pg)
for c, quem in ((cc, 'CONSULTA'), (ca, 'AUDITOR'), (cg, 'GESTOR')):
    t(f'{quem}: /acessos/ e /configuracao/ barrados (302), sem as abas', c.get('/auditoria-d1/acessos/').status_code == 302
      and c.get('/auditoria-d1/configuracao/').status_code == 302 and 'auditoria-d1/acessos/' not in c.get('/auditoria-d1/').content.decode())
n_acessos = AcessoAuditoriaD1.objects.count()
r = cg.post('/auditoria-d1/acessos/', {'acao': 'adicionar', 'user': semacesso.pk, 'papel': 'GESTOR'})
t('GESTOR não libera ninguém pelo POST de /acessos/', r.status_code == 302 and AcessoAuditoriaD1.objects.count() == n_acessos)
r = cg.post('/auditoria-d1/acessos/', {'acao': 'papel', 'acesso': AcessoAuditoriaD1.objects.get(user=consulta).pk, 'papel': 'GESTOR'})
t('GESTOR não muda papel', AcessoAuditoriaD1.objects.get(user=consulta).papel == 'CONSULTA')
t('SUPERADMIN vê acessos e configuração', cs.get('/auditoria-d1/acessos/').status_code == 200 and cs.get('/auditoria-d1/configuracao/').status_code == 200)
r = cs.post('/auditoria-d1/acessos/', {'acao': 'adicionar', 'user': semacesso.pk, 'papel': 'AUDITOR'})
t('SUPERADMIN libera pessoa', AcessoAuditoriaD1.objects.get(user=semacesso).papel == 'AUDITOR')
cn2 = Client()
cn2.force_login(User.objects.get(pk=semacesso.pk))
t('liberado agora entra', cn2.get('/auditoria-d1/').status_code == 200)
cs.post('/auditoria-d1/acessos/', {'acao': 'remover', 'acesso': AcessoAuditoriaD1.objects.get(user=semacesso).pk})
cn3 = Client()
cn3.force_login(User.objects.get(pk=semacesso.pk))
t('removido volta a ser barrado', cn3.get('/auditoria-d1/').status_code == 302 and cn3.get('/auditoria-d1/api/meta').status_code == 403)
meta = cs.get('/auditoria-d1/api/meta').json()
t('meta: categorias, regras e o usuário do portal', meta['correcoes'][0] == 'VENDA INEXISTENTE' and meta['usuario'] == 'Super Teste'
  and meta['regras']['VALORES INCORRETOS']['campos'] == ['receita_correta'])

print('== MYSQL FORA DO AR ==')
salvo = d1mysql.conectar


def fora(*a, **k):
    raise d1mysql.D1Indisponivel('caiu')


d1mysql.conectar = fora
cache.clear()
r = ca.get('/auditoria-d1/api/vendas?data=2026-10-05')
t('banco fora: 503 com mensagem, sem 500', r.status_code == 503 and 'banco' in r.json()['erro'])
t('banco fora: a página da esteira ainda abre', ca.get('/auditoria-d1/').status_code == 200)
d1mysql.conectar = salvo
t('nenhum DDL rodou no "MySQL"', not any(re.search(r'\b(CREATE|ALTER|DROP)\b', s, re.I) for s in SQL_EXECUTADO))

print('== TEMPLATES ==')
pasta = pathlib.Path(__file__).resolve().parent / 'templates' / 'auditoria_d1'
multilinha = []
for arq_t in sorted(pasta.glob('*.html')):
    texto = arq_t.read_text()
    for m in re.finditer(r'\{#(.*?)#\}', texto, re.S):
        if '\n' in m.group(1):
            multilinha.append(arq_t.name)
    if texto.count('{#') != texto.count('#}'):
        multilinha.append(arq_t.name + ' (desparelhado)')
t('nenhum {# #} em várias linhas', not multilinha, multilinha)
paginas = [cs.get(u).content.decode() for u in PAGINAS]
t('nenhum comentário vazando na tela', all('{#' not in p and '#}' not in p and '{% comment' not in p for p in paginas))

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
