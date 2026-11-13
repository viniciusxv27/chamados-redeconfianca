"""Contas a Pagar (/contas-a-pagar/): o sistema standalone portado para o portal (11/11/2026).

Cobre: as 3 tratativas (Agging, Banco pagador, TipoMacro), saldo final, importar a BASE
VIVOGO (.csv e .xlsx), montar e publicar (substitui o dia), excluir dia, log de cada
publicação/exclusão, painel por período (Hoje/Semana/Quinzena/Mês e setas) e filtros por
banco/categoria/filial, configurações, permissões por papel (sem acesso é barrado nas telas
e nas APIs; só o SUPERADMIN mexe nos acessos), MySQL fora do ar, o command
importar_contas_pagar_local, a semente de categorias e os comentários de template.

Banco descartável (SQLite). O MySQL é um dublê em memória: o pymysql.connect real é
trocado por uma função que falha, então nada chega ao MySQL de verdade. Nada sai.
"""
import os
import pathlib
import re
import sys
import tempfile

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-contas-pagar-'))
(PASTA / 'zz_contas_pagar_settings.py').write_text(f"""
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
VIVOGO_MYSQL_URL = ''
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']
CACHES = {{
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-cp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_contas_pagar_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
os.environ['VIVOGO_MYSQL_URL'] = ''
os.environ['CONTAS_PAGAR_MYSQL_URL'] = ''

# Nenhum teste pode falar com o MySQL real: o connect do pymysql vira uma bomba.
import pymysql  # noqa: E402


def _proibido(*a, **k):
    raise AssertionError('teste tentou conectar no MySQL real')


pymysql.connect = _proibido
pymysql.Connect = _proibido

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import io  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from decimal import Decimal as D  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.utils import timezone  # noqa: E402

from contas_pagar import mysql as cp_mysql  # noqa: E402
from contas_pagar import regras, servicos  # noqa: E402
from contas_pagar.models import (AcessoContasPagar, Categoria, ConfiguracaoContasPagar, ContaBancaria,  # noqa: E402
                                 LogContasPagar, Rascunho)
from contas_pagar.permissoes import pode_ver  # noqa: E402

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


# ── Dublê do MySQL: SQLite em memória com as mesmas tabelas ────────────────
class Duble:
    def __init__(self):
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE cp_titulos_programados (id INTEGER PRIMARY KEY AUTOINCREMENT, data_pagamento TEXT NOT NULL,
              vencimento TEXT, fornecedor TEXT, cnpj_cpf_fornecedor TEXT, filial TEXT, observacoes TEXT, plano_contas TEXT,
              despesa TEXT, agging TEXT, tipo_macro TEXT, forma_pagamento TEXT, num_movimento TEXT, num_documento TEXT,
              nota_fiscal TEXT, debito TEXT, banco_pagador TEXT, valor_liquido NUMERIC NOT NULL DEFAULT 0,
              publicado_em TEXT NOT NULL);
            CREATE TABLE cp_saldos_bancos (data_pagamento TEXT NOT NULL, banco TEXT NOT NULL, ordem INT DEFAULT 0,
              aplicacao NUMERIC DEFAULT 0, conta_garantida NUMERIC DEFAULT 0, saldo NUMERIC DEFAULT 0,
              total_disponivel NUMERIC DEFAULT 0, pagamentos NUMERIC DEFAULT 0, transferencia NUMERIC DEFAULT 0,
              saldo_final NUMERIC DEFAULT 0, publicado_em TEXT NOT NULL, PRIMARY KEY (data_pagamento, banco));
        """)
        self.fora = False
        self.conexoes = 0

    def conexao(self):
        if self.fora:
            raise cp_mysql.ContasPagarIndisponivel('Não deu para conectar no MySQL do contas a pagar: dublê fora do ar')
        self.conexoes += 1
        return _Con(self.db)

    def q(self, sql, p=()):
        return [dict(r) for r in self.db.execute(sql, p)]


def _p(v):
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, D):
        return float(v)
    if hasattr(v, 'isoformat'):
        return v.isoformat(sep=' ')
    return v


class _Cur:
    def __init__(self, db):
        self.db, self.res = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, p=()):
        cur = self.db.execute(sql.replace('%s', '?'), tuple(_p(x) for x in p))
        self.res = [dict(r) for r in cur.fetchall()] if cur.description else []

    def executemany(self, sql, linhas):
        self.db.executemany(sql.replace('%s', '?'), [tuple(_p(x) for x in l) for l in linhas])

    def fetchall(self):
        return self.res

    def fetchone(self):
        return self.res[0] if self.res else None


class _Con:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _Cur(self.db)

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def close(self):
        pass


duble = Duble()
cp_mysql.conexao = duble.conexao

# ── Pessoas ─────────────────────────────────────────────────────────────────
admin = User.objects.create_user(username='zz.cp.adm', email='zz.cp.adm@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Ana', last_name='Super', hierarchy='SUPERADMIN')
cons = User.objects.create_user(username='zz.cp.cons', email='zz.cp.cons@exemplo-teste.local', password='S3nha!teste',
                                first_name='Caio', last_name='Consulta', hierarchy='PADRAO')
oper = User.objects.create_user(username='zz.cp.oper', email='zz.cp.oper@exemplo-teste.local', password='S3nha!teste',
                                first_name='Olga', last_name='Operadora', hierarchy='PADRAO')
oper2 = User.objects.create_user(username='zz.cp.oper2', email='zz.cp.oper2@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Otto', last_name='Outro', hierarchy='PADRAO')
conf = User.objects.create_user(username='zz.cp.conf', email='zz.cp.conf@exemplo-teste.local', password='S3nha!teste',
                                first_name='Cora', last_name='Config', hierarchy='ADMIN')
nada = User.objects.create_user(username='zz.cp.nada', email='zz.cp.nada@exemplo-teste.local', password='S3nha!teste',
                                first_name='Nil', last_name='Nada', hierarchy='ADMIN')
