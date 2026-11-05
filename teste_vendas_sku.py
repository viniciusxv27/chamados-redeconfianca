"""/vendas/precos/importar/ (05/11/2026): SKU na importação e busca por SKU em /vendas/nova/;
popup de "tabela atualizada" em cartões. Banco descartável; WhatsApp nunca é chamado aqui.

Com PLANILHA_REAL=<caminho> no ambiente, importa também a planilha oficial e confere de ponta a ponta.
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-sku-'))
(PASTA / 'zz_sku_settings.py').write_text(f"""
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
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_sku_settings'
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
import os as _os
from decimal import Decimal as D
from io import BytesIO

import openpyxl
import requests

from portal_popups.models import PortalPopup
from users.models import Sector
from vendas import aviso_precos, slv
from vendas.busca import buscar
from vendas.models import ImportacaoPrecos, ItemPreco, VendaProduto
from vendas.services import importar_tabela_precos

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {str(extra)[:600]}')


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


for nome_m in ('get', 'post', 'request'):
    mock.patch.object(requests, nome_m, sem_rede).start()

GRUPOS = ['PRÉ', 'PÓS INDIVIDUAL']


def aba_regular(wb, nome, itens):
    ws = wb.create_sheet(nome)
    cab = []
    for g in GRUPOS:
        cab += [g, 'PIX\ne\nVivo Pay', '2x']
    ws.append([None] * 4 + ['TITULO'])
    ws.append([None] * 4 + ['Vigência', 'TABELA REGULAR'] + [None] * (len(cab) - 1) + ['TABELA RENOVA'])
    ws.append(['PORTFÓLIO', 'CATEGORIA', 'MARCA', 'OFERTA DE COMUNICAÇÃO', 'Nome Comercial'] + cab + ['X'])
    for nome_item, preco in itens:
        ws.append(['In', 'Smartphone', 'Zz', 'O', nome_item] + [preco, preco * 0.9, preco] * 2 + [0])


def planilha(preco=1000, produtos=True, cab_sku='Cod_Sap', extra_linhas=()):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    if produtos:
        ws = wb.create_sheet('PRODUTOS')
        ws.append([cab_sku, 'Descricao_Sap', 'Modelo', 'Cor', 'Nome Comercial', 'Fabricante', 'Categoria', 'Sub', 'ID_DPGC'])
        for linha in [
            ('DGZZ14712000', 'd', 'm', 'Azul', 'ZZ Phone 128GB', 'Zz', 'Smartphone', 's', 'ZZA190'),
            ('DGZZ14722000', 'd', 'm', 'Branco', 'ZZ Phone 128GB', 'Zz', 'Smartphone', 's', 'ZZA190'),
            ('DGZZ14722000', 'd', 'm', 'Branco', 'ZZ Phone 128GB', 'Zz', 'Smartphone', 's', 'ZZA190'),   # repetido
            ('MGZZ05523000', 'd', 'm', 'Preto', 'ZZ Modem', 'Zz', 'Box', 's', 'ZZM119'),
            (22019876, 'd', 'm', 'Vermelho', 'zz capa do phone', 'Zz', 'Essenciais', 's', '-'),          # número e caixa diferente
            (22019877.0, 'd', 'm', '', 'ZZ Cabo', 'Zz', 'Essenciais', 's', None),                        # número em float
            (None, None, None, None, None, None, None, None, None),
            ('', 'd', 'm', 'Azul', 'ZZ Sem Codigo', 'Zz', 'Essenciais', 's', ''),
            ('FFZZ00000000', 'd', 'm', 'Azul', 'ZZ Fora Das Abas', 'Zz', 'Essenciais', 's', ''),
        ]:
            ws.append(list(linha))
        for linha in extra_linhas:
            ws.append(list(linha))
    aba_regular(wb, 'SMARTPHONES', [('ZZ Phone 128GB', preco), ('ZZ Phone Sem SKU', 2000)])
    aba_regular(wb, 'ELETRÔNICOS_LP_Conectados', [('ZZ Modem', 400)])
    ws = wb.create_sheet('ELETRÔNICOS_LP_Não Conectados')
    ws.append([None, None, None, 'TITULO'])
    ws.append([None, None, None, 'Vigência'])
    ws.append(['PORTFÓLIO', 'CATEGORIA', 'MARCA', 'Nome Comercial', 'PVP Base', 'PIX\ne\nVivo Pay'])
    for nome_item, valor in (('ZZ Capa do Phone', 50), ('ZZ Cabo', 40), ('ZZ Sem Codigo', 30)):
        ws.append(['In', 'Essenciais', 'Zz', nome_item, valor, None])
    arquivo = BytesIO()
    wb.save(arquivo)
    arquivo.seek(0)
    return arquivo


