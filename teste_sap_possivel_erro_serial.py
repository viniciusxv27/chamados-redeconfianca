"""/sap/ (18/11/2026): possível erro de serial sai da Visão SAP — o D-1 concilia.

O item do Vivo GO que "não está no SAP" e a venda do SAP que "não está no Vivo GO" são,
muitas vezes, a mesma venda com o serial digitado errado. A view só casa quando o SKU
também bate; com SKU diferente os dois lados ficavam nas listas. Agora o par (mesmo CPF,
até 3 dias, e mesmo SKU / serial parecido / chip × chip / mesmo valor) sai das duas abas,
um para um, e a tela diz quantos saíram. Sem NF continua aparecendo (a nota é outro problema).

Banco descartável (sqlite, syncdb) e MySQL do SAP dublê: nada sai daqui.
"""
import os
import pathlib
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-sap-serial-par-'))
(PASTA / 'zz_sap_serial_par_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sapsp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sapsp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_sap_serial_par_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import requests
from django.contrib.auth import get_user_model
from django.utils import timezone

from auditoria_sap import espelho, mysql
from auditoria_sap import renova as sap_renova
from auditoria_sap.espelho import linhas_nao_lancadas_no_sap, sincronizar
from auditoria_sap.models import (TIPO_NAO_LANCADA_NO_SAP, LinhaAuditoria, com_tipo_principal, q_da_tela)

User = get_user_model()
D = Decimal
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
# Nenhuma conexão com o MySQL do SAP: o que não for dublê explode aqui.
mock.patch.object(mysql, 'conexao', side_effect=AssertionError('teste tentou ler o MySQL do SAP')).start()
mock.patch.object(mysql, 'ler_formas_de_pagamento', return_value={}).start()
mock.patch.object(sap_renova, 'ler_do_sap', return_value={}).start()

HOJE = timezone.localdate()
MES_PASSADO = HOJE.replace(day=1) - timedelta(days=1)
DIA = HOJE.replace(day=1)          # um dia do mês corrente que nunca é "futuro"


def linha_sap(**troca):
    """Uma linha da vw_auditoria_visao_geral, como o MySQL devolve."""
    base = {
        'TIPO_ERRO': 'SERIAL ERRADO NO VIVO GO', 'DATA_VENDA': DIA, 'PDV': 'ICONHA', 'LOJA_VIVOGO': 'ICONHA',
        'ID_VENDA': '9000001', 'NOME_CLIENTE': 'CLIENTE ZZ', 'DOCUMENTO_VIVOGO': '085.114.656-26',
        'DOCUMENTO_SAP': '085.114.656-26', 'PRODUTO_VIVOGO': 'Película', 'PRODUTO_SAP': 'PELICULA',
        'SKU_VIVOGO': '22023374', 'COD_MATERIAL_SAP': '22023374',
        'SERIAL_VIVOGO': '2202337410035656', 'SERIAL_SAP': '220233741035656',
        'ORDEM_SAP': 'PADR', 'NUM_FAT_SAP': '9113964600', 'STATUS_NF': 'COM NF',
        'VALOR_SAP': D('89.00'), 'VALOR_VIVO_GO': D('89.00'), 'DIFERENCA_VALOR': D('0.00'),
        'STATUS_DOCUMENTO': 'OK', 'STATUS_PRODUTO': 'OK', 'STATUS_VALOR': 'OK', 'STATUS_LOJA': 'OK',
        'STATUS_DATA': 'OK', 'DIFERENCA_EXPLICITA': 'SERIAL ERRADO NO VIVO GO',
    }
    base.update(troca)
    return base


def item_vivogo(**troca):
    """Uma linha da vw_conferencia_vendas_produtos (Vivo GO sem o serial no SAP)."""
    base = {
        'PDV': 'ICONHA', 'DATA_VENDA': DIA, 'ID_VENDA': '7000001', 'NOME_CLIENTE': 'COMPRADOR ZZ',
        'CPF_CLIENTE_VIVOGO': '111.222.333-44', 'NOME_COMERCIAL_PRODUTO': 'Carregador 20W',
        'TIPO_PRODUTO': 'Produto Vivo - Estoque Avançado', 'SKU': 'SKU-A',
        'IMEI_ORIGINAL': '35000000000001', 'IMEI_CONSULTA': '35000000000001',
        'RECEITA_VIVOGO': D('219.00'), 'QTD_VENDIDA': 1, 'VENDEDOR': 'VENDEDOR ZZ',
    }
    base.update(troca)
    return base


chefe = User.objects.create_user(username='zzsap.chefe', email='zzsap.chefe@exemplo-teste.local',
                                 password='S3nha!teste', first_name='Chefe', last_name='ZZ',
                                 hierarchy='SUPERADMIN', is_superuser=True)
cliente = Client()
cliente.force_login(chefe)


def tela(q):
    r = cliente.get('/sap/' + q)
    return r, r.content.decode()



from auditoria_sap.models import MARCA_POSSIVEL_SERIAL

ONTEM = DIA + timedelta(days=1) if DIA + timedelta(days=1) <= HOJE else DIA


def sap(cpf, cod, serial, nf, valor, **troca):
    return linha_sap(TIPO_ERRO='VENDA NÃO ENCONTRADA NO VIVO GO', ID_VENDA='', DOCUMENTO_VIVOGO='',
                     SERIAL_VIVOGO='', SKU_VIVOGO='', PRODUTO_VIVOGO='', DOCUMENTO_SAP=cpf,
                     COD_MATERIAL_SAP=cod, SERIAL_SAP=serial, NUM_FAT_SAP=nf, VALOR_SAP=D(valor),
                     VALOR_VIVO_GO=None, DIFERENCA_VALOR=None, **troca)


def vg(venda, cpf, sku, serial, valor, **troca):
    return item_vivogo(ID_VENDA=venda, CPF_CLIENTE_VIVOGO=cpf, SKU=sku, IMEI_ORIGINAL=serial,
                       IMEI_CONSULTA=serial, RECEITA_VIVOGO=D(valor), **troca)


auditoria = [
    # 1) Galaxy: SKU de cor diferente (B2000 × B4000) e um dígito a mais no serial.
    sap('111.111.111-11', 'TGSA609B4000', '358852341590844', 'NF-GALAXY', '2498.87'),
    # 2) chip × chip do mesmo cliente, ICCIDs diferentes.
    sap('222.222.222-22', 'YBSC001J4000', '89551132269005843386', 'NF-CHIP', '13.00'),
    # 3) mesmo SKU, um dia depois.
    sap('333.333.333-33', '22023388', '220233880107102', 'NF-SKU', '69.00', DATA_VENDA=ONTEM),
    # 4) serial parecido, mas OUTRO cliente: não é par.
    sap('444.444.444-44', '22022764', '220227640000268', 'NF-OUTRO-CLIENTE', '44.11'),
    # 5) mesmo cliente, 10 dias antes: não é par.
    sap('555.555.555-55', 'TGMO61852000', '354332912808853', 'NF-LONGE', '1229.00', DATA_VENDA=DIA - timedelta(days=10)),
    # 6) mesmo cliente e dia, produto e valor diferentes: não é par.
    sap('666.666.666-66', '22022526', '22022526383968', 'NF-OUTRO-PRODUTO', '218.99'),
    # 7) um chip no SAP para dois chips no Vivo GO: só um par.
    sap('777.777.777-77', 'YBSC001J4000', '89551132369003622350', 'NF-UM-CHIP', '13.00'),
    # 8) par com a nota pendente: continua em Sem NF.
    sap('888.888.888-88', 'TGSA58664000', '357884918083078', 'NF-SEM-NF', '989.07', STATUS_NF='SEM NF'),
]
itens = [
    vg('V-GALAXY', '111.111.111-11', 'TGSA609B2000', '3588523141590844', '2498.87'),
    vg('V-CHIP', '222.222.222-22', '', '89551132369005843386', '13.00'),
    vg('V-SKU', '333.333.333-33', '22023388', '220233880107101', '69.00'),
    vg('V-OUTRO-CLIENTE', '999.999.999-99', '22020801', '2202276440000268', '44.10'),
    vg('V-LONGE', '555.555.555-55', 'TGMO61852000', '324332912808853', '1229.00'),
    vg('V-OUTRO-PRODUTO', '666.666.666-66', '22023263', '220232630107366', '89.00'),
    vg('V-CHIP-A', '777.777.777-77', '', '89551132369003622351', '13.00'),
    vg('V-CHIP-B', '777.777.777-77', '', '89551132469001234404', '13.00'),
    vg('V-SEM-NF', '888.888.888-88', 'TGSA58662000', '357884618083078', '1099.07'),
]

print('== 1) O PAR NA LEITURA DO SAP ==')
with mock.patch.object(mysql, 'ler_visao_geral', return_value=[dict(b) for b in auditoria]), \
        mock.patch.object(mysql, 'ler_vendas_sem_sap', return_value=[dict(i) for i in itens]):
    sincronizar()

def marcada(**f):
    l = LinhaAuditoria.objects.filter(ativa=True, **f).first()
    return l is not None and MARCA_POSSIVEL_SERIAL in (l.dados or {}), l

for venda, nf, nome in (('V-GALAXY', 'NF-GALAXY', 'serial com um dígito a mais e SKU de outra cor'),
                        ('V-CHIP', 'NF-CHIP', 'chip × chip do mesmo cliente'),
                        ('V-SKU', 'NF-SKU', 'mesmo SKU, um dia depois')):
    a, la = marcada(id_venda=venda, tipo_erro=TIPO_NAO_LANCADA_NO_SAP)
    b, lb = marcada(num_fat_sap=nf)
    t(f'par: {nome}', a and b, (a, b))
t('a marca diz o outro lado do par', 'NF-GALAXY' in marcada(id_venda='V-GALAXY')[1].dados[MARCA_POSSIVEL_SERIAL]
  and 'V-GALAXY' in marcada(num_fat_sap='NF-GALAXY')[1].dados[MARCA_POSSIVEL_SERIAL])
for venda, nf, nome in (('V-OUTRO-CLIENTE', 'NF-OUTRO-CLIENTE', 'outro cliente'),
                        ('V-LONGE', 'NF-LONGE', '10 dias de distância'),
                        ('V-OUTRO-PRODUTO', 'NF-OUTRO-PRODUTO', 'outro produto e outro valor')):
    t(f'não é par: {nome}', not marcada(id_venda=venda)[0] and not marcada(num_fat_sap=nf)[0])
chips = [marcada(id_venda=v)[0] for v in ('V-CHIP-A', 'V-CHIP-B')]
t('um chip no SAP explica só um dos dois chips do Vivo GO (o de serial mais parecido)',
  chips == [True, False] and marcada(num_fat_sap='NF-UM-CHIP')[0], chips)

print('\n== 2) NA TELA ==')
def na_aba(grupo):
    r = cliente.get(f'/sap/?grupo={grupo}&mes=todos&situacao=todas')
    linhas = list(r.context['pagina'].paginator.object_list)
    return r, {l.id_venda for l in linhas} | {l.num_fat_sap for l in linhas}

r, nao_sap = na_aba('nao_sap')
t('a aba "Lançadas no Vivo GO e não no SAP" não mostra os pares',
  not {'V-GALAXY', 'V-CHIP', 'V-SKU', 'V-CHIP-A'} & nao_sap, nao_sap)
t('e continua mostrando o resto', {'V-OUTRO-CLIENTE', 'V-LONGE', 'V-OUTRO-PRODUTO', 'V-CHIP-B'} <= nao_sap, nao_sap)
r, vivogo = na_aba('vivogo')
t('a aba "Lançado no SAP e não no Vivo GO" não mostra os pares',
  not {'NF-GALAXY', 'NF-CHIP', 'NF-SKU', 'NF-UM-CHIP'} & vivogo, vivogo)
t('e continua mostrando o resto', {'NF-OUTRO-CLIENTE', 'NF-LONGE', 'NF-OUTRO-PRODUTO'} <= vivogo, vivogo)
grupos = {g['chave']: g['n'] for g in r.context['grupos']}
t('as contagens das abas já vêm sem os pares', grupos.get('nao_sap') == 4 and grupos.get('vivogo') == 3, grupos)
r, sem_nf = na_aba('sem_nf')
t('par sem NF: o lado do SAP continua em "Sem NF" (a nota é outro problema)', 'NF-SEM-NF' in sem_nf, sem_nf)
t('e o lado do Vivo GO sai como os outros', 'V-SEM-NF' not in sem_nf and 'V-SEM-NF' not in nao_sap)
html = r.content.decode()
# 4 pares inteiros + o lado Vivo GO do par sem NF.
t('a tela diz quantas linhas saíram e por quê', r.context['serial_tirados'] == 9
  and 'possível erro de serial' in html and 'o D-1 concilia' in html, r.context['serial_tirados'])
r = cliente.get('/sap/painel/?mes=todos')
t('o painel abre', r.status_code == 200, r.status_code)

print('\n== 3) LEITURAS SEGUINTES ==')
# O SAP corrigiu a venda do Galaxy (a linha sumiu): o item do Vivo GO volta para a lista.
sem_galaxy = [dict(b) for b in auditoria if b['NUM_FAT_SAP'] != 'NF-GALAXY']
with mock.patch.object(mysql, 'ler_visao_geral', return_value=sem_galaxy), \
        mock.patch.object(mysql, 'ler_vendas_sem_sap', return_value=[dict(i) for i in itens]):
    sincronizar()
t('sem o outro lado, o item volta a aparecer', not marcada(id_venda='V-GALAXY')[0])
# A leitura do Vivo GO falhou: a marca do lado SAP fica como estava (não pisca na tela).
with mock.patch.object(mysql, 'ler_visao_geral', return_value=sem_galaxy), \
        mock.patch.object(mysql, 'ler_vendas_sem_sap', side_effect=mysql.SapIndisponivel('fora do ar')):
    sincronizar()
t('Vivo GO fora do ar: o lado SAP continua marcado', marcada(num_fat_sap='NF-CHIP')[0]
  and marcada(id_venda='V-CHIP')[0])

print('\n== 4) 2 DE 3 INDÍCIOS DA QUERY (19/11/2026) ==')
# A query viu a venda no Vivo GO, mas sem os 3 indícios: chama de "não encontrada".
indicios = [
    # mesmo SKU e dia na loja, CPF diferente: o item do Vivo GO que sobrou sai junto.
    sap('121.212.121-21', '22023386', '220233860409970', 'NF-IND-PAR', '69.00', PDV='EMBU DAS ARTES',
        PONTUACAO_INDICIOS='2', INDICIOS_LOCALIZACAO='CPF=NÃO | SKU=OK | DATA=OK | VALOR=OK'),
    # 2 indícios e nenhum item sobrando no Vivo GO: sai sozinha.
    sap('131.313.131-31', 'TGSA61564000', '356599706085561', 'NF-IND-SO', '1564.04', PDV='ITACIBA',
        PONTUACAO_INDICIOS='2', INDICIOS_LOCALIZACAO='CPF=OK | SKU=NÃO | DATA=OK | VALOR=OK'),
    # 1 indício só: continua na lista.
    sap('141.414.141-41', '22025568', '22025568102717', 'NF-IND-UM', '3999.00', PDV='JACARAÍPE',
        PONTUACAO_INDICIOS='1', INDICIOS_LOCALIZACAO='CPF=OK | SKU=NÃO | DATA=NÃO | VALOR=NÃO'),
]
itens_ind = [vg('V-IND-PAR', '989.898.989-89', '22023386', '220233860490970', '69.00', PDV='EMBU DAS ARTES')]
with mock.patch.object(mysql, 'ler_visao_geral', return_value=[dict(b) for b in sem_galaxy + indicios]), \
        mock.patch.object(mysql, 'ler_vendas_sem_sap', return_value=[dict(i) for i in itens + itens_ind]):
    sincronizar()
a_, l = marcada(num_fat_sap='NF-IND-PAR')
t('2 de 3 indícios: a linha do SAP sai', a_ and '2 de 3 indícios' in l.dados[MARCA_POSSIVEL_SERIAL], l and l.dados.get(MARCA_POSSIVEL_SERIAL))
t('e o item do Vivo GO da mesma loja/dia/SKU sai junto', marcada(id_venda='V-IND-PAR')[0])
t('2 de 3 sem item do Vivo GO sobrando: sai sozinha', marcada(num_fat_sap='NF-IND-SO')[0])
t('1 indício só: continua na lista', not marcada(num_fat_sap='NF-IND-UM')[0])
r, vivogo = na_aba('vivogo')
t('a aba mostra a de 1 indício e não as de 2', 'NF-IND-UM' in vivogo and not {'NF-IND-PAR', 'NF-IND-SO'} & vivogo, vivogo)
t('os pares de antes continuam marcados', marcada(num_fat_sap='NF-CHIP')[0] and marcada(id_venda='V-SKU')[0])

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