AcessoContasPagar.objects.create(user=cons, papel='CONSULTA')
AcessoContasPagar.objects.create(user=oper, papel='OPERADOR')
AcessoContasPagar.objects.create(user=oper2, papel='OPERADOR')
AcessoContasPagar.objects.create(user=conf, papel='CONFIGURADOR')
ca, cc, co, co2, cf, cn, anon = Client(), Client(), Client(), Client(), Client(), Client(), Client()
for cli, u in ((ca, admin), (cc, cons), (co, oper), (co2, oper2), (cf, conf), (cn, nada)):
    cli.force_login(u)

# ── Configuração (contas fictícias) ─────────────────────────────────────────
Categoria.objects.all().delete()
ContaBancaria.objects.all().delete()
# As contas vêm do Financeiro › Bancos (13/11/2026): cadastra lá, o Contas a Pagar espelha.
from financeiro.models import ContaBancaria as ContaFin  # noqa: E402
ContaFin.objects.all().delete()


def conta_fin(nome, agencia, conta, ordem, usar=True, **extra):
    f = ContaFin.objects.create(nome=nome, agencia=agencia, conta=conta, **extra)
    servicos.sincronizar_contas()
    c = ContaBancaria.objects.get(conta_financeiro=f)
    ContaBancaria.objects.filter(pk=c.pk).update(ordem=ordem, ativo=usar)
    c.refresh_from_db()
    return c


B_SICOOB = conta_fin('ZZ SICOOB - REDE', '301-0', '203409', 0, digito='3')
B_SICOOB_APL = conta_fin('ZZ SICOOB - APLICAÇÃO', '3010', '203409', 1)
B_INTER = conta_fin('ZZ INTER', '0001', '8499970', 2)
B_SEMAG = conta_fin('ZZ CORA SEM AGÊNCIA', '', '777123', 3)
B_INATIVO = conta_fin('ZZ INATIVO', '9', '555', 4, usar=False)
for tipo, cat in (('Compra', 'Fornecedor'), ('AGGING', 'AGGING'), ('ALUGUEL OPERACIONAL (LOJAS)', 'Aluguéis'),
                  ('MATERIAL DE USO E CONSUMO', 'Não classificado'), ('energia eletrica', 'Energia')):
    Categoria.objects.create(tipo=tipo, categoria=cat)
ConfiguracaoContasPagar.atual()

HOJE = timezone.localdate()
ONTEM, AMANHA = HOJE - timedelta(days=1), HOJE + timedelta(days=1)
br = lambda d: d.strftime('%d/%m/%Y')  # noqa: E731

CAB = ('Situação;Data de lançamento;Data de vencimento;Data de pagamento;Forma pagamento;Filial;Plano de Contas;Despesa;'
       'Obs. despesa;Fornecedor;CNPJ/CPF Fornecedor;Nº do movimento;Nº do documento / Linha de pagamento;Nota fiscal;'
       'Observações;Débito;Valor total;Valor líquido')
LINHAS = [
    # situação, vencimento, filial, despesa, fornecedor, débito, valor
    ('A Pagar', HOJE, 'LOJA ZZ A', 'Compra de mercadoria', 'TELEFONICA BRASIL SA', 'Conta ( AG: 0301 CC: 203409-3)', '1.234,56'),
    ('A Pagar', HOJE, 'LOJA ZZ B', 'Compra de mercadoria', 'OUTRO FORNECEDOR', 'Conta ( AG: 3010 CC: 203.409-3)', '100,00'),
    ('A Pagar', HOJE, 'LOJA ZZ A', 'ALUGUEL OPERACIONAL (LOJAS)', 'IMOBILIARIA ZZ', 'Conta ( AG: 1 CC: 8499970-5)', '2500'),
    ('A Pagar', HOJE, 'SEDE ZZ', 'MATERIAL DE USO E CONSUMO', 'PAPELARIA ZZ', 'Conta ( AG: 77 CC: 777123-0)', '50,5'),
    ('A Pagar', HOJE, 'SEDE ZZ', 'Energia Eletrica', 'ENERGIA ZZ', 'Caixa', '300,00'),
    ('A Pagar', HOJE, 'SEDE ZZ', 'DESPESA NOVA ZZ', 'FORN ZZ', 'Conta ( AG: 9 CC: 555-1)', '10,00'),
    ('A Pagar', ONTEM, 'LOJA ZZ B', 'ALUGUEL OPERACIONAL (LOJAS)', 'IMOB ATRASADA', 'Conta ( AG: 301 CC: 203409-3)', '400,00'),
    ('A Pagar', AMANHA, 'LOJA ZZ A', 'Compra', 'TELEFONICA BRASIL SA', 'Conta ( AG: 301 CC: 203409-3)', '999,99'),
    ('Pago', HOJE, 'LOJA ZZ A', 'Compra', 'JA PAGO', 'Conta ( AG: 301 CC: 203409-3)', '5000,00'),
]


def csv_base(linhas=LINHAS):
    out = [CAB]
    for i, (sit, venc, fil, desp, forn, deb, val) in enumerate(linhas):
        out.append(';'.join([sit, br(ONTEM), br(venc), '', 'PIX', fil, 'PLANO ZZ', desp, 'obs ' + desp, forn,
                             '00.000.000/0001-00', str(1000 + i), "'000%d" % i, str(500 + i), 'OBS ZZ %d' % i, deb, val, val]))
    return ('\r\n'.join(out) + '\r\n').encode('cp1252')


def subir(cliente, conteudo, nome='rel_contas_a_pagar_e_ja_pagas.csv'):
    f = io.BytesIO(conteudo)
    f.name = nome
    return cliente.post('/contas-a-pagar/api/rascunhos/', {'arquivo': f})


bancos_ctx, cats_ctx, cfg_ctx = servicos.contexto_regras()

print('== AS 3 TRATATIVAS ==')
linhas_csv = regras.ler_arquivo('x.csv', csv_base())
t('CSV em cp1252 com ; lido com cabeçalhos acentuados', len(linhas_csv) == 9 and 'Situação' in linhas_csv[0]
  and linhas_csv[0]['Débito'].startswith('Conta'), linhas_csv[0].keys())