print('== IMPORTAÇÃO ==')
r = importar_tabela_precos(planilha())
fone = ItemPreco.objects.get(nome='ZZ Phone 128GB')
t('os SKUs do produto vêm da aba PRODUTOS, um por cor, sem repetir', [(v['sku'], v['cor']) for v in fone.extra['skus']]
  == [('DGZZ14712000', 'Azul'), ('DGZZ14722000', 'Branco')], fone.extra)
t('cod_sap fica com o primeiro; ID DPGC também entra', fone.cod_sap == 'DGZZ14712000' and fone.extra['ID DPGC'] == 'ZZA190')
capa = ItemPreco.objects.get(nome='ZZ Capa do Phone')
t('liga sem olhar maiúscula; SKU numérico vira texto', capa.cod_sap == '22019876' and capa.extra['skus'][0]['cor'] == 'Vermelho', capa.extra)
t('número guardado como float não ganha ".0"', ItemPreco.objects.get(nome='ZZ Cabo').cod_sap == '22019877')
t('"-" no ID DPGC não vira código', 'ID DPGC' not in capa.extra)
t('sem SKU na aba: fica vazio', ItemPreco.objects.get(nome='ZZ Sem Codigo').cod_sap == '')
s = r['sku']
t('o resumo diz quantos e de qual coluna', s['coluna'] == 'Cod_Sap' and s['com_sku'] == 4 and s['sem_sku'] == 2 and s['codigos'] == 5 and not s['problema'], s)
t('e dá exemplos do que ficou sem', set(s['exemplos_sem_sku']) == {'ZZ Phone Sem SKU', 'ZZ Sem Codigo'}, s)
t('SKU não vira produto: quem só está na aba PRODUTOS fica fora', not ItemPreco.objects.filter(nome='ZZ Fora Das Abas').exists())

print('\n== PRIMEIRA CARGA DE SKU NÃO É "PREÇO ALTERADO" ==')
ItemPreco.objects.update(cod_sap='')
for i in ItemPreco.objects.all():
    i.extra = {k: v for k, v in i.extra.items() if k not in ('skus', 'ID DPGC')}
    i.save(update_fields=['extra', 'cod_sap'])
r = importar_tabela_precos(planilha())
t('nenhum preço alterado, SKU gravado', r['alterados'] == 0 and r['sem_mudanca'] == 6 and ItemPreco.objects.get(pk=fone.pk).cod_sap == 'DGZZ14712000', r)
v = r['variacao']
t('o aviso não acusa mudança', not (v['subiram'] or v['diminuiram'] or v['novos'] or v['removidos'] or v['outros']), v)
r = importar_tabela_precos(planilha(preco=1100))
t('preço mudou: alterado, e o SKU continua', r['alterados'] == 1 and ItemPreco.objects.get(pk=fone.pk).cod_sap == 'DGZZ14712000', r)

print('\n== PLANILHA SEM A ABA OU SEM A COLUNA ==')
r = importar_tabela_precos(planilha(preco=1100, produtos=False))
fone.refresh_from_db()
t('sem a aba: importa, avisa e não apaga o SKU', 'não tem a aba PRODUTOS' in r['sku']['problema'] and r['importados'] == 6
  and fone.cod_sap == 'DGZZ14712000' and len(fone.extra['skus']) == 2 and r['alterados'] == 0, r['sku'])
