"""/financeiro/conciliacao/ (03/11/2026): extrato de um lado, contas a pagar/receber do outro.

Cobre a comparação (negativo × a pagar, positivo × a receber; valor, descrição, CNPJ, CPF mascarado),
a conciliação com título (baixa com o valor do banco: total, parcial, com juros), o estorno ao desfazer,
permissões e recorte de loja, e o botão de excluir a importação.

Roda num sqlite descartável.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-fin-concil-'))
(PASTA / 'zz_fin_concil_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fcon'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-fcon-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_fin_concil_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import datetime as dt
from decimal import Decimal as D

import requests

from financeiro import extrato
from financeiro.models import (AcessoFinanceiro, Baixa, ContaBancaria, ExtratoImportacao, ExtratoLinha, LogFinanceiro,
                               MovimentoBancario, Titulo)
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

gloria = Sector.objects.create(name='Loja Glória')
serra = Sector.objects.create(name='Loja Serra Sede')
esc = Sector.objects.create(name='Financeiro')


def usuario(nome, permissoes=None, lojas=(), hierarquia='PADRAO'):
    u = User.objects.create_user(username=f'zzc.{nome}', email=f'zzc.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZC', last_name=nome, sector=esc, hierarchy=hierarquia)
    if permissoes:
        a = AcessoFinanceiro.objects.create(user=u, permissoes=permissoes, ativo=True)
        a.lojas.add(*lojas)
    c = Client(); c.force_login(u)
    return u, c


chefe, cs = usuario('super', hierarquia='SUPERADMIN')
so_conc, cc = usuario('concilia', ['conciliacao.conciliar', 'pagar.ver'])
limitado, cl = usuario('limitado', ['conciliacao.conciliar', 'pagar.ver', 'receber.ver', 'baixas.baixar'], [gloria])
conta = ContaBancaria.objects.create(nome='SICOOB', banco_nome='SICOOB', agencia='301', conta='203409', data_saldo_inicial=dt.date(2026, 1, 1), loja=gloria)
DIA = dt.date(2026, 10, 30)
imp = ExtratoImportacao.objects.create(conta=conta, arquivo_nome='extrato.ofx', formato='OFX', linhas_novas=6)


def linha(valor, descricao, n=[0]):
    n[0] += 1
    return ExtratoLinha.objects.create(conta=conta, importacao=imp, data=DIA, valor=D(valor), descricao=descricao, identificador=f'i{n[0]}')


def titulo(tipo, valor, pessoa, descricao='Conta', doc='', loja=None, venc=DIA):
    return Titulo.objects.create(tipo=tipo, descricao=descricao, pessoa_nome=pessoa, pessoa_documento=doc, vencimento=venc, valor=D(valor), loja=loja)


l_cnpj = linha('-9369.10', 'PIX EMITIDO OUTRA IF Pagamento Pix 02.558.157 0001-62')
l_cpf = linha('-730.00', 'PIX EMITIDO OUTRA IF Pagamento Pix ***.090.978-** VT')
l_nome = linha('-350.00', 'DÉB.TRANSF.CONTAS DIF.TIT. INTERCREDIS FAV.: 58.037.711 JOCIVAN SILVERI')
l_entrada = linha('2992.00', 'PIX RECEBIDO - OUTRA IF Recebimento Pix JONY CUINS')
l_nada = linha('-11.11', 'TARIFA BANCARIA')

telefonica = titulo('PAGAR', '9369.10', 'TELEFÔNICA BRASIL SA', doc='02558157000162')
telefonica2 = titulo('PAGAR', '147.69', 'TELEFÔNICA BRASIL SA', 'Internet', doc='02558157000324')
mesmo_valor = titulo('PAGAR', '9369.10', 'OUTRA EMPRESA', 'Sem relação')
vt = titulo('PAGAR', '730.00', 'MARIA VT', doc='12309097812', loja=gloria)
jocivan = titulo('PAGAR', '500.00', 'JOCIVAN SILVERIO', loja=serra)
receber = titulo('RECEBER', '2992.00', 'JONY CUINS')
receber_valor_de_pagar = titulo('RECEBER', '9369.10', 'CLIENTE X')
quitado = titulo('PAGAR', '11.11', 'JA PAGO')
quitado.status = 'CANCELADO'; quitado.save()

print('Comparação')
abertos = list(Titulo.objects.filter(status__in=('ABERTO', 'PARCIAL')))
c = extrato.comparar([l_cnpj, l_cpf, l_nome, l_entrada, l_nada], abertos)
pares = c[l_cnpj.id]['pares']
t('negativo só com a pagar', all(Titulo.objects.get(pk=p['id']).tipo == 'PAGAR' for p in pares) and receber_valor_de_pagar.id not in [p['id'] for p in pares])
t('valor + CNPJ vem primeiro, com o que bateu', pares[0]['id'] == telefonica.id and pares[0]['valor'] and pares[0]['documento'] and c[l_cnpj.id]['situacao'] == 'bate', pares)
t('depois só o valor, depois só o CNPJ', [p['id'] for p in pares] == [telefonica.id, mesmo_valor.id, telefonica2.id], pares)
t('CPF mascarado do banco casa', c[l_cpf.id]['pares'][0]['id'] == vt.id and c[l_cpf.id]['pares'][0]['documento'] and c[l_cpf.id]['situacao'] == 'bate')
t('só o nome parecido: situação descrição', c[l_nome.id]['pares'][0]['id'] == jocivan.id and c[l_nome.id]['pares'][0]['descricao']
  and not c[l_nome.id]['pares'][0]['valor'] and c[l_nome.id]['situacao'] == 'descricao', c[l_nome.id])
t('positivo com a receber', c[l_entrada.id]['pares'][0]['id'] == receber.id and c[l_entrada.id]['situacao'] == 'bate')
t('palavra de banco ("tarifa") não faz par, e cancelada não entra', c[l_nada.id] == {'situacao': '', 'pares': []}, c[l_nada.id])

print('Tela dividida')
r = cs.get('/financeiro/conciliacao/', {'conta': conta.id})
h = r.content.decode()
t('duas colunas iguais, com o painel de comparação', r.status_code == 200 and 'lg:grid-cols-2' in h and 'id="finComp"' in h and 'id="finCompDados"' in h)
t('as linhas mostram o que bateu', 'bate com conta' in h and 'só a descrição bate' in h and 'sem par' in h)
t('continua com lançar e ignorar; sem movimento livre, nada de "ligar"', 'lançar na conta' in h and 'ignorar' in h and 'outro movimento' not in h)
t('importação com botão de excluir', 'excluir importação' in h)
h = cc.get('/financeiro/conciliacao/', {'conta': conta.id}).content.decode()
t('sem "Dar baixa": compara, mas não concilia', '"id": %d' % telefonica.id in h and 'const PODE = false' in h)
t('só vê pagar: nada de a receber nos dados', 'CLIENTE X' not in h and '"tipo": "RECEBER"' not in h)
h = cl.get('/financeiro/conciliacao/', {'conta': conta.id}).content.decode()
t('recorte de loja: só as contas das lojas dele', 'MARIA VT' in h and 'JOCIVAN SILVERIO' not in h and 'OUTRA EMPRESA' not in h, ('MARIA VT' in h, 'JOCIVAN SILVERIO' in h, 'OUTRA EMPRESA' in h))

print('Conciliar com a conta')
URL = f'/financeiro/conciliacao/linha/{l_cnpj.id}/titulo/'
cc.post(URL, {'titulo': telefonica.id})
t('sem "Dar baixa" recusa', not Baixa.objects.exists() and not ExtratoLinha.objects.get(pk=l_cnpj.id).movimento_id)
cs.post(URL, {'titulo': receber_valor_de_pagar.id})
t('saída não concilia com a receber', not Baixa.objects.exists())
cl.post(f'/financeiro/conciliacao/linha/{l_nome.id}/titulo/', {'titulo': jocivan.id})
t('conta de outra loja recusa', not Baixa.objects.exists())
cs.post(URL, {'titulo': telefonica.id})
telefonica.refresh_from_db(); l_cnpj.refresh_from_db()
b = Baixa.objects.get(titulo=telefonica)
t('valor igual: quita', telefonica.status == 'QUITADO' and b.valor == D('9369.10') and b.juros == 0 and b.data == DIA and b.conta == conta and b.forma == 'PIX', (telefonica.status, b.forma))
t('a linha fica ligada ao movimento, com o valor do banco', l_cnpj.movimento_id == b.movimento.id and b.movimento.valor == D('-9369.10') and b.movimento.conciliado and l_cnpj.conciliada_por == chefe)
t('saldo do portal saiu', conta.saldo() == D('-9369.10'), conta.saldo())
t('rastreabilidade', LogFinanceiro.objects.filter(acao='BAIXAR').exists() and LogFinanceiro.objects.filter(acao='CONCILIAR').exists())
cs.post(URL, {'titulo': mesmo_valor.id})
t('linha conciliada não concilia de novo', not Baixa.objects.filter(titulo=mesmo_valor).exists())

r = cs.post(f'/financeiro/conciliacao/linha/{l_nome.id}/titulo/', {'titulo': jocivan.id}, follow=True)
jocivan.refresh_from_db(); l_nome.refresh_from_db()
t('banco menor que a conta: baixa parcial', jocivan.status == 'PARCIAL' and jocivan.saldo == D('150.00') and l_nome.movimento.valor == D('-350.00')
  and 'faltam R$ 150' in r.content.decode(), (jocivan.status, jocivan.saldo))
t('forma vem do texto do banco', Baixa.objects.get(titulo=jocivan).forma == 'TED')
pequena = titulo('PAGAR', '700.00', 'MARIA VT', doc='12309097812')
r = cs.post(f'/financeiro/conciliacao/linha/{l_cpf.id}/titulo/', {'titulo': pequena.id}, follow=True)
pequena.refresh_from_db(); l_cpf.refresh_from_db()
bj = Baixa.objects.get(titulo=pequena)
t('banco maior: quita e o resto vira juros; movimento igual à linha', pequena.status == 'QUITADO' and bj.valor == D('700') and bj.juros == D('30') and l_cpf.movimento.valor == D('-730.00')
  and 'em juros' in r.content.decode(), (bj.valor, bj.juros))
cs.post(f'/financeiro/conciliacao/linha/{l_entrada.id}/titulo/', {'titulo': receber.id})
receber.refresh_from_db(); l_entrada.refresh_from_db()
t('entrada recebe a conta a receber', receber.status == 'QUITADO' and l_entrada.movimento.valor == D('2992.00'))

print('Desfazer')
cs.post(f'/financeiro/conciliacao/linha/{l_nome.id}/desfazer/')
jocivan.refresh_from_db(); l_nome.refresh_from_db()
bx = Baixa.objects.get(titulo=jocivan)
t('estorna a baixa e a conta volta a aberto', bx.estornada and jocivan.status == 'ABERTO' and jocivan.saldo == D('500') and not l_nome.movimento_id, (jocivan.status, jocivan.saldo))
t('movimento estornado sai do saldo', MovimentoBancario.objects.get(baixa=bx).estornado)
avulso = MovimentoBancario.objects.create(conta=conta, data=DIA, valor=D('-11.11'), descricao='Tarifa')
t('com movimento livre, aparece "ligar"', 'outro movimento' in cs.get('/financeiro/conciliacao/', {'conta': conta.id}).content.decode())
cs.post(f'/financeiro/conciliacao/linha/{l_nada.id}/conciliar/', {'movimento': avulso.id})
cs.post(f'/financeiro/conciliacao/linha/{l_nada.id}/desfazer/')
avulso.refresh_from_db(); l_nada.refresh_from_db()
t('movimento lançado à parte só se solta, não estorna', not avulso.estornado and not avulso.conciliado and not l_nada.movimento_id)

print('Excluir a importação')
URL_E = f'/financeiro/conciliacao/importacao/{imp.id}/excluir/'
h = cs.get('/financeiro/conciliacao/', {'conta': conta.id, 'situacao': 'pendentes'}).content.decode()
t('com linha conciliada, o botão dá lugar ao aviso', 'desfaça para excluir' in h and 'excluir importação' not in h)
r = cs.post(URL_E, follow=True)
t('e o servidor recusa', ExtratoImportacao.objects.filter(pk=imp.id).exists() and 'desfaça a conciliação' in r.content.decode())
t('leitor não exclui', Client().post(URL_E).status_code == 302 and ExtratoImportacao.objects.filter(pk=imp.id).exists())
for l in (l_cnpj, l_cpf, l_entrada):
    cs.post(f'/financeiro/conciliacao/linha/{l.id}/desfazer/')
outro_imp = ExtratoImportacao.objects.create(conta=conta, arquivo_nome='outro.ofx', formato='OFX')
fica = ExtratoLinha.objects.create(conta=conta, importacao=outro_imp, data=DIA, valor=D('1'), identificador='fica')
r = cs.post(URL_E, follow=True)
t('sem conciliada, exclui importação e linhas', not ExtratoImportacao.objects.filter(pk=imp.id).exists() and ExtratoLinha.objects.count() == 1 and 'Extrato excluído: 5' in r.content.decode(), ExtratoLinha.objects.count())
t('outras importações ficam', ExtratoLinha.objects.filter(pk=fica.pk).exists())
t('contas e estornos seguem', Titulo.objects.filter(pk=telefonica.pk, status='ABERTO').exists() and Baixa.objects.filter(titulo=telefonica, estornada=True).exists())
t('fica registrado', LogFinanceiro.objects.filter(acao='EXCLUIR', area='conciliacao').exists())

print(f'\n{ok} OK, {fail} falha(s)')
