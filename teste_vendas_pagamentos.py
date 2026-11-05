"""Vendas: formas de pagamento novas, desconto no Pix e prateleira infinita (05/11/2026). Banco descartável; nada sai para a rede."""
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-vd-pag-'))
(PASTA / 'zz_vd_pag_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-iacomp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_vd_pag_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import json
from decimal import Decimal as D

import requests

from financeiro.cartoes import vendas_no_cartao
from vendas import slv
from vendas.models import ItemPreco, PagamentoVenda, Venda, VendaProduto

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

from users.models import Sector  # noqa: E402

loja = Sector.objects.create(name='Loja ZZ')
chefe = User.objects.create_user(username='zzv.chefe', email='zzv.chefe@exemplo-teste.local', password='S3nha!teste', first_name='Che', last_name='ZZ', hierarchy='SUPERADMIN', is_superuser=True, sector=loja)
cs = Client(); cs.force_login(chefe)
aparelho = ItemPreco.objects.create(categoria='SMARTPHONES', nome='ZZ Phone', valor=D('1000.00'), extra={'PRÉ': '1000'})
capa = ItemPreco.objects.create(categoria='PRODUTOS', nome='ZZ Capa', valor=D('50.00'), cod_sap='ZZCAPA')
fone = ItemPreco.objects.create(categoria='ELETRÔNICOS_LP_Conectados', nome='ZZ Fone', valor=D('300.00'), extra={'CATEGORIA': 'Áudio'})
CPF = '52998224725'
CLIENTE = {'cpf': CPF, 'nome': 'Zz Cliente'}
seq = [0]


def imei():
    seq[0] += 1
    base = f'3599{seq[0]:010d}'
    soma = sum((int(c) * 2 - 9 if int(c) * 2 > 9 else int(c) * 2) if n % 2 else int(c) for n, c in enumerate(base))
    return base + str((10 - soma % 10) % 10)


def vender(pagamentos, produtos=None, **extra):
    dados = {'cliente': CLIENTE, 'produtos': produtos or [{'preco_id': capa.id}], 'pagamentos': pagamentos, **extra}
    r = cs.post('/vendas/nova/', json.dumps(dados), content_type='application/json')
    return r.json()


def erro_com(j, texto):
    return not j.get('ok') and any(texto in e for e in j.get('erros', []))


print('formas de pagamento')
for forma, rotulo in (('DINHEIRO', 'Dinheiro'), ('VIVO_PAY', 'Vivo Pay'), ('AGORACRED', 'Agora Cred'), ('PIX', 'Pix')):
    j = vender([{'forma': forma, 'valor': '50'}])
    v = Venda.objects.filter(pk=j.get('id')).first()
    t(f'{rotulo} grava sem pedir comprovante', j.get('ok') and v.forma_pagamento == forma and v.get_forma_pagamento_display() == rotulo and not v.nsu, j)
j = vender([{'forma': 'DEBITO', 'valor': '50'}])
t('débito exige NSU', erro_com(j, 'informe o NSU'), j)
j = vender([{'forma': 'DEBITO', 'valor': '50', 'nsu': 'ab-1234'}])
v = Venda.objects.get(pk=j['id'])
t('débito grava NSU, sem parcelas', v.forma_pagamento == 'DEBITO' and v.nsu == 'AB1234' and v.parcelas is None and v.pagamentos.get().parcelas is None, j)
t('débito entra na conciliação da maquininha', vendas_no_cartao().filter(pk=v.pk).exists())
j = vender([{'forma': 'CARTAO', 'valor': '50', 'nsu': '1234'}])
t('crédito continua exigindo parcelas', erro_com(j, 'parcelas'), j)
j = vender([{'forma': 'CARTAO', 'valor': '50', 'nsu': '999999', 'parcelas': 3}])
v = Venda.objects.get(pk=j['id'])
t('crédito grava igual', v.forma_pagamento == 'CARTAO' and v.parcelas == 3 and v.get_forma_pagamento_display() == 'Cartão de crédito' and vendas_no_cartao().filter(pk=v.pk).exists())
t('forma inventada é recusada', erro_com(vender([{'forma': 'CHEQUE', 'valor': '50'}]), 'escolha a forma'))
t('sem forma é recusado', erro_com(vender([{'forma': '', 'valor': '50'}]), 'escolha a forma'))
j = vender([{'forma': 'DINHEIRO', 'valor': '20'}, {'forma': 'AGORACRED', 'valor': '10'}, {'forma': 'DEBITO', 'valor': '20', 'nsu': '5555'}])
v = Venda.objects.get(pk=j['id'])
t('várias formas juntas', v.forma_pagamento == 'MISTO' and [p.forma for p in v.pagamentos.all()] == ['DINHEIRO', 'AGORACRED', 'DEBITO'] and v.nsu == '5555', j)
t('soma precisa fechar', erro_com(vender([{'forma': 'DINHEIRO', 'valor': '20'}]), 'falta'))
t('venda antiga, com a forma só, continua aceita', vender([], forma_pagamento='DINHEIRO').get('ok'))
h = cs.get(f'/vendas/{v.pk}/').content.decode()
t('detalhe mostra as formas com ícone', 'Dinheiro' in h and 'Agora Cred' in h and 'fa-money-bill-wave' in h and 'fa-handshake' in h)
h = cs.get('/vendas/nova/').content.decode()
t('formulário oferece as seis formas', all(x in h for x in ("['CARTAO', 'Crédito'", "['DEBITO', 'Débito'", "['PIX', 'Pix'", "['DINHEIRO', 'Dinheiro'", "['VIVO_PAY', 'Vivo Pay'", "['AGORACRED', 'Agora Cred'")))

print('desconto no Pix')
j = vender([{'forma': 'PIX', 'valor': '40'}], desconto_pix='10')
v = Venda.objects.get(pk=j['id'])
p = v.produtos.get()
t('desconta do total e do item', v.desconto_pix == D('10.00') and p.desconto_pix == D('10.00') and p.valor_venda == D('40.00') and v.total == D('40.00'), j)
t('não conta como valor editado à mão', not p.valor_editado and p.valor_calculado == D('50.00'))
t('o cliente paga o valor com desconto', v.pagamentos.get().valor == D('40.00'))
t('pagar o valor cheio com desconto não fecha', erro_com(vender([{'forma': 'PIX', 'valor': '50'}], desconto_pix='10'), 'sobra'))
t('sem Pix não tem desconto', erro_com(vender([{'forma': 'DINHEIRO', 'valor': '40'}], desconto_pix='10'), 'só vale quando parte da venda é paga no Pix'))
t('Vivo Pay não é Pix, para o desconto', erro_com(vender([{'forma': 'VIVO_PAY', 'valor': '40'}], desconto_pix='10'), 'só vale'))
t('desconto do tamanho da venda é recusado', erro_com(vender([{'forma': 'PIX', 'valor': '0.01'}], desconto_pix='50'), 'não pode chegar'))
t('desconto maior que a venda é recusado', erro_com(vender([{'forma': 'PIX', 'valor': '1'}], desconto_pix='80'), 'não pode chegar'))
t('desconto negativo é recusado', erro_com(vender([{'forma': 'PIX', 'valor': '50'}], desconto_pix='-5'), 'inválido'))
t('texto no desconto é recusado', erro_com(vender([{'forma': 'PIX', 'valor': '50'}], desconto_pix='abc'), 'inválido'))
j = vender([{'forma': 'PIX', 'valor': '50'}], desconto_pix='')
t('sem desconto segue normal', j.get('ok') and Venda.objects.get(pk=j['id']).desconto_pix == 0)
j = vender([{'forma': 'PIX', 'valor': '50'}], desconto_pix=0)
t('zero é sem desconto', j.get('ok'))
trio = [{'preco_id': capa.id, 'valor_final': '10,00'}, {'preco_id': capa.id, 'valor_final': '10,00'}, {'preco_id': capa.id, 'valor_final': '10,00'}]
j = vender([{'forma': 'PIX', 'valor': '29'}], trio, desconto_pix='1')
v = Venda.objects.get(pk=j['id'])
partes = sorted(p.desconto_pix for p in v.produtos.all())
t('reparte sem perder centavo', sum(partes) == D('1.00') and partes == [D('0.33'), D('0.33'), D('0.34')] and v.total == D('29.00'), partes)
j = vender([{'forma': 'PIX', 'valor': '80'}, {'forma': 'CARTAO', 'valor': '200', 'nsu': '77777', 'parcelas': 1}],
           [{'preco_id': fone.id, 'imei': 'S1'}], desconto_pix='20')
v = Venda.objects.get(pk=j['id']) if j.get('ok') else None
t('Pix junto com cartão também desconta', v and v.total == D('280.00') and v.desconto_pix == D('20.00'), j)
h = cs.get(f'/vendas/{v.pk}/').content.decode()
t('detalhe mostra o desconto', 'data-desconto-pix' in h and 'Desconto no Pix' in h)
from vendas import views as vv
t('coluna no relatório', 'Desconto no Pix' in vv._PRODUTO_HEADERS)

print('prateleira infinita')
t('aparelho sem IMEI é recusado', erro_com(vender([{'forma': 'PIX', 'valor': '1000'}], [{'preco_id': aparelho.id}]), 'ou marque Prateleira infinita'))
j = vender([{'forma': 'PIX', 'valor': '1000'}], [{'preco_id': aparelho.id, 'prateleira_infinita': True}])
p = VendaProduto.objects.get(venda_id=j['id']) if j.get('ok') else None
t('marcado, passa sem IMEI', p and p.serial == '' and p.prateleira_infinita, j)
t('marcado com IMEI errado continua recusado', erro_com(vender([{'forma': 'PIX', 'valor': '1000'}], [{'preco_id': aparelho.id, 'prateleira_infinita': True, 'imei': '123'}]), 'IMEI'))
um = imei()
j = vender([{'forma': 'PIX', 'valor': '1000'}], [{'preco_id': aparelho.id, 'prateleira_infinita': True, 'imei': um}])
t('marcado com IMEI certo grava', j.get('ok') and VendaProduto.objects.get(venda_id=j['id']).serial == um, j)
t('IMEI repetido continua recusado', erro_com(vender([{'forma': 'PIX', 'valor': '1000'}], [{'preco_id': aparelho.id, 'prateleira_infinita': True, 'imei': um}]), 'já foi vendido'))
j = vender([{'forma': 'PIX', 'valor': '2000'}], [{'preco_id': aparelho.id, 'prateleira_infinita': True}, {'preco_id': aparelho.id, 'prateleira_infinita': True}])
t('dois aparelhos sem IMEI não contam como repetidos', j.get('ok'), j)
t('eletrônico sem serial é recusado', erro_com(vender([{'forma': 'PIX', 'valor': '300'}], [{'preco_id': fone.id}]), 'ou marque Prateleira infinita'))
j = vender([{'forma': 'PIX', 'valor': '300'}], [{'preco_id': fone.id, 'prateleira_infinita': True}])
t('marcado, eletrônico passa sem serial', j.get('ok'), j)
t('capa segue sem serial, sem marcar nada', vender([{'forma': 'PIX', 'valor': '50'}]).get('ok'))

print(f'\n{ok} OK, {fail} falha(s)')