trat = [regras.tratar(r, bancos_ctx, cats_ctx, cfg_ctx) for r in linhas_csv]
t('Agging: "Compra" + "TELEFONICA BRASIL" vira AGGING', trat[0][0] == 'AGGING' and trat[7][0] == 'AGGING')
t('Agging: sem TELEFONICA fica a própria Despesa', trat[1][0] == 'Compra de mercadoria')
t('Agging: diferencia maiúsculas (regra "compra" não pega "Compra")',
  regras.tratar(linhas_csv[0], bancos_ctx, cats_ctx, dict(cfg_ctx, agging_despesa_contem='compra'))[0]
  == 'Compra de mercadoria')
t('Banco pagador: AG 0301 casa com a agência 301 (zeros à esquerda)', trat[0][1]['id'] == B_SICOOB.id)
t('Banco pagador: mesma conta, agência 3010 vai para a outra conta', trat[1][1]['id'] == B_SICOOB_APL.id)
t('Banco pagador: conta sem agência cadastrada casa só pelo número', trat[3][1]['id'] == B_SEMAG.id)
t('Banco pagador: sem conta no Débito → Não Informado', trat[4][1] is None
  and regras.nome_banco(trat[4][1]) == 'Não Informado')
t('Banco pagador: conta inativa ainda é reconhecida (como no original)', trat[5][1]['id'] == B_INATIVO.id)
t('Banco pagador: nome no formato "BANCO - AG: x CC: y"', regras.nome_banco(trat[0][1]) == 'ZZ SICOOB - REDE - AG: 301 CC: 203409')
t('TipoMacro: AGGING → AGGING', trat[0][2] == 'AGGING')
t('TipoMacro: tabela não diferencia maiúsculas ("energia eletrica" × "Energia Eletrica")', trat[4][2] == 'Energia')
t('TipoMacro: categoria "Não classificado" vira "Não Classificado"', trat[3][2] == 'Não Classificado')
t('TipoMacro: tipo sem categoria → Não Classificado', trat[5][2] == 'Não Classificado')
t('número BR: 1.234,56 / 2500 / 50,5 / 1.000', regras.parse_num('1.234,56') == D('1234.56')
  and regras.parse_num('2500') == 2500 and regras.parse_num('50,5') == D('50.5') and regras.parse_num('1.000') == 1000
  and regras.parse_num('R$ 10,00') == 10 and regras.parse_num('abc') == 0)

print('== SALDO FINAL ==')
t('saldo final = aplicação + garantida + saldo − pagamentos + transferência',
  regras.saldo_final(D('1000'), D('200'), D('50.50'), D('300.25'), D('-100')) == D('850.25'))
tits = [regras.titulo_tratado(i, regras.parse_data(r['Data de vencimento']), regras.parse_num(r['Valor líquido']), False,
                              r, bancos_ctx, cats_ctx, cfg_ctx) for i, r in enumerate(linhas_csv)]
sel = regras.selecionados(tits, HOJE, HOJE)
t('selecionados: só vencimento no intervalo e "A Pagar"', len(sel) == 6 and all(x['situacao'] == 'A Pagar' for x in sel))
lt, ls = regras.linhas_para_publicar(HOJE, sel, bancos_ctx,
                                     {B_SICOOB.id: {'aplicacao': D('1000'), 'garantida': D('0'), 'saldo': D('5000'),
                                                    'transferencia': D('-500')},
                                      B_INTER.id: {'saldo': D('100'), 'transferencia': D('500')}}, 'agora')
por = {l[1]: l for l in ls}
s1 = por['ZZ SICOOB - REDE - AG: 301 CC: 203409']
t('linha de saldo do SICOOB: total 6000, pagamentos 1234,56, final 4265,44',
  s1[6] == D('6000') and s1[7] == D('1234.56') and s1[9] == D('4265.44'), s1)
si = por['ZZ INTER - AG: 0001 CC: 8499970']
t('INTER: 100 − 2500 + 500 = −1900', si[9] == D('-1900'), si)
t('sem conta → linha "Não Informado" com ordem 9999 e final negativo',
  por['Não Informado'][2] == 9999 and por['Não Informado'][9] == D('-300'))
t('conta inativa com pagamento entra', 'ZZ INATIVO - AG: 9 CC: 555' in por
  and 'ZZ SICOOB - APLICAÇÃO - AG: 3010 CC: 203409' in por)
t('conta sem nada no dia não gera linha', regras.linhas_para_publicar(HOJE, [], bancos_ctx, {}, 'agora') == ([], []))
t('título vai com documento sem o apóstrofo e valor com 2 casas', lt[0][12] == '0000' and lt[0][16] == D('1234.56'), lt[0])

print('== PERÍODOS DO PAINEL ==')
qua = date(2026, 11, 11)
t('semana = segunda a domingo', regras.periodo('semana', qua) == (date(2026, 11, 9), date(2026, 11, 15)))
t('quinzena 1–15 e 16–fim', regras.periodo('quinzena', qua) == (date(2026, 11, 1), date(2026, 11, 15))
  and regras.periodo('quinzena', date(2026, 2, 20)) == (date(2026, 2, 16), date(2026, 2, 28)))
t('mês', regras.periodo('mes', date(2026, 12, 5)) == (date(2026, 12, 1), date(2026, 12, 31)))
t('seta: hoje → ontem vira "custom"', regras.deslocar('hoje', qua, qua, -1, qua) == ('custom', qua - timedelta(1), qua - timedelta(1)))
t('seta: custom que volta a hoje vira "hoje"', regras.deslocar('custom', qua - timedelta(1), qua - timedelta(1), 1, qua)[0] == 'hoje')
t('seta: quinzena seguinte e mês anterior', regras.deslocar('quinzena', date(2026, 11, 1), date(2026, 11, 15), 1, qua)[1:]
  == (date(2026, 11, 16), date(2026, 11, 30)) and regras.deslocar('mes', date(2026, 1, 1), date(2026, 1, 31), -1, qua)[1:]
  == (date(2025, 12, 1), date(2025, 12, 31)))

print('== PERMISSÕES ==')
t('pode_ver: SUPERADMIN e liberados sim; sem liberação não', pode_ver(admin) and pode_ver(cons) and not pode_ver(nada))
paginas = ['/contas-a-pagar/', '/contas-a-pagar/pagar-hoje/', '/contas-a-pagar/historico/',
           '/contas-a-pagar/configuracoes/', '/contas-a-pagar/acessos/']