r = importar_tabela_precos(planilha(preco=1200, produtos=False))
fone.refresh_from_db()
t('sem a aba e com preço novo: também não apaga', r['alterados'] == 1 and fone.cod_sap == 'DGZZ14712000' and len(fone.extra['skus']) == 2, fone.extra.keys())
r = importar_tabela_precos(planilha(preco=1200, cab_sku='Outra Coisa'))
t('sem a coluna: importa e avisa', 'coluna do SKU' in r['sku']['problema'] and r['importados'] == 6, r['sku'])
r = importar_tabela_precos(planilha(preco=1200, cab_sku='SKU'))
t('coluna chamada SKU também vale', r['sku']['coluna'] == 'SKU' and r['sku']['com_sku'] == 4, r['sku'])
r = importar_tabela_precos(planilha(preco=1200, extra_linhas=[('DGZZ14732000', 'd', 'm', 'Preto', 'ZZ Phone 128GB', 'Zz', 'S', 's', 'ZZA190')]))
fone.refresh_from_db()
t('cor nova na planilha: o SKU entra sem contar como preço', r['alterados'] == 0 and len(fone.extra['skus']) == 3, r)
t('...e a busca enxerga na hora (o índice se refaz)', buscar('DGZZ14732000') == [fone.pk], buscar('DGZZ14732000'))
importar_tabela_precos(planilha(preco=1200))

print('\n== BUSCA ==')
caches_id = {n: ItemPreco.objects.get(nome=n).pk for n in ('ZZ Phone 128GB', 'ZZ Capa do Phone', 'ZZ Modem')}
t('SKU inteiro acha, em qualquer caixa', buscar('dgzz14722000') == [fone.pk] and buscar('DGZZ14712000') == [fone.pk])
t('SKU numérico acha', buscar('22019876') == [caches_id['ZZ Capa do Phone']], buscar('22019876'))
t('começo do SKU acha', fone.pk in buscar('DGZZ147'))
t('ID DPGC acha', buscar('ZZA190') == [fone.pk], buscar('ZZA190'))
t('nome continua achando', buscar('zz phone 128')[0] == fone.pk)
ItemPreco.objects.create(categoria='SMARTPHONES', nome='ZY Outro 64GB', valor=D('10'), extra={'skus': [{'sku': 'AB128CD99', 'cor': ''}]})
t('pedaço curto não casa no meio do SKU ("128")', all(ItemPreco.objects.get(pk=i).nome != 'ZY Outro 64GB' for i in buscar('128')), buscar('128'))
t('e "128" sozinho continua achando pelo nome', fone.pk in buscar('128'))
ItemPreco.objects.filter(nome='ZY Outro 64GB').delete()

print('\n== /vendas/nova/ ==')
loja = Sector.objects.create(name='Loja ZZ')
chefe = User.objects.create_user(username='zzs.chefe', email='zzs.chefe@exemplo-teste.local', password='S3nha!teste', first_name='Che', last_name='ZZ',
                                 hierarchy='SUPERADMIN', sector=loja, is_superuser=True, is_staff=True)
cs = Client(); cs.force_login(chefe)


def achar(q):
    return cs.get('/vendas/precos/buscar/', {'q': q}).json()['results']


a = achar('DGZZ14722000')
t('digitou o SKU: o produto vem com ele e a cor', len(a) == 1 and a[0]['sku'] == 'DGZZ14722000' and a[0]['cor'] == 'Branco' and a[0]['sku_digitado'], a)
a = achar('ZZ Phone 128')
t('pelo nome com várias cores: ninguém escolhido, mas a lista vem', a[0]['sku'] == '' and a[0]['cor'] == '' and not a[0]['sku_digitado']
  and [v['cor'] for v in a[0]['skus']] == ['Azul', 'Branco'], a[0]['skus'])
a = achar('ZZ Modem')
t('pelo nome com um SKU só: já vem com ele', a[0]['sku'] == 'MGZZ05523000' and a[0]['cor'] == 'Preto', a[0]['sku'])
h = cs.get('/vendas/nova/').content.decode()
t('a tela tem o seletor e o campo no envio', 'data-campo="sku"' in h and "sku: p.sku || ''" in h and 'SKU inteiro: Enter já adiciona' in h)

