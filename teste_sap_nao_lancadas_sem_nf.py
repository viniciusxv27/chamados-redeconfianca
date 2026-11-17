"""/sap/ (17/11/2026): "Lançadas no Vivo GO e não no SAP" e o "Sem NF" na frente dos outros erros.

1) Aba nova ao lado de "Lançado no SAP e não no Vivo GO": itens que o Vivo GO tem e o SAP não
   (vw_conferencia_vendas_produtos, STATUS_LOCALIZACAO = NÃO ENCONTRADO NA SAP), com quantas
   vendas e o valor, nos mesmos filtros da tela (mês, loja). O item que a auditoria já explica
   como serial errado (mesma venda + mesmo SKU ou serial) fica de fora.
2) Venda com vários erros: o "Sem NF" vem SEMPRE na frente — nas abas, no filtro de tipo,
   nas contagens do painel e no chip da lista.

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

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-sap-nao-lancadas-'))
(PASTA / 'zz_sap_nao_lancadas_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sapnl'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sapnl-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_sap_nao_lancadas_settings'
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


# ─────────────────────────────────────────────────────────────────────────────
print('== 1) LANÇADAS NO VIVO GO E NÃO NO SAP: de onde vem ==')
auditoria = [
    # A venda 9000001 tem serial errado no SKU 22023374: o mesmo item no Vivo GO "não acha" o SAP.
    linha_sap(),
    linha_sap(TIPO_ERRO='VENDA NÃO ENCONTRADA NO VIVO GO', ID_VENDA='', SERIAL_VIVOGO='',
              SERIAL_SAP='359999', NUM_FAT_SAP='9113964601', DATA_VENDA=MES_PASSADO),
]
itens = [
    item_vivogo(),                                                         # 7000001 · ICONHA · 219,00
    item_vivogo(SKU='SKU-B', IMEI_ORIGINAL='89551234', IMEI_CONSULTA='89551234',
                NOME_COMERCIAL_PRODUTO='Sim Card', RECEITA_VIVOGO=D('10.00')),   # mesma venda, 2º item
    item_vivogo(ID_VENDA='7000002', PDV='GLÓRIA', IMEI_ORIGINAL='35000000000002',
                IMEI_CONSULTA='35000000000002', RECEITA_VIVOGO=D('1500.00')),    # outra loja
    item_vivogo(ID_VENDA='7000003', DATA_VENDA=MES_PASSADO, IMEI_ORIGINAL='35000000000003',
                IMEI_CONSULTA='35000000000003', RECEITA_VIVOGO=D('80.00')),      # mês passado
    # Já explicadas pela auditoria como serial errado: fora.
    item_vivogo(ID_VENDA='9000001', SKU='22023374', IMEI_ORIGINAL='22023374100356',
                IMEI_CONSULTA='22023374100356'),                                 # mesma venda + SKU
    item_vivogo(ID_VENDA='9000001', SKU='OUTRO', IMEI_ORIGINAL='2202337410035656',
                IMEI_CONSULTA='2202337410035656'),                               # mesma venda + serial
]
pedidos = []


def ler_sem_sap(desde):
    pedidos.append(desde)
    return [dict(i) for i in itens]


with mock.patch.object(mysql, 'ler_vendas_sem_sap', side_effect=ler_sem_sap):
    montadas = linhas_nao_lancadas_no_sap(auditoria)
t('lê a partir da venda mais antiga da auditoria', pedidos == [MES_PASSADO], pedidos)
t('o item já explicado como serial errado (mesma venda + SKU ou serial) fica de fora',
  sorted(m['ID_VENDA'] for m in montadas) == ['7000001', '7000001', '7000002', '7000003'],
  [m['ID_VENDA'] for m in montadas])
t('a linha sai no formato da view, com o tipo novo',
  all(m['TIPO_ERRO'] == TIPO_NAO_LANCADA_NO_SAP for m in montadas))
um = next(m for m in montadas if m['ID_VENDA'] == '7000002')
t('valor do Vivo GO e diferença no sinal da view (SAP − Vivo GO)',
  um['VALOR_VIVO_GO'] == D('1500.00') and um['DIFERENCA_VALOR'] == D('-1500.00'), um)

with mock.patch.object(mysql, '_consultar', return_value=[]) as consulta:
    mysql.ler_vendas_sem_sap(DIA)
sql = consulta.call_args[0][0]
t('a consulta é a da ponte Vivo GO × SAP, só o que o SAP não tem',
  'vw_conferencia_vendas_produtos' in sql and "STATUS_LOCALIZACAO = 'NÃO ENCONTRADO NA SAP'" in sql
  and "ORIGEM_REGISTRO = 'VIVOGO'" in sql and consulta.call_args[0][1] == (DIA,), sql)

print('\n== 1) O ESPELHO ==')
with mock.patch.object(mysql, 'ler_visao_geral', return_value=auditoria), \
        mock.patch.object(mysql, 'ler_vendas_sem_sap', side_effect=ler_sem_sap):
    resumo = sincronizar()
nl = LinhaAuditoria.objects.filter(tipo_erro=TIPO_NAO_LANCADA_NO_SAP)
t('a leitura traz a auditoria e as não lançadas', resumo['novas'] == 6 and nl.count() == 4, resumo)
t('a linha nova guarda a origem na ficha',
  nl.filter(dados__ORIGEM='vw_conferencia_vendas_produtos').count() == 4)

print('\n== 1) A ABA NA TELA ==')
r, html = tela('?grupo=serial')
abas = {g['chave']: g for g in r.context['grupos']}
chaves = [g['chave'] for g in r.context['grupos']]
t('a aba nova fica ao lado de "Lançado no SAP e não no Vivo GO"',
  chaves.index('nao_sap') == chaves.index('vivogo') + 1, chaves)
t('no mês atual: 3 itens, 2 vendas, R$ 1.729,00',
  abas['nao_sap']['n'] == 3 and abas['nao_sap']['vendas'] == 2 and abas['nao_sap']['valor'] == D('1729.00'),
  abas['nao_sap'])
t('o rótulo e o resumo aparecem na tela',
  'Lançadas no Vivo GO e não no SAP' in html and '2 vendas · R$ 1.729,00' in html)
r, html = tela('?grupo=serial&loja=GLÓRIA')
abas = {g['chave']: g for g in r.context['grupos']}
t('respeita a loja do filtro', abas['nao_sap']['n'] == 1 and abas['nao_sap']['valor'] == D('1500.00'),
  abas['nao_sap'])
r, html = tela(f'?grupo=serial&mes={MES_PASSADO:%Y-%m}')
abas = {g['chave']: g for g in r.context['grupos']}
t('respeita o mês do filtro', abas['nao_sap']['n'] == 1 and abas['nao_sap']['valor'] == D('80.00'),
  abas['nao_sap'])
r, html = tela('?grupo=nao_sap')
ids = sorted(l.id_venda for l in r.context['pagina'])
t('clicar na aba lista só as não lançadas do filtro', ids == ['7000001', '7000001', '7000002'], ids)
t('e os números do topo somam o valor no Vivo GO', r.context['resumo']['em_risco'] == D('1729.00'),
  r.context['resumo'])
t('a lista mostra o tipo', 'VENDA NÃO ENCONTRADA NO SAP' in html)

print('\n== 1) LEITURAS SEGUINTES ==')
with mock.patch.object(mysql, 'ler_visao_geral', return_value=auditoria), \
        mock.patch.object(mysql, 'ler_vendas_sem_sap', side_effect=mysql.SapIndisponivel('demorou')):
    sincronizar()
t('se a leitura do Vivo GO falha, as não lançadas continuam ativas',
  nl.filter(ativa=True).count() == 4)
itens.pop(2)          # a 7000002 entrou no SAP
with mock.patch.object(mysql, 'ler_visao_geral', return_value=auditoria), \
        mock.patch.object(mysql, 'ler_vendas_sem_sap', side_effect=ler_sem_sap):
    resumo = sincronizar()
t('a venda que entrou no SAP sai da aba', not nl.get(id_venda='7000002').ativa and resumo['sumiram'] == 1, resumo)

# ─────────────────────────────────────────────────────────────────────────────
print('\n== 2) SEM NF NA FRENTE ==')
LinhaAuditoria.objects.all().delete()


def criar(chave, tipo, id_venda, status_nf='COM NF', ativa=True, **extra):
    return LinhaAuditoria.objects.create(chave=chave, tipo_erro=tipo, id_venda=id_venda, status_nf=status_nf,
                                         ativa=ativa, pdv=extra.pop('pdv', 'ICONHA'), data_venda=DIA, **extra)


serial_sem_nf = criar('zz-a', 'SERIAL ERRADO NO VIVO GO', '8000001', 'SEM NF')          # a própria NF pendente
serial_irma = criar('zz-b', 'SERIAL ERRADO NO VIVO GO', '8000002')                      # COM NF, mas…
irma = criar('zz-c', 'SEM NF', '8000002', 'SEM NF')                                      # …a venda tem SEM NF
serial_puro = criar('zz-d', 'SERIAL ERRADO NO VIVO GO', '8000003')                      # só serial
vivogo_sem_nf = criar('zz-e', 'VENDA NÃO ENCONTRADA NO VIVO GO', '', 'SEM NF')          # SAP sem venda e sem NF
vivogo_puro = criar('zz-f', 'VENDA NÃO ENCONTRADA NO VIVO GO', '')
serial_irma_velha = criar('zz-g', 'SERIAL AUSENTE NO VIVO GO', '8000004')               # a SEM NF já saiu do SAP
criar('zz-h', 'SEM NF', '8000004', 'SEM NF', ativa=False)

principal = dict(com_tipo_principal(LinhaAuditoria.objects.all()).values_list('chave', 'tipo_principal'))
t('serial com a NF da própria linha "SEM NF" vira SEM NF', principal['zz-a'] == 'SEM NF', principal)
t('serial de venda que tem outra linha SEM NF vira SEM NF', principal['zz-b'] == 'SEM NF', principal)
t('venda não encontrada no Vivo GO e sem NF vira SEM NF', principal['zz-e'] == 'SEM NF', principal)
t('o que não tem NF pendente fica como veio',
  principal['zz-d'] == 'SERIAL ERRADO NO VIVO GO' and principal['zz-f'] == 'VENDA NÃO ENCONTRADA NO VIVO GO')
t('SEM NF que já saiu do SAP não puxa a linha ativa da venda',
  principal['zz-g'] == 'SERIAL AUSENTE NO VIVO GO', principal)

aba = lambda g: set(LinhaAuditoria.objects.filter(q_da_tela(g), ativa=True).values_list('chave', flat=True))
t('aba Sem NF', aba('sem_nf') == {'zz-a', 'zz-b', 'zz-c', 'zz-e'}, aba('sem_nf'))
t('aba Serial sem as que foram para Sem NF', aba('serial') == {'zz-d', 'zz-g'}, aba('serial'))
t('aba Vivo GO idem', aba('vivogo') == {'zz-f'}, aba('vivogo'))
t('as abas não se repetem nem perdem linha',
  sum(len(aba(g)) for g in ('serial', 'vivogo', 'nao_sap', 'sem_nf')) == len(aba('')) == 7)

r, html = tela('?grupo=serial')
abas = {g['chave']: g['n'] for g in r.context['grupos']}
t('as contagens das abas seguem a regra', abas == {'serial': 2, 'vivogo': 1, 'nao_sap': 0, 'sem_nf': 4}, abas)
r, html = tela('?grupo=sem_nf')
t('a lista da aba Sem NF traz as 4', len(r.context['pagina']) == 4)
t('o chip mostra SEM NF e avisa o outro erro', 'também: Serial errado no vivo go' in html
  and 'também: Venda não encontrada no vivo go' in html, html.count('também:'))
r, html = tela('?grupo=sem_nf&tipo=SEM NF')
t('filtro de tipo "SEM NF" pega as reclassificadas', len(r.context['pagina']) == 4)
t('o menu de tipo da aba Sem NF só tem SEM NF', list(r.context['tipos']) == ['SEM NF'], list(r.context['tipos']))
r, html = tela('?grupo=serial&tipo=SERIAL ERRADO NO VIVO GO')
t('filtro de tipo serial não traz a que foi para Sem NF',
  [l.chave for l in r.context['pagina']] == ['zz-d'], [l.chave for l in r.context['pagina']])
r, html = tela('?grupo=todos&ordem=tipo')
t('ordenar por tipo usa o erro principal',
  [l.tipo_principal for l in r.context['pagina']][:4] == ['SEM NF'] * 4,
  [l.tipo_principal for l in r.context['pagina']])

r = cliente.get('/sap/painel/')
por_tipo = {p['tipo_principal']: p['abertas'] for p in r.context['por_tipo']}
t('o painel conta por erro principal',
  por_tipo == {'SEM NF': 4, 'SERIAL ERRADO NO VIVO GO': 1, 'SERIAL AUSENTE NO VIVO GO': 1,
               'VENDA NÃO ENCONTRADA NO VIVO GO': 1}, por_tipo)
html = r.content.decode()
t('e o link do tipo abre a lista em "Todos"', 'tipo=SEM%20NF&grupo=todos' in html)

from rotina_adm import servicos as rotina
from users.models import Sector
loja = Sector.objects.create(name='Loja Iconha')
fiscal = rotina.pendencias_fiscais(loja)
t('a Rotina ADM (Sem NF da loja) usa a mesma regra', fiscal['total'] == 4, fiscal)

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