apis = [('get', '/contas-a-pagar/api/painel/'), ('post', '/contas-a-pagar/api/rascunhos/')]
r_anon = [anon.get(p).status_code for p in paginas]
t('anônimo: telas mandam para o login', all(s == 302 for s in r_anon), r_anon)
r_nada = [cn.get(p) for p in paginas]
t('sem liberação: todas as telas barradas (302 para a home)',
  all(r.status_code == 302 and r.url == '/' for r in r_nada), [(r.status_code, r.get('Location')) for r in r_nada])
r_api = [getattr(cn, m)(u).status_code for m, u in apis]
t('sem liberação: APIs dão 403', r_api == [403, 403], r_api)
t('sem liberação: excluir dia e descartar barrados', cn.post('/contas-a-pagar/historico/excluir/', {'dia': HOJE.isoformat()}).status_code == 302
  and cn.post('/contas-a-pagar/api/rascunhos/1/').status_code == 403
  and cn.post('/contas-a-pagar/api/rascunhos/1/descartar/').status_code == 403)
s_cons = [cc.get(p).status_code for p in paginas]
t('CONSULTA: painel e histórico 200; pagar hoje, configurações e acessos barrados', s_cons == [200, 302, 200, 302, 302], s_cons)
t('CONSULTA: não importa (API 403)', subir(cc, csv_base()).status_code == 403)
s_oper = [co.get(p).status_code for p in paginas]
t('OPERADOR: + pagar hoje; configurações e acessos barrados', s_oper == [200, 200, 200, 302, 302], s_oper)
s_conf = [cf.get(p).status_code for p in paginas]
t('CONFIGURADOR: tudo menos acessos', s_conf == [200, 200, 200, 200, 302], s_conf)
r = cf.post('/contas-a-pagar/acessos/', {'acao': 'adicionar', 'user': nada.id, 'papel': 'CONFIGURADOR'})
t('CONFIGURADOR não edita a lista de acessos (POST barrado, nada muda)', r.status_code == 302
  and not AcessoContasPagar.objects.filter(user=nada).exists())
r = cf.post('/contas-a-pagar/acessos/', {'acao': 'papel', 'acesso': AcessoContasPagar.objects.get(user=cons).id, 'papel': 'CONFIGURADOR'})
t('CONFIGURADOR não troca papel de ninguém', AcessoContasPagar.objects.get(user=cons).papel == 'CONSULTA')
t('SUPERADMIN vê acessos', ca.get('/contas-a-pagar/acessos/').status_code == 200)
html_painel_cons = cc.get('/contas-a-pagar/').content.decode()
t('abas: CONSULTA não vê Pagar hoje / Configurações / Acessos', '/contas-a-pagar/pagar-hoje/' not in html_painel_cons
  and '/contas-a-pagar/configuracoes/' not in html_painel_cons and '/contas-a-pagar/acessos/' not in html_painel_cons
  and '/contas-a-pagar/historico/' in html_painel_cons)
html_painel_conf = cf.get('/contas-a-pagar/').content.decode()
t('abas: CONFIGURADOR não vê Acessos', '/contas-a-pagar/acessos/' not in html_painel_conf
  and '/contas-a-pagar/configuracoes/' in html_painel_conf)

print('== IMPORTAR E MONTAR ==')
duble.db.execute("INSERT INTO cp_saldos_bancos VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ((HOJE - timedelta(days=10)).isoformat(), 'ZZ SICOOB - REDE - AG: 301 CC: 203409', 0, 1000, 50, 7000, 8050,
                  0, 0, 8050, '2026-01-01 10:00:00'))
duble.db.commit()
r = subir(co, csv_base())
j = r.json()
t('operador importa o CSV', r.status_code == 200 and j.get('id'), j)
rasc = Rascunho.objects.get(pk=j['id'])
t('rascunho: 9 linhas, dono é quem importou, datas = hoje', rasc.titulos.count() == 9 and rasc.user_id == oper.id
  and rasc.data_pagamento == HOJE == rasc.venc_ini == rasc.venc_fim)
t('saldos pré-preenchidos com o último dia publicado (sem transferência)',
  rasc.saldos.filter(conta=B_SICOOB, aplicacao=1000, garantida=50, saldo=7000, transferencia=0).exists())
r = subir(co, b'qualquer;coisa\n1;2\n')
t('arquivo que não é o relatório é recusado com a mensagem', r.status_code == 400 and 'rel_contas_a_pagar' in r.json()['erro'])
t('extensão estranha recusada', subir(co, b'x', nome='virus.exe').status_code == 400)
t('tela de montagem abre', co.get(f'/contas-a-pagar/pagar-hoje/{rasc.id}/').status_code == 200)
t('outro operador não abre o rascunho alheio (404)', co2.get(f'/contas-a-pagar/api/rascunhos/{rasc.id}/').status_code == 404
  and co2.post(f'/contas-a-pagar/api/rascunhos/{rasc.id}/descartar/').status_code == 404)
det = co.get(f'/contas-a-pagar/api/rascunhos/{rasc.id}/').json()
t('detalhe traz títulos tratados e só contas ativas', len(det['titulos']) == 9 and det['titulos'][0]['tipo_macro']
  and all(b['ativo'] for b in det['bancos']) and len(det['bancos']) == 4)
excluir = [x['id'] for x in det['titulos'] if x['fornecedor'] == 'PAPELARIA ZZ']
corpo = {'data_pagamento': HOJE.isoformat(), 'venc_ini': ONTEM.isoformat(), 'venc_fim': HOJE.isoformat(),
         'excluidos': excluir,
         'saldos': [{'banco_id': B_SICOOB.id, 'aplicacao': '1.000,00', 'garantida': 0, 'saldo': '5.000,00', 'transferencia': '-500'},
                    {'banco_id': B_INTER.id, 'saldo': 100, 'transferencia': 500}]}
r = co.post(f'/contas-a-pagar/api/rascunhos/{rasc.id}/', json.dumps(corpo), content_type='application/json')
rasc.refresh_from_db()
t('salvar rascunho grava período, desmarcados e saldos', r.status_code == 200 and rasc.venc_ini == ONTEM
  and rasc.titulos.filter(excluido=True).count() == 1 and rasc.saldos.count() == 2
  and rasc.saldos.get(conta=B_SICOOB).saldo == D('5000'))