t('sku_e_cor: pedido de outro produto é ignorado', slv.sku_e_cor(fone, 'MGZZ05523000') == ('', ''))
t('sku_e_cor: sem lista segue o código (cadastro manual)', slv.sku_e_cor(ItemPreco(nome='m', cod_sap='MANUAL', extra={})) == ('MANUAL', ''))

seq = [0]


def imei():
    seq[0] += 1
    base = f'3599{seq[0]:010d}'
    soma = sum((int(c) * 2 - 9 if int(c) * 2 > 9 else int(c) * 2) if n % 2 else int(c) for n, c in enumerate(base))
    return base + str((10 - soma % 10) % 10)


def vender(**p):
    fone_item = {'preco_id': fone.pk, 'imei': imei(), 'grupamento': 'PRÉ', 'condicao': 'Preço', **p}
    d = {'cliente': {'cpf': '52998224725', 'nome': 'Zz Cliente'}, 'produtos': [fone_item],
         'pagamentos': [{'forma': 'Pix', 'valor': '1200.00'}]}
    j = cs.post('/vendas/nova/', json.dumps(d), content_type='application/json').json()
    return j, (VendaProduto.objects.filter(venda_id=j.get('id') or j.get('venda_id')).first() if j.get('ok') else None)


j, vp = vender(sku='DGZZ14722000')
t('a venda grava o SKU e a cor escolhidos', vp and vp.sku == 'DGZZ14722000' and vp.cor == 'Branco', (j, vp and vp.sku))
j, vp = vender()
t('várias cores e nada escolhido: fica sem SKU, não chuta', vp and vp.sku == '' and vp.cor == '', (j, vp and vp.sku))
j, vp = vender(sku='INVENTADO')
t('SKU inventado no envio não é gravado', vp and vp.sku == '', (j, vp and vp.sku))

print('\n== POPUP EM CARTÕES ==')
consultor = User.objects.create_user(username='zzs.cons', email='zzs.cons@exemplo-teste.local', password='S3nha!teste', first_name='Con', last_name='ZZ', hierarchy='PADRAO', sector=loja)
variacao = aviso_precos.calcular_variacao(
    {('S', f'Sobe {n}'): {'Preço': D('1000'), 'PIX': D('900'), 'tabela': None} for n in range(7)}
    | {('S', 'Desce <b>x</b>'): {'Preço': D('2000'), 'PIX': None, 'tabela': None}, ('S', 'Saiu'): {'Preço': D('50'), 'PIX': None, 'tabela': None}},
    {('S', f'Sobe {n}'): {'Preço': D('1100'), 'PIX': D('990'), 'tabela': None} for n in range(7)}
    | {('S', 'Desce <b>x</b>'): {'Preço': D('1500'), 'PIX': None, 'tabela': None}, ('S', 'Novo'): {'Preço': D('10'), 'PIX': None, 'tabela': None}})
imp = ImportacaoPrecos.objects.create(usuario=chefe, arquivo='x.xlsx')
popup = aviso_precos.criar_popup(imp, variacao, por=chefe)
imp.refresh_from_db()
t('o conteúdo fica na importação e é JSON de texto', imp.aviso['contagens'] == {'subiram': 7, 'diminuiram': 1, 'novos': 1, 'removidos': 1}
  and len(imp.aviso['precos'][0]['itens']) == 5 and imp.aviso['precos'][0]['mais'] == 2, imp.aviso)