r = co.post(f'/contas-a-pagar/api/rascunhos/{rasc.id}/', json.dumps({'venc_ini': '2026-12-31', 'venc_fim': '2026-01-01'}),
            content_type='application/json')
t('período invertido recusado', r.status_code == 400)

print('== PUBLICAR ==')
corpo['status'] = 'publicado'
r = co.post(f'/contas-a-pagar/api/rascunhos/{rasc.id}/', json.dumps(corpo), content_type='application/json')
j = r.json()
# selecionados: venc ontem..hoje, A Pagar, sem a papelaria → 6 de hoje (−1) + 1 de ontem = 6
esperado_valor = D('1234.56') + D('100') + D('2500') + D('300') + D('10') + D('400')
t('publicou 6 títulos com o valor certo', r.status_code == 200 and j['titulos'] == 6 and D(str(j['valor'])) == esperado_valor, j)
mt = duble.q('SELECT * FROM cp_titulos_programados WHERE data_pagamento=?', (HOJE.isoformat(),))
ms = duble.q('SELECT * FROM cp_saldos_bancos WHERE data_pagamento=?', (HOJE.isoformat(),))
t('MySQL (dublê) recebeu 6 títulos do dia', len(mt) == 6)
t('colunas tratadas gravadas (agging, tipo_macro, banco_pagador)',
  any(x['agging'] == 'AGGING' and x['tipo_macro'] == 'AGGING' and x['banco_pagador'].startswith('ZZ SICOOB - REDE') for x in mt)
  and any(x['banco_pagador'] == 'Não Informado' for x in mt))
sic = next(x for x in ms if x['banco'].startswith('ZZ SICOOB - REDE'))
t('saldo do SICOOB: 6000 − (1234,56 + 400) − 500 = 3865,44', abs(float(sic['saldo_final']) - 3865.44) < 0.001
  and abs(float(sic['pagamentos']) - 1634.56) < 0.001, sic)
t('rascunho apagado depois de publicar', not Rascunho.objects.filter(pk=rasc.id).exists())
log = LogContasPagar.objects.filter(acao='PUBLICOU').first()
t('log da publicação: quem, dia, quantos, valor', log and log.user_id == oper.id and log.data_pagamento == HOJE
  and log.qtd_titulos == 6 and log.valor == esperado_valor and log.detalhe['substituiu']['qtd'] == 0)
t('URL de volta abre o painel no dia publicado', 'ini=' + HOJE.isoformat() in j['url'])

# publicar de novo a mesma data substitui
r2 = subir(co, csv_base())
rid = r2.json()['id']
r = co.post(f'/contas-a-pagar/api/rascunhos/{rid}/', json.dumps({'data_pagamento': HOJE.isoformat(), 'venc_ini': AMANHA.isoformat(),
                                                                'venc_fim': AMANHA.isoformat(), 'excluidos': [], 'saldos': [],
                                                                'status': 'publicado'}), content_type='application/json')
mt = duble.q('SELECT * FROM cp_titulos_programados WHERE data_pagamento=?', (HOJE.isoformat(),))
ms = duble.q('SELECT * FROM cp_saldos_bancos WHERE data_pagamento=?', (HOJE.isoformat(),))
t('republicar o mesmo dia substitui títulos e saldos', r.status_code == 200 and len(mt) == 1 and mt[0]['fornecedor'] == 'TELEFONICA BRASIL SA'
  and len(ms) == 1 and abs(float(ms[0]['saldo_final']) + 999.99) < 0.001, (len(mt), ms))
log2 = LogContasPagar.objects.filter(acao='PUBLICOU').first()
t('log da republicação guarda o que foi substituído', log2.detalhe['substituiu']['qtd'] == 6)
# outro dia (amanhã) para o painel
r3 = subir(co, csv_base())
co.post(f"/contas-a-pagar/api/rascunhos/{r3.json()['id']}/", json.dumps({'data_pagamento': AMANHA.isoformat(), 'venc_ini': ONTEM.isoformat(),
        'venc_fim': HOJE.isoformat(), 'excluidos': [], 'saldos': [{'banco_id': B_INTER.id, 'saldo': 10000}], 'status': 'publicado'}),
        content_type='application/json')
t('dois dias publicados no dublê', len(duble.q('SELECT DISTINCT data_pagamento FROM cp_titulos_programados')) == 2)

print('== MYSQL FORA DO AR ==')
r4 = subir(co, csv_base())
rid4 = r4.json()['id']
duble.fora = True
r = co.post(f'/contas-a-pagar/api/rascunhos/{rid4}/', json.dumps({'status': 'publicado'}), content_type='application/json')
t('publicar com MySQL fora: 503 com mensagem e rascunho mantido', r.status_code == 503 and 'MySQL' in r.json()['erro']
  and Rascunho.objects.filter(pk=rid4).exists())
r = cc.get('/contas-a-pagar/')
t('painel com MySQL fora mostra o aviso (sem 500)', r.status_code == 200 and 'dublê fora do ar' in r.content.decode())
t('histórico com MySQL fora mostra o aviso', cc.get('/contas-a-pagar/historico/').status_code == 200)
r5 = subir(co, csv_base())
t('importar com MySQL fora funciona e avisa que não pré-preencheu', r5.status_code == 200 and r5.json()['aviso'])
duble.fora = False
co.post(f'/contas-a-pagar/api/rascunhos/{rid4}/descartar/')
co.post(f"/contas-a-pagar/pagar-hoje/{r5.json()['id']}/descartar/")
t('descartar rascunho (API e tela)', not Rascunho.objects.filter(pk__in=[rid4, r5.json()['id']]).exists())

print('== PAINEL ==')
r = cc.get('/contas-a-pagar/')
html = r.content.decode()
t('painel abre em Hoje com o título e o saldo', r.status_code == 200 and 'Hoje · ' in html and 'TELEFONICA BRASIL SA' in html
  and 'Acompanhamento de contas' in html)
t('contas ativas sem movimento aparecem; inativa sem movimento não', 'ZZ CORA SEM AGÊNCIA' in html and 'ZZ INATIVO' not in html)
r = cc.get(f'/contas-a-pagar/?modo=semana&ini={HOJE.isoformat()}')
ini_s, fim_s = regras.periodo('semana', HOJE)
t('semana: período normalizado e "Pagamentos por dia"', r.context['ini'] == ini_s and r.context['fim'] == fim_s
  and 'Pagamentos por dia' in r.content.decode())
r = cc.get(f'/contas-a-pagar/?modo=custom&ini={ONTEM.isoformat()}&fim={AMANHA.isoformat()}')
t('intervalo: soma os dois dias publicados', r.context['qtd_todos'] == 1 + 7, r.context['qtd_todos'])
t('mês abre', cc.get(f'/contas-a-pagar/?modo=mes&ini={HOJE.isoformat()}').status_code == 200)
r = cc.get(f'/contas-a-pagar/?modo=custom&ini={(HOJE + timedelta(days=40)).isoformat()}&fim={(HOJE + timedelta(days=41)).isoformat()}')
t('período sem nada publicado: mensagem de vazio', 'Nenhuma programação publicada neste período' in r.content.decode())
ini_m, fim_m = regras.periodo('mes', HOJE)
base = f'/contas-a-pagar/?modo=custom&ini={ONTEM.isoformat()}&fim={AMANHA.isoformat()}'
nome_inter = B_INTER.nome
r = cc.get(base + '&banco=' + nome_inter.replace(' ', '+').replace(':', '%3A'))
t('filtro por banco (clique no banco)', r.context['titulos'] and all(x['banco'] == nome_inter for x in r.context['titulos'])
  and len(r.context['titulos']) == 1)
r = cc.get(base + '&categoria=Aluguéis')
t('filtro por categoria', len(r.context['titulos']) == 2 and all(x['tipo_macro'] == 'Aluguéis' for x in r.context['titulos']))
r = cc.get(base + '&filial=SEDE+ZZ')
t('filtro por filial', len(r.context['titulos']) == 3 and all(x['filial'] == 'SEDE ZZ' for x in r.context['titulos']))
r = cc.get(base + '&filial=LOJA+ZZ+A&categoria=AGGING')
t('filtros somam (filial E categoria)', len(r.context['titulos']) == 2)
t('KPIs não mudam com o filtro (como o original)', r.context['tot']['pagamentos'] == cc.get(base).context['tot']['pagamentos'])
t('filtro ativo vira link de desfazer', 'limpar filtro' in r.content.decode())
j = cc.get(f'/contas-a-pagar/api/painel/?ini={ONTEM.isoformat()}&fim={AMANHA.isoformat()}&filial=SEDE+ZZ').json()
t('API do painel: mesmos filtros e totais', len(j['titulos']) == 3 and j['totais']['pagamentos'] > 0 and len(j['dias']) == 2)
r = cc.get(f'/contas-a-pagar/?modo=custom&ini={HOJE.isoformat()}&fim={HOJE.isoformat()}')
t('setas: anterior/seguinte de um dia', f'ini={ONTEM.isoformat()}' in r.context['setas']['anterior']
  and f'ini={AMANHA.isoformat()}' in r.context['setas']['seguinte'])
t('parâmetros inválidos não quebram', cc.get('/contas-a-pagar/?modo=xpto&ini=abc&fim=2026-99-99').status_code == 200)

print('== HISTÓRICO E EXCLUIR DIA ==')
html = cc.get('/contas-a-pagar/historico/').content.decode()
t('histórico lista os dois dias', br(HOJE) in html and br(AMANHA) in html)
t('CONSULTA não vê o botão de excluir', 'historico/excluir' not in html)
r = cc.post('/contas-a-pagar/historico/excluir/', {'dia': AMANHA.isoformat()})
t('CONSULTA não exclui dia', len(duble.q('SELECT * FROM cp_titulos_programados WHERE data_pagamento=?', (AMANHA.isoformat(),))) == 7)
r = co.post('/contas-a-pagar/historico/excluir/', {'dia': AMANHA.isoformat()})
t('OPERADOR exclui o dia (títulos e saldos)', r.status_code == 302
  and not duble.q('SELECT * FROM cp_titulos_programados WHERE data_pagamento=?', (AMANHA.isoformat(),))
  and not duble.q('SELECT * FROM cp_saldos_bancos WHERE data_pagamento=?', (AMANHA.isoformat(),)))
le = LogContasPagar.objects.filter(acao='EXCLUIU_DIA').first()
t('log da exclusão: quem, dia, quantos e valor que havia', le and le.user_id == oper.id and le.data_pagamento == AMANHA
  and le.qtd_titulos == 7 and le.valor == esperado_valor + D('50.50') and le.detalhe['saldos'] >= 1, le and (le.qtd_titulos, le.valor))
t('excluir com data inválida não faz nada', co.post('/contas-a-pagar/historico/excluir/', {'dia': 'xx'}).status_code == 302
  and LogContasPagar.objects.filter(acao='EXCLUIU_DIA').count() == 1)
t('dia de hoje continua lá', len(duble.q('SELECT * FROM cp_titulos_programados WHERE data_pagamento=?', (HOJE.isoformat(),))) == 1)

print('== XLSX ==')
import openpyxl  # noqa: E402
wb = openpyxl.Workbook()
ws = wb.active
ws.append(CAB.split(';'))
ws.append(['A Pagar', ONTEM, HOJE, None, 'PIX', 'LOJA X', 'P', 'Compra', 'o', 'TELEFONICA BRASIL S/A', '1', 142543, "'0099", 5,
           'obs', 'Conta ( AG: 301 CC: 203409-3)', 77.7, 77.7])
ws.append([None] * 18)
buf = io.BytesIO()
wb.save(buf)
r = subir(co, buf.getvalue(), nome='base.xlsx')
rx = Rascunho.objects.get(pk=r.json()['id'])
tx = servicos.titulos_do_rascunho(rx)
t('xlsx: data, número e linha vazia tratados', rx.titulos.count() == 1 and tx[0]['vencimento'] == HOJE.isoformat()
  and tx[0]['valor'] == D('77.70') and tx[0]['agging'] == 'AGGING' and tx[0]['banco_id'] == B_SICOOB.id)