t('o texto simples continua guardado', 'Maiores altas' in popup.message)
cc = Client(); cc.force_login(consultor)
h = cc.get('/').content.decode()
t('o popup abre em cartões na home', 'data-aviso-precos' in h and 'Subiram' in h and 'R$ 1.100,00' in h and 'line-through' in h, 'portal-popup-modal' in h)
t('nome de produto é escapado', 'Desce &lt;b&gt;x&lt;/b&gt;' in h and 'Desce <b>x</b>' not in h)
t('"e mais 2"', 'e mais 2 produtos' in h)
t('quem não entra em Vendas não vê o link da tabela', 'Ver a tabela completa' not in h)
t('rolagem no corpo e botão Ciente', 'overflow-y-auto min-h-0' in h and 'portal-popup-ack' in h and 'Ciente' in h)
PortalPopup.objects.filter(pk=popup.pk).update(target_hierarchies=['PADRAO', 'SUPERADMIN'])
h = cs.get('/').content.decode()
t('quem entra em Vendas vê o link', 'Ver a tabela completa' in h and '/vendas/precos/' in h, 'data-aviso-precos' in h)

outro = PortalPopup.objects.create(title='Comum', message='Só texto aqui', icon='fas fa-bell', color='indigo', completion_mode=PortalPopup.MODE_ACK,
                                   action_label='Ok', target_all=True, blocking_mode=PortalPopup.BLOCK_NEVER, order=0)
PortalPopup.objects.filter(pk=popup.pk).update(is_active=False)
h = cc.get('/').content.decode()
t('popup de outro assunto segue só com o texto', 'Só texto aqui' in h and 'data-aviso-precos' not in h)
PortalPopup.objects.filter(pk=outro.pk).update(is_active=False)
PortalPopup.objects.filter(pk=popup.pk).update(is_active=True)
with mock.patch('vendas.models.ImportacaoPrecos.objects.filter', side_effect=RuntimeError('fora do ar')):
    h = cc.get('/').content.decode()
t('falha ao ler os cartões cai no texto, e a página abre', 'Maiores altas' in h and 'data-aviso-precos' not in h and 'portal-popup-modal' in h)

print('\n== TELA DA IMPORTAÇÃO ==')
f = planilha(preco=1300); f.name = 'tabela.xlsx'
with mock.patch('core.evolution.enviar_texto', side_effect=AssertionError('não pode enviar')):
    r = cs.post('/vendas/precos/importar/', {'arquivo': f, 'aviso_popup': 'off'}, follow=True)
h = r.content.decode()
t('mostra quantos ficaram com SKU e de onde veio', 'data-resultado-sku' in h and 'coluna Cod_Sap da aba PRODUTOS' in h, h[:300])

real = _os.environ.get('PLANILHA_REAL')
if real:
    print('\n== PLANILHA REAL ==')
    with open(real, 'rb') as arq:
        r = importar_tabela_precos(arq)
    print('    resumo:', {k: r[k] for k in ('importados', 'incluidos', 'alterados', 'removidos', 'inativados')}, 'rejeitados', len(r['rejeitados']))
    print('    sku:', r['sku'])
    t('todo produto ligou a um SKU', r['sku']['sem_sku'] == 0 and r['sku']['com_sku'] == r['importados'], r['sku'])
    amostra = ItemPreco.objects.get(categoria='SMARTPHONES', nome='iPhone 13 128GB')
    print('    iPhone 13 128GB:', amostra.cod_sap, amostra.extra['skus'], amostra.extra.get('ID DPGC'))
    t('SKU real acha o aparelho e a cor', achar('DGAP14712000')[0]['nome'] == 'iPhone 13 128GB' and achar('DGAP14712000')[0]['cor'] == 'Azul', achar('DGAP14712000')[:1])
    t('SKU numérico real acha', achar('22019876')[0]['nome'] == 'Caixa de Som JBL Go 3', [x['nome'] for x in achar('22019876')])
    bruta = [x['nome'] for x in achar('iphone 13 128')]
    print('    "iphone 13 128":', bruta[:5])
    t('busca por nome não piorou', bruta[0] == 'iPhone 13 128GB', bruta[:5])
    t('"128" não traz produto só por causa do SKU', all('128' in x['nome'] or 'gb' in x['nome'].lower() for x in achar('128')[:10]), [x['nome'] for x in achar('128')[:10]])
    import time
    t0 = time.time(); buscar('galaxy s25'); buscar('xyz'); print(f'    busca: {(time.time() - t0) * 500:.1f} ms cada')

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