_, lt, _ = servicos.calcular_publicacao(rx)
t('xlsx: nº do movimento sai como texto "142543"', lt[0][11] == '142543', lt[0][11])
rx.delete()

print('== CONFIGURAÇÕES ==')
P = '/contas-a-pagar/configuracoes/'
print('-- contas vêm do Financeiro › Bancos (13/11/2026)')
t('agência entra sem o dígito (301-0 → 301) e casa com o Débito', B_SICOOB.agencia == '301'
  and regras.resolver_banco('Conta ( AG: 301 CC: 203409-3)', [b.como_dict() for b in servicos.contas_do_financeiro()])['id'] == B_SICOOB.id)
fin_nova = ContaFin.objects.create(nome='zz banco novo', agencia='12-7', conta='3456-1')
html = cf.get(P + '?aba=bancos').content.decode()
nova = ContaBancaria.objects.filter(conta_financeiro=fin_nova).first()
t('conta nova no Financeiro aparece aqui no fim, em uso, com nome em maiúsculas', nova and nova.ordem == 5 and nova.ativo
  and nova.banco == 'ZZ BANCO NOVO' and nova.agencia == '12' and nova.conta == '3456' and 'ZZ BANCO NOVO' in html)
t('a tela aponta para o cadastro do Financeiro e não tem mais "Adicionar conta"', 'Financeiro › Bancos' in html
  and 'conta_adicionar' not in html and 'name="banco"' not in html)
cf.post(P, {'aba': 'bancos', 'acao': 'conta_usar', 'id': nova.id})
nova.refresh_from_db()
t('desmarcar "usar aqui" tira do Contas a Pagar', nova.ativo is False)
cf.post(P, {'aba': 'bancos', 'acao': 'conta_usar', 'id': nova.id, 'ativo': 'on'})
nova.refresh_from_db()
t('marcar de novo volta', nova.ativo is True)
cf.post(P, {'aba': 'bancos', 'acao': 'conta_subir', 'id': nova.id})
nova.refresh_from_db()
t('subir a conta troca a ordem', nova.ordem == 4 and ContaBancaria.objects.get(pk=B_INATIVO.pk).ordem == 5)
ContaFin.objects.filter(pk=fin_nova.pk).update(nome='ZZ BANCO RENOMEADO', agencia='99')
servicos.sincronizar_contas()
nova.refresh_from_db()
t('renomear no Financeiro reflete aqui (e mantém ordem e "usar")', nova.banco == 'ZZ BANCO RENOMEADO' and nova.agencia == '99'
  and nova.ordem == 4 and nova.ativo is True)
ContaFin.objects.filter(pk=fin_nova.pk).update(ativa=False)
t('conta desativada no Financeiro some daqui', nova.pk not in [c.pk for c in servicos.contas_do_financeiro()])
caixa = ContaFin.objects.create(nome='ZZ CAIXA LOJA', tipo='CAIXA')
t('conta do tipo Caixa não entra', 'ZZ CAIXA LOJA' not in [c.banco for c in servicos.contas_do_financeiro()])
t('editar banco/agência/conta por aqui não existe mais', cf.post(P, {'aba': 'bancos', 'acao': 'conta_salvar', 'id': B_INTER.id,
  'banco': 'HACK', 'agencia': '1', 'conta': '2'}).status_code == 302 and ContaBancaria.objects.get(pk=B_INTER.pk).banco == 'ZZ INTER')
ContaFin.objects.filter(pk__in=[fin_nova.pk, caixa.pk]).delete()
r6 = subir(co, csv_base())
html = cf.get(P + '?aba=categorias').content.decode()
t('aba categorias lista o tipo sem categoria dos rascunhos', 'DESPESA NOVA ZZ' in html)
cf.post(P, {'aba': 'categorias', 'acao': 'categoria_salvar', 'tipo': 'DESPESA NOVA ZZ', 'categoria': 'Despesas'})
t('definir a categoria corrige o TipoMacro do rascunho',
  any(x['tipo_macro'] == 'Despesas' for x in servicos.titulos_do_rascunho(Rascunho.objects.get(pk=r6.json()['id']))))
cf.post(P, {'aba': 'categorias', 'acao': 'categoria_remover', 'tipo': 'DESPESA NOVA ZZ'})
t('remover categoria', not Categoria.objects.filter(tipo='DESPESA NOVA ZZ').exists())
wb = openpyxl.Workbook()
wb.active.title = 'Base'
d = wb.create_sheet('Dados')
for linha in (('Tipo de Conta', 'Categoria'), ('ZZ IMPORTADA', 'Energia'), ('Compra', 'Fornecedor 2'), (None, None), ('DEPOIS', 'X')):
    d.append(linha)
buf = io.BytesIO()
wb.save(buf)
buf.seek(0)
buf.name = 'calc.xlsx'
cf.post(P, {'aba': 'categorias', 'acao': 'categoria_importar', 'arquivo': buf})
t('importar aba Dados: até a primeira linha vazia, atualiza o que existe',
  Categoria.objects.filter(tipo='ZZ IMPORTADA', categoria='Energia').exists()
  and Categoria.objects.get(tipo='Compra').categoria == 'Fornecedor 2' and not Categoria.objects.filter(tipo='DEPOIS').exists())
cf.post(P, {'aba': 'regras', 'acao': 'regras_salvar', 'agging_despesa_contem': 'Compra', 'agging_fornecedor_contem': 'IMOB'})
cfg = ConfiguracaoContasPagar.atual()
t('regra do Agging salva e usada', cfg.agging_fornecedor_contem == 'IMOB'
  and regras.tratar({'Despesa': 'Compra X', 'Fornecedor': 'IMOB Y'}, [], {}, cfg.como_dict())[0] == 'AGGING')
cf.post(P, {'aba': 'regras', 'acao': 'regras_salvar', 'agging_despesa_contem': 'Compra', 'agging_fornecedor_contem': 'TELEFONICA BRASIL'})
t('OPERADOR não salva configuração', co.post(P, {'aba': 'regras', 'acao': 'regras_salvar', 'agging_despesa_contem': 'x',
                                                 'agging_fornecedor_contem': 'y'}).status_code == 302
  and ConfiguracaoContasPagar.atual().agging_despesa_contem == 'Compra')
for aba in ('bancos', 'categorias', 'regras'):
    t(f'aba {aba} abre', cf.get(P + '?aba=' + aba).status_code == 200)

print('== ACESSOS (SUPERADMIN) ==')
ca.post('/contas-a-pagar/acessos/', {'acao': 'adicionar', 'user': nada.id, 'papel': 'OPERADOR'})
a = AcessoContasPagar.objects.filter(user=nada).first()
t('SUPERADMIN libera pessoa com papel', a and a.papel == 'OPERADOR')
cn2 = Client()
cn2.force_login(User.objects.get(pk=nada.pk))
t('liberado passa a entrar', cn2.get('/contas-a-pagar/pagar-hoje/').status_code == 200)
ca.post('/contas-a-pagar/acessos/', {'acao': 'papel', 'acesso': a.id, 'papel': 'CONSULTA'})
a.refresh_from_db()
t('SUPERADMIN troca o papel', a.papel == 'CONSULTA')
ca.post('/contas-a-pagar/acessos/', {'acao': 'remover', 'acesso': a.id})
t('SUPERADMIN remove o acesso', not AcessoContasPagar.objects.filter(user=nada).exists()
  and Client().get('/contas-a-pagar/').status_code == 302)

print('== COMMAND importar_contas_pagar_local ==')
velho = PASTA / 'dados_antigo.db'
sq = sqlite3.connect(velho)
sq.executescript("""
CREATE TABLE bancos (id INTEGER PRIMARY KEY AUTOINCREMENT, banco TEXT NOT NULL, agencia TEXT DEFAULT '', conta TEXT DEFAULT '',
  ordem INTEGER DEFAULT 0, ativo INTEGER DEFAULT 1);
CREATE TABLE categorias (tipo TEXT PRIMARY KEY, categoria TEXT NOT NULL);
CREATE TABLE config (chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, criado_em TEXT NOT NULL);
""")
sq.executemany('INSERT INTO bancos (banco, agencia, conta, ordem, ativo) VALUES (?,?,?,?,?)',
               [('ZZ SICOOB - REDE', '301', '203409', 7, 1), ('ZZ BANCO ANTIGO', '', '4242', 8, 0), ('ZZ OUTRO', '0049', '1111', 9, 1)])
sq.executemany('INSERT INTO categorias VALUES (?,?)', [('ZZ CAT VELHA', 'Benefícios'), ('Compra', 'Fornecedor')])
sq.executemany('INSERT INTO config VALUES (?,?)', [('agging_despesa_contem', 'Compra'), ('agging_fornecedor_contem', 'TELEFONICA BRASIL'),
                                                    ('empresa', 'Rede')])
sq.commit()
sq.close()
antes = ContaBancaria.objects.count()
saida = io.StringIO()
call_command('importar_contas_pagar_local', str(velho), '--simular', stdout=saida)
t('--simular não grava', ContaBancaria.objects.count() == antes and 'SIMULAÇÃO' in saida.getvalue())
saida = io.StringIO()
call_command('importar_contas_pagar_local', str(velho), stdout=saida)
t('contas bancárias não são mais importadas (vêm do Financeiro)', ContaBancaria.objects.count() == antes
  and ContaBancaria.objects.get(pk=B_SICOOB.pk).ordem == 0 and 'vêm do Financeiro' in saida.getvalue(), saida.getvalue())
t('importa categorias (nova e atualizada)', Categoria.objects.filter(tipo='ZZ CAT VELHA', categoria='Benefícios').exists()
  and Categoria.objects.get(tipo='Compra').categoria == 'Fornecedor')
n_c, n_cat = ContaBancaria.objects.count(), Categoria.objects.count()
saida = io.StringIO()
call_command('importar_contas_pagar_local', str(velho), stdout=saida)
t('rodar de novo não duplica nada (idempotente)', ContaBancaria.objects.count() == n_c and Categoria.objects.count() == n_cat
  and 'Categorias: 0 nova(s), 0 atualizada(s)' in saida.getvalue(), saida.getvalue())
try:
    call_command('importar_contas_pagar_local', str(PASTA / 'nao_existe.db'))
    t('arquivo inexistente dá erro', False)
except Exception as exc:  # noqa: BLE001
    t('arquivo inexistente dá erro', 'não encontrado' in str(exc))

print('== SEMENTE DE CATEGORIAS ==')
import importlib  # noqa: E402

from django.apps import apps as django_apps  # noqa: E402
sem = importlib.import_module('contas_pagar.migrations.0002_semente_categorias')
Categoria.objects.all().delete()
Categoria.objects.create(tipo='AGGING', categoria='Editada')
sem.semear(django_apps, None)
t('semente cria as 128 categorias do sistema antigo sem sobrescrever a editada',
  Categoria.objects.count() == 128 and Categoria.objects.get(tipo='AGGING').categoria == 'Editada', Categoria.objects.count())

print('== TEMPLATES ==')
pasta = pathlib.Path(__file__).resolve().parent / 'templates' / 'contas_pagar'
multilinha = []
for arq in sorted(pasta.glob('*.html')):
    texto = arq.read_text()
    for m in re.finditer(r'\{#(.*?)#\}', texto, re.S):
        if '\n' in m.group(1):
            multilinha.append(arq.name)
    if texto.count('{#') != texto.count('#}'):
        multilinha.append(arq.name + ' (desparelhado)')
t('nenhum {# #} em várias linhas', not multilinha, multilinha)
rid6 = r6.json()['id']
telas = [ca.get(u).content.decode() for u in ('/contas-a-pagar/', '/contas-a-pagar/?modo=mes', '/contas-a-pagar/pagar-hoje/',
                                               f'/contas-a-pagar/pagar-hoje/{rid6}/', '/contas-a-pagar/historico/',
                                               P + '?aba=bancos', P + '?aba=categorias', P + '?aba=regras', '/contas-a-pagar/acessos/')]
t('nenhum comentário vazando na tela', all('{#' not in p and '#}' not in p and '{% comment' not in p for p in telas))
t('nenhuma conexão real ao MySQL (só o dublê)', duble.conexoes > 0)

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
