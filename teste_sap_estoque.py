"""/sap/estoque/ (23/11/2026): a visão de estoque — disponível, em trânsito, reposição,
detalhado e produtos, com a distância entre lojas.

Pedido: "criar uma visão de estoque, use como referência de funcionalidade o Power BI
(Consulta de Estoque), porém o layout precisa ser intuitivo ao máximo [...] interprete a
distância entre lojas".

Cobre: a leitura das cargas (só a última, linha de total da MB52 fora, trânsito pelas
linhas sem depósito, data do Excel), as vizinhas por distância, a matriz, "mais perto com
saldo", a reposição (crítico/ruptura/insuficiente, transferência só até 200 km), as telas,
a gaveta de seriais, a busca por serial, o CSV, quem entra e o SAP fora do ar.

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

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-sap-estoque-'))
(PASTA / 'zz_sap_estoque_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sapest'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-sapest-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_sap_estoque_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import json
import re
import subprocess
from datetime import date, timedelta

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from auditoria_sap import estoque as est
from auditoria_sap import mysql
from communications.models import CommunicationGroup
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
mock.patch.object(mysql, 'conexao', side_effect=AssertionError('teste tentou ler o MySQL do SAP')).start()

IQ, MB = 'iq09_20261123_175754', 'mb52_20261123_175858'
EXCEL_HA_10_DIAS = str((date.today() - timedelta(days=10) - date(1899, 12, 30)).days)
EXCEL_HA_120_DIAS = str((date.today() - timedelta(days=120) - date(1899, 12, 30)).days)


def mb(centro, dep, material, livre=0, transito=0, texto=''):
    return {'centro': centro, 'nome_1': f'EA MASTERCEL {centro}', 'deposito': dep, 'material': material,
            'texto_breve_material': texto or f'DESC SAP {material}', 'utilizacao_livre': str(livre),
            'transito_e_te': str(transito), 'bloqueado': '0', 'em_contr_qualidade': '0'}


def iq(material, serial, centro, dep, tipo='01', quando=EXCEL_HA_10_DIAS, status='DEPS'):
    return {'material': material, 'denominacao': f'DESC SAP {material}', 'n_de_serie': serial, 'centro': centro,
            'deposito': dep, 'tipo_de_estoque': tipo, 'status_sistema': status, 'status_usuario': None,
            'modificado_em': quando}


GALAXY, PELICULA, CARREGADOR, BOOMBOX, TV = 'TGSA609C2000', '22023386', '22019815', '22025654', '22099999'
MB52 = [
    {'centro': None, 'nome_1': None, 'deposito': None, 'material': None, 'texto_breve_material': None,
     'utilizacao_livre': '999', 'transito_e_te': '999', 'bloqueado': '0', 'em_contr_qualidade': '0'},
    mb('064E', 'LVUT', GALAXY, livre=2), mb('220L', 'LVUT', GALAXY, livre=5),
    mb('057E', None, GALAXY, transito=1),
    mb('057E', 'LVUT', PELICULA, livre=10),
    mb('057E', 'LVUT', CARREGADOR, livre=1), mb('057E', 'RPAR', CARREGADOR, livre=1),
    mb('045E', 'LVUT', CARREGADOR, livre=3), mb('220L', 'LVUT', CARREGADOR, livre=10),
    mb('045E', 'EXPO', TV, livre=1),
]
IQ09 = [
    iq(GALAXY, '350000000000001', '064E', 'LVUT', quando=EXCEL_HA_120_DIAS),
    iq(GALAXY, '350000000000002', '064E', 'LVUT'),
    *[iq(GALAXY, f'35000000000010{i}', '220L', 'LVUT') for i in range(5)],
    iq(GALAXY, '350000000000099', '057E', None, tipo='06', status='DEPS NREM'),
    iq(CARREGADOR, 'SC4H00000001', '057E', 'LVUT'),
    iq(CARREGADOR, 'SC4H00000002', '057E', 'RPAR'),
    iq(TV, 'TV000000000001', '045E', 'EXPO'),
]
CATALOGO = [
    {'cod_sap': GALAXY, 'cor': 'Azul', 'nome_comercial': 'Samsung Galaxy A57 5G 256GB', 'fabricante': 'Samsung',
     'categoria': 'Smartphones', 'subcategoria': 'Android', 'gama': 'Média'},
    {'cod_sap': PELICULA, 'cor': '', 'nome_comercial': 'Película Máquina Transparente Ovvi GS',
     'fabricante': 'Ovvi', 'categoria': 'Essenciais', 'subcategoria': 'Películas', 'gama': None},
    {'cod_sap': CARREGADOR, 'cor': 'Branco', 'nome_comercial': 'Carregador USB-C de 20W', 'fabricante': 'Geonav',
     'categoria': 'Essenciais', 'subcategoria': 'Carregadores', 'gama': None},
    # 24/11: a mesma categoria/fabricante com outra grafia (vinha duplicada no filtro).
    {'cod_sap': '22000001', 'cor': '', 'nome_comercial': 'Cabo ZZ', 'fabricante': 'GEONAV',
     'categoria': 'essenciais', 'subcategoria': 'Cabos', 'gama': None},
    {'cod_sap': '22000002', 'cor': '', 'nome_comercial': 'Roteador ZZ', 'fabricante': 'TP-Link',
     'categoria': 'Demais Eletrônicos', 'subcategoria': 'Áudio e Vídeo', 'gama': None},
    {'cod_sap': '22000003', 'cor': '', 'nome_comercial': 'Repetidor ZZ', 'fabricante': 'TP Link',
     'categoria': 'Demais eletrônicos', 'subcategoria': 'Áudio e vídeo', 'gama': None},
    {'cod_sap': '22000004', 'cor': '', 'nome_comercial': 'Extensor ZZ', 'fabricante': 'TP Link',
     'categoria': 'Demais Eletrônicos', 'subcategoria': 'Áudio e Vídeo', 'gama': None},
]
RENOVA = [
    {'fabricante': 'Samsung', 'modelo': 'Galaxy A57 5G 256GB', 'nome_comercial': 'Samsung Galaxy A57 5G 256GB',
     'desconto': '300.00', 'inicio': date.today() - timedelta(days=10), 'fim': date.today() + timedelta(days=6)},
]
VENDAS = [
    {'PDV': 'ANCHIETA', 'SKU': GALAXY, 'd30': 7, 'd90': 15, 'nome': 'Samsung Galaxy A57 5G 256GB'},
    {'PDV': 'ANCHIETA', 'SKU': PELICULA, 'd30': 4, 'd90': 9, 'nome': 'Película'},
    {'PDV': 'ANCHIETA', 'SKU': CARREGADOR, 'd30': 3, 'd90': 5, 'nome': 'Carregador'},
    {'PDV': 'ANCHIETA', 'SKU': BOOMBOX, 'd30': 3, 'd90': 3, 'nome': 'Caixa de Som JBL Boombox 4'},
    {'PDV': 'SEDE ADMINISTRATIVA', 'SKU': GALAXY, 'd30': 1, 'd90': 1, 'nome': 'x'},
]
leituras = []


def dublê(sql, parametros=()):
    leituras.append(sql)
    if 'MAX(execucao_id)' in sql:
        return [{'iq09': IQ, 'mb52': MB}]
    if 'FROM estoque_mb52' in sql:
        assert parametros == (MB,), parametros
        return [dict(r) for r in MB52]
    if 'FROM estoque_iq09' in sql:
        assert parametros == (IQ,), parametros
        return [dict(r) for r in IQ09]
    if 'produtos_nome_comercial' in sql:
        return [dict(r) for r in CATALOGO]
    if 'vw_vivogo_produtos_historico' in sql:
        return [dict(r) for r in VENDAS]
    if 'descontos_fabricante' in sql:
        hoje = parametros[0]
        return [dict(r) for r in RENOVA if r['inicio'] <= hoje <= r['fim']]
    raise AssertionError(sql)


mock.patch.object(mysql, '_consultar', side_effect=dublê).start()

print('== 1) A LEITURA DA CARGA ==')
foto = est.fotografia(forcar=True)
t('só a última carga, nas duas tabelas', foto['iq09'] == IQ and foto['mb52'] == MB)
t('a linha de total da MB52 fica de fora', all(c for (c, _m) in foto['saldos']) and 999 not in foto['transito'].values())
t('trânsito vem das linhas sem depósito', foto['transito'] == {('057E', GALAXY): 1.0}, foto['transito'])
t('livre por depósito', est.livre_para_venda(foto, '057E', CARREGADOR) == 1
  and foto['saldos'][('057E', CARREGADOR)]['RPAR'][0] == 1)
t('data da carga', foto['quando_mb52'] and foto['quando_mb52'].strftime('%d/%m %H:%M') == '23/11 17:58')
u = est.unidades_de(foto, '064E', GALAXY)
t('data do Excel vira dia e dias parados', u[0]['dias'] == 120 and u[1]['dias'] == 10, [x['dias'] for x in u])
t('vendas por centro (PDV do Vivo GO → centro), sem a sede', foto['vendas'][('057E', GALAXY)] == [7, 15]
  and not any(c == '' for c, _m in foto['vendas']))
t('nome comercial; sem cadastro usa o nome da venda', est.produto(foto, GALAXY)['nome'] == 'Samsung Galaxy A57 5G 256GB'
  and est.produto(foto, BOOMBOX)['nome'] == 'Caixa de Som JBL Boombox 4')
t('a segunda vez não lê o SAP de novo', (lambda n: (est.fotografia(), len(leituras) == n)[1])(len(leituras)))

print('\n== 2) DISTÂNCIA ENTRE LOJAS ==')
t('loja do portal → centro', est.centro_da_loja('Loja Anchieta') == '057E' and est.centro_da_loja('Loja Glória') == '065E'
  and est.centro_da_loja('Loja Itacibá') == '060E' and est.centro_da_loja('057E') == '057E')
km = est.distancia_km(est.POR_CENTRO['057E'], est.POR_CENTRO['064E'])
t('Anchieta → Piúma ~9 km', 5 < km < 15, km)
cols = est.colunas_da_matriz(foto, '057E')
t('matriz: a loja escolhida primeiro e as outras da mais perto para a mais longe',
  [c['centro'] for c in cols] == ['057E', '064E', '045E', '220L'], [c['centro'] for c in cols])
t('a de SP fica longe (centenas de km)', cols[-1]['km'] > 600, cols[-1]['km'])
t('todas as lojas da rede têm posição', all(c in est.POR_CENTRO for c in est.centros_da_foto(foto)))

print('\n== 3) MATRIZ ==')
linhas = {l['produto']['material']: l for l in est.matriz(foto, cols)}
t('Galaxy: 0 na minha, 2 em Piúma, 5 em SP', linhas[GALAXY]['qtds'] == [0, 2, 0, 5], linhas[GALAXY]['qtds'])
t('exposição não conta como livre para venda', TV not in linhas)
perto = est.mais_perto_com_saldo(foto, '057E', GALAXY)
t('mais perto com saldo: Piúma', perto and perto['centro'] == '064E' and perto['qtd'] == 2, perto)
trans = {l['produto']['material']: l for l in est.matriz(foto, cols, modo='transito')}
t('em trânsito: o Galaxy chegando em Anchieta', list(trans) == [GALAXY] and trans[GALAXY]['qtds'][0] == 1)
t('busca por nome sem acento', [l['produto']['material'] for l in est.matriz(foto, cols, busca='pelicula')] == [PELICULA])

print('\n== 4) REPOSIÇÃO ==')
rep = {l['produto']['material']: l for l in est.reposicao(foto, '057E')}
g = rep[GALAXY]
t('Galaxy zerado com 7 vendas: crítico, sugestão 6 (já tem 1 chegando)', g['situacao'] == 'critico'
  and g['estoque'] == 1 and g['sugestao'] == 6, (g['situacao'], g['estoque'], g['sugestao']))
t('transferir de Piúma (mais perto com sobra)', g['buscar_em'] and g['buscar_em']['centro'] == '064E'
  and g['buscar_em']['sobra'] == 2, g['buscar_em'])
c = rep[CARREGADOR]
t('carregador: 1 livre para 3 vendas = insuficiente, sugestão 2', c['situacao'] == 'insuficiente' and c['sugestao'] == 2)
t('transferência de Iconha (17 km), nunca de SP mesmo com 10 sobrando', c['buscar_em'] and c['buscar_em']['centro'] == '045E')
t('película coberta', rep[PELICULA]['situacao'] == 'ok' and rep[PELICULA]['sugestao'] == 0)
t('vendido sem estoque nenhum: ruptura e "pedir"', rep[BOOMBOX]['situacao'] == 'ruptura' and rep[BOOMBOX]['buscar_em'] is None)
t('ordem: crítico primeiro', est.reposicao(foto, '057E')[0]['situacao'] == 'critico')
t('filtro de situação', [l['produto']['material'] for l in est.reposicao(foto, '057E', status='ruptura')] == [BOOMBOX])

print('\n== 5) A TELA ==')
loja = Sector.objects.create(name='Loja Anchieta')
outra = Sector.objects.create(name='Loja Piúma')
chefe = User.objects.create_user(username='zzest.chefe', email='zzest.chefe@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Chefe', last_name='ZZ', hierarchy='SUPERADMIN', is_superuser=True, sector=loja)
grupo, _ = CommunicationGroup.objects.get_or_create(name='GERENTES', defaults={'created_by': chefe})
gerente = User.objects.create_user(username='zzest.gerente', email='zzest.ger@exemplo-teste.local', password='S3nha!teste',
                                   first_name='Gerente', last_name='Piúma', hierarchy='PADRAO', sector=outra)
gerente.communication_groups.add(grupo)
vendedor = User.objects.create_user(username='zzest.cn', email='zzest.cn@exemplo-teste.local', password='S3nha!teste',
                                    first_name='Vendedor', last_name='ZZ', hierarchy='PADRAO', sector=loja)
cc, cg, cv = Client(), Client(), Client()
cc.force_login(chefe); cg.force_login(gerente); cv.force_login(vendedor)

r = cc.get('/sap/estoque/?aba=disponivel')
html = r.content.decode()
t('abre', r.status_code == 200, r.status_code)
t('abre na loja de quem entra (Anchieta)', r.context['f']['loja'] == '057E')
t('números do topo da loja', r.context['resumo'] == {'livre': 11, 'transito': 1, 'ruptura': 2, 'criticos': 1, 'sugestao': 11},
  r.context['resumo'])
t('cabeçalho: lojas com km e a data do SAP', 'Piúma' in html and ' km' in html and '23/11' in html)
t('linha zerada mostra onde buscar', 'Mais perto com saldo' in html and 'sem saldo na rede' not in html.split('Samsung Galaxy A57')[1][:2000] if 'Samsung Galaxy A57' in html else False)
t('aba Estoque no topo do /sap/', '/sap/estoque/' in html and 'fa-warehouse' in html)
for aba in ('transito', 'reposicao', 'detalhado', 'produtos'):
    r = cc.get(f'/sap/estoque/?aba={aba}&loja=057E')
    t(f'aba {aba} abre', r.status_code == 200, r.status_code)
r = cc.get('/sap/estoque/?aba=reposicao&loja=057E')
t('reposição mostra "Transferir de" Piúma', 'Transferir de' in r.content.decode() and 'Piúma' in r.content.decode())
html_rep = r.content.decode()
t('reposição: Vendas 30d, 90d e Sugestão ordenáveis (clique no cabeçalho)',
  all(re.search(r'data-ordenar="numero">' + nome, html_rep) for nome in ('Vendas 30d', '90d', 'Sugestão', 'Livre')))
t('botão de copiar ao lado do material', f'data-copiar="{GALAXY}"' in html_rep)
t('a tabela rola dentro do cartão, sem o embrulho global do portal (overflow-auto)',
  'class="est-rolagem overflow-auto"' in html_rep)
t('barra de rolagem de lado no topo, setas, "ir para a loja" e modo compacto',
  'id="estBarraTopo"' in html_rep and 'data-rolar="1"' in html_rep)
r_rede = cc.get('/sap/estoque/?loja=todas').content.decode()
t('"ir para a loja" lista as lojas; com poucas lojas (4) abre no modo normal, com 9+ abre compacta',
  'id="estIrPara"' in r_rede and 'data-padrao="normal"' in r_rede and '>Itaim Paulista' in r_rede)
t('chips das lojas mais perto, com a distância pintada', 'est-chip' in html_rep and 'est-km-perto' in html_rep)
r = cc.get('/sap/estoque/?aba=detalhado&loja=057E')
t('detalhado: copiar o serial', 'data-copiar="SC4H00000001"' in r.content.decode())
r = cc.get('/sap/estoque/?loja=057E')
t('matriz: dá para ordenar por loja (coluna de cada loja)', r.content.decode().count('data-ordenar="numero"') >= 5)
js = next((s for s in re.findall(r'<script>(.*?)</script>', r.content.decode(), re.S) if 'estFechar' in s), '')
arq = PASTA / 'estoque.js'
arq.write_text(js, encoding='utf-8')
try:
    chk = subprocess.run(['node', '--check', str(arq)], capture_output=True, text=True, timeout=30)
    t('JS da tela passa no node --check', chk.returncode == 0, chk.stderr[:300])
except FileNotFoundError:
    print('  (node não encontrado: pulei o --check)')
r = cc.get('/sap/estoque/?loja=todas')
t('rede inteira: todas as lojas viram coluna', r.status_code == 200 and len(r.context['colunas']) == 4
  and r.context['resumo']['livre'] == 31, r.context['resumo'])
r = cc.get('/sap/estoque/?aba=detalhado&loja=todas&deposito=RPAR')
t('detalhado por depósito', [u['serial'] for u in r.context['pagina']] == ['SC4H00000002'])
r = cc.get('/sap/estoque/?aba=detalhado&loja=057E&formato=csv')
corpo = r.content.decode('utf-8-sig')
t('CSV do detalhado', r.status_code == 200 and 'IMEI / serial' in corpo and 'SC4H00000001' in corpo
  and '350000000000099' in corpo, corpo[:200])
r = cc.get('/sap/estoque/?serial=0000000000002')
t('busca por serial acha a unidade e a loja', [a['loja'] for a in r.context['achados']] == ['Piúma'], r.context.get('achados'))

print('\n== 6) A GAVETA DE SERIAIS ==')
j = cc.get(f'/sap/estoque/unidades/?centro=064E&material={GALAXY}&modo=disponivel&minha=057E').json()
t('Piúma: 2 seriais, a mais parada primeiro, com km da minha loja', j['ok'] and [u['serial'] for u in j['unidades']]
  == ['350000000000001', '350000000000002'] and j['unidades'][0]['dias'] == 120 and 5 < j['km'] < 15, j)
j = cc.get(f'/sap/estoque/unidades/?centro=057E&material={GALAXY}&modo=transito').json()
t('em trânsito: a unidade chegando', [u['serial'] for u in j['unidades']] == ['350000000000099'])
j = cc.get(f'/sap/estoque/unidades/?centro=057E&material={PELICULA}&modo=disponivel').json()
t('item sem serial: diz quantas, sem lista', j['unidades'] == [] and j['sem_serial'] == 10)
t('loja inválida: 400', cc.get(f'/sap/estoque/unidades/?centro=XXXX&material={GALAXY}').status_code == 400)

print('\n== 7) QUEM ENTRA ==')
r = cg.get('/sap/estoque/?aba=disponivel')
t('gerente de loja entra e abre na loja dele, vendo a rede', r.status_code == 200 and r.context['f']['loja'] == '064E'
  and len(r.context['colunas']) == 4)
t('quem não é da Visão SAP não entra', cv.get('/sap/estoque/').status_code == 302)
t('nem na gaveta', cv.get(f'/sap/estoque/unidades/?centro=064E&material={GALAXY}',
                          HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code == 403)

print('\n== 8) SAP FORA DO AR ==')
est._MEMORIA.clear(); cache.clear()
with mock.patch.object(mysql, '_consultar', side_effect=mysql.SapIndisponivel('fora do ar')):
    r = cc.get('/sap/estoque/')
t('a tela avisa em vez de quebrar', r.status_code == 200 and 'Não deu para ler o estoque do SAP' in r.content.decode())
est._MEMORIA.clear(); cache.clear()
def sem_vendas(sql, parametros=()):
    if 'vw_vivogo' in sql:
        raise mysql.SapIndisponivel('view lenta')
    return dublê(sql, parametros)
with mock.patch.object(mysql, '_consultar', side_effect=sem_vendas):
    r = cc.get('/sap/estoque/?aba=reposicao')
t('sem as vendas: estoque abre e avisa na reposição', r.status_code == 200
  and 'Vendas do Vivo GO indisponíveis agora' in r.content.decode())

print('\n== 9) CATEGORIAS PADRONIZADAS (24/11/2026) ==')
est._MEMORIA.clear(); cache.clear()
foto = est.fotografia(forcar=True)
cats = est.categorias(foto)
t('cada categoria uma vez, com a grafia mais usada', cats.count('Essenciais') == 1 and 'essenciais' not in cats
  and cats.count('Demais Eletrônicos') == 1 and 'Demais eletrônicos' not in cats, cats)
t('"Sem categoria" uma vez, no fim', cats[-1] == 'Sem categoria' and cats.count('Sem categoria') == 1)
fabs = est.fabricantes(foto)
t('fabricante: TP Link e TP-Link viram um só; GEONAV e Geonav também', len([f for f in fabs if 'tp' in f.lower()]) == 1
  and len([f for f in fabs if f.lower() == 'geonav']) == 1, fabs)
t('subcategoria padronizada', {foto['catalogo'][m]['subcategoria'] for m in ('22000002', '22000003', '22000004')}
  == {'Áudio e Vídeo'})
t('filtrar por categoria pega as duas grafias', foto['catalogo']['22000001']['categoria'] == 'Essenciais')

print('\n== 10) PREÇO E RENOVA NA GAVETA ==')
from vendas.models import ItemPreco
ItemPreco.objects.create(categoria='SMARTPHONES', nome='Samsung Galaxy A57 5G 256GB', cod_sap='TGSA60912000', valor=2299,
                         ativo=True, importado_em=timezone.now(), extra={
                             'skus': [{'cor': 'Azul', 'sku': 'TGSA60912000'}, {'cor': 'Azul', 'sku': GALAXY}],
                             'PORTFÓLIO': 'In',
                             'tabela': {'PRÉ': {'Preço': '2299.00', 'PIX e Vivo Pay': '2069.10', '12x': '2299.00',
                                                '21x': '2582.73'},
                                        'VIVO V': {'Preço': '1749.00', 'PIX e Vivo Pay': '1574.10', '12x': '1749.00',
                                                   '21x': '1964.85'},
                                        'CONTROLE': {'Preço': '-'}}})
ItemPreco.objects.create(categoria='ELETRÔNICOS_LP_Não Conectados', nome='Carregador USB-C de 20W', cod_sap=CARREGADOR,
                         valor=219, ativo=True, importado_em=timezone.now(), extra={'skus': [{'sku': CARREGADOR}]})
j = cc.get(f'/sap/estoque/produto/?material={GALAXY}&minha=057E').json()
pr = j.get('preco') or {}
t('clicar no produto: preço da tabela, pelo SKU da cor', j['ok'] and pr.get('valor') == 2299.0, j.get('preco'))
t('PIX/Vivo Pay, 12x e 21x (parcela e total)', pr.get('pix') == 2069.1 and pr.get('parcela12') == 191.58
  and pr.get('parcela21') == 122.99 and pr.get('total21') == 2582.73, pr)
t('o preço em cada plano (sem o "-" de plano que não vende)', [g['grupo'] for g in pr.get('grupos', [])] == ['PRÉ', 'VIVO V'])
t('tem Renova: avisa o desconto, até quando e o preço com ele', j['renova'] and j['renova']['desconto'] == 300
  and j['renova']['com_renova'] == 1999.0, j.get('renova'))
t('onde tem: a sua loja primeiro e depois pela distância', [l['centro'] for l in j['lojas']] == ['057E', '064E', '220L']
  and j['lojas'][0]['minha'] and j['lojas'][1]['km'] < j['lojas'][2]['km'], j.get('lojas'))
j = cc.get(f'/sap/estoque/produto/?material={CARREGADOR}').json()
t('sem Renova: não avisa nada', j['renova'] is None and j['preco']['valor'] == 219.0 and j['preco']['pix'] is None)
j = cc.get(f'/sap/estoque/produto/?material={PELICULA}').json()
t('sem preço na tabela: a gaveta abre igual', j['ok'] and j['preco'] is None)
j = cc.get(f'/sap/estoque/unidades/?centro=064E&material={GALAXY}&modo=disponivel&minha=057E').json()
t('a gaveta dos seriais também traz preço e Renova', j['preco']['valor'] == 2299.0 and j['renova']['desconto'] == 300)
html = cc.get('/sap/estoque/?loja=057E').content.decode()
t('na tabela: o nome abre a gaveta do produto e o selo Renova aparece', f'data-produto="{GALAXY}"' in html
  and 'Renova −R$ 300' in html)
t('quem não é da Visão SAP não vê preço', cv.get(f'/sap/estoque/produto/?material={GALAXY}',
                                                HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code == 403)
js = next((sc for sc in re.findall(r'<script>(.*?)</script>', html, re.S) if 'estFechar' in sc), '')
arq = PASTA / 'estoque2.js'
arq.write_text(js, encoding='utf-8')
try:
    chk = subprocess.run(['node', '--check', str(arq)], capture_output=True, text=True, timeout=30)
    t('JS da tela (com preço e Renova) passa no node --check', chk.returncode == 0, chk.stderr[:300])
except FileNotFoundError:
    print('  (node não encontrado: pulei o --check)')


print('\n== 11) VISÃO INICIAL: CARTÕES DAS CATEGORIAS (25/11/2026) ==')
from auditoria_sap.views_estoque import icone_da_categoria as ve_ic, faixa_de_idade as ve_faixa
r = cc.get('/sap/estoque/')
html = r.content.decode()
cartoes = r.context['cartoes'] or []
t('sem nada escolhido abre nos cartões das categorias', r.status_code == 200 and r.context['inicial']
  and 'data-cartoes-categorias' in html and 'O que você procura?' in html)
t('um cartão por categoria, com ícone', {c['nome'] for c in cartoes} == set(est.categorias(est.fotografia()))
  and all(c['icone'].startswith('fa-') for c in cartoes), [(c['nome'], c['icone']) for c in cartoes])
sm = next((c for c in cartoes if c['nome'] == 'Smartphones'), None)
t('Smartphone com o ícone de celular e os números da loja e da rede', sm and sm['icone'] == 'fa-mobile-screen-button'
  and sm['rede'] >= sm['loja'] and sm['rede'] > 0, sm)
t('ícone tolera plural e grafia', ve_ic('Smartphone')[0] == ve_ic('Smartphones')[0] == 'fa-mobile-screen-button'
  and ve_ic('Áudio')[0] == 'fa-headphones' and ve_ic('Games')[0] == 'fa-gamepad' and ve_ic('Xpto')[0] == 'fa-tag')
t('"Sem categoria" fica por último', cartoes and cartoes[-1]['nome'] == 'Sem categoria')
t('o cartão leva para a categoria', 'categoria=Smartphones' in html and 'aba=disponivel' in html)
t('não monta a tabela na abertura', 'colunas' not in r.context and 'id="estRolagem"' not in html)
r = cc.get('/sap/estoque/?aba=disponivel&categoria=Smartphones')
t('clicar no cartão abre a tabela daquela categoria', r.status_code == 200 and not r.context['inicial']
  and 'colunas' in r.context and 'data-cartoes-categorias' not in r.content.decode())
h_cat = r.content.decode()
t('na tabela, a aba Panorama volta às categorias (24/11/2026)', 'data-voltar-inicio' in h_cat and '>Panorama' in h_cat
  and 'href="?aba=inicio&loja=' in h_cat)
t('a abertura (cartões) não tem o botão de voltar', 'data-voltar-inicio' not in html)
t('o filtro de categoria vira um chip que se tira', 'est-filtro-chip' in h_cat and '&categoria="' in h_cat)

print('\n== 11b) PANORAMA E NAVEGAÇÃO SEM RECARREGAR (24/11/2026) ==')
r = cc.get('/sap/estoque/?aba=inicio&loja=057E')
pan = r.context['panorama']
html_pan = r.content.decode()
t('?aba=inicio abre o Panorama da loja', r.status_code == 200 and r.context['inicial'] and r.context['f']['loja'] == '057E'
  and 'data-panorama' in html_pan and 'data-cartoes-categorias' in html_pan)
t('os números aparecem também no Panorama', 'data-contar=' in html_pan and 'Livre para venda' in html_pan)
lojas_pan = {l['centro']: l for l in pan['lojas']}
t('estoque por loja: livre e a caminho, maior primeiro', lojas_pan['057E']['livre'] == int(sum(
    deps.get('LVUT', (0, 0))[0] for (c, _m), deps in est.fotografia()['saldos'].items() if c == '057E'))
  and lojas_pan['057E']['transito'] == 1 and lojas_pan['057E']['minha']
  and [l['livre'] for l in pan['lojas']] == sorted((l['livre'] for l in pan['lojas']), reverse=True), pan['lojas'][:3])
t('a maior barra enche o trilho', max(l['pct_livre'] + l['pct_transito'] for l in pan['lojas']) == 100)
t('clicar na loja abre o Panorama dela (e na escolhida volta para a rede)',
  'href="?aba=inicio&loja=064E"' in html_pan and 'href="?aba=inicio&loja=todas"' in html_pan)
saude = {s['chave']: s for s in pan['saude']}
rep_loja = est.reposicao(est.fotografia(), '057E')
t('saúde da reposição conta cada situação', saude['critico']['qtd'] == sum(1 for l in rep_loja if l['situacao'] == 'critico')
  and pan['saude_total'] == len(rep_loja) and round(sum(s['pct'] for s in pan['saude'])) in (0, 100), saude)
t('cada pedaço da saúde abre a reposição filtrada', 'aba=reposicao&situacao=critico&loja=057E' in html_pan)
t('mais urgentes: só zerados que vendem', all(u['situacao'] in ('critico', 'ruptura') for u in pan['urgentes'])
  and len(pan['urgentes']) <= 6)
t('tempo parado: só seriais livres da loja', pan['idades_total'] + pan['sem_data'] == sum(
    1 for u in est.fotografia()['unidades'] if u[2] == '057E' and u[3] == 'LVUT' and u[4] == '01'), pan['idades'])
t('a faixa de tempo leva ao detalhado', 'aba=detalhado&deposito=LVUT&idade=' in html_pan)
t('faixas de idade', [ve_faixa(d) for d in (0, 30, 31, 60, 61, 90, 91, 400, None)]
  == ['ate30', 'ate30', '31a60', '31a60', '61a90', '61a90', 'mais90', 'mais90', ''])
r = cc.get('/sap/estoque/?aba=detalhado&loja=057E&idade=mais90')
t('detalhado filtra pelo tempo parado', r.status_code == 200 and all((u['dias'] or 0) > 90 for u in r.context['pagina'])
  and 'data-idades' in r.content.decode())
r = cc.get('/sap/estoque/?aba=inicio&loja=057E&categoria=Smartphones')
t('buscar/escolher categoria no Panorama mostra a tabela', r.context['f']['aba'] == 'disponivel' and not r.context['inicial'])
r = cc.get('/sap/estoque/?aba=reposicao&loja=057E&situacao=critico')
sits = {c: q for c, _r, q, _cor in r.context['situacoes']}
t('chips da reposição com a contagem de cada situação', sits[''] == len(rep_loja)
  and sits['critico'] == saude['critico']['qtd'] and len(r.context['pagina'].object_list) == sits['critico'], sits)
r = cc.get('/sap/estoque/?aba=disponivel&loja=057E', HTTP_X_ESTOQUE_PARCIAL='1')
parcial = r.content.decode()
t('pedido da própria tela devolve só o miolo', r.status_code == 200 and r['X-Estoque-Parcial'] == '1'
  and 'id="estEstado"' in parcial and 'id="estRolagem"' not in html_pan and 'id="estRolagem"' in parcial
  and '<html' not in parcial and 'id="estGaveta"' not in parcial and 'X-Estoque-Parcial' in r.get('Vary', ''))
r = cc.get('/sap/estoque/?aba=disponivel&loja=057E')
t('sem o cabeçalho, a página inteira (com a gaveta)', 'id="estGaveta"' in r.content.decode()
  and 'id="estApp"' in r.content.decode() and 'X-Estoque-Parcial' not in r)
t('a matriz pinta a quantidade em escala', re.search(r'class="est-q est-q-[1-5]"', r.content.decode()) is not None
  and 'est-q-5' in r.content.decode())
t('quem não vê o estoque não recebe o miolo', cv.get('/sap/estoque/?aba=disponivel', HTTP_X_ESTOQUE_PARCIAL='1').status_code == 302)

print('\n== 12) O CAMPO PRODUTO SE AUTOPREENCHE ==')
j = cc.get('/sap/estoque/sugestoes/?q=gal', HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
nomes = [s['nome'] for s in j['sugestoes']]
t('sugere os produtos que batem', j['ok'] and any('Galaxy' in n for n in nomes), nomes)
t('um por nome (as cores viram uma sugestão só)', len(nomes) == len(set(n.lower() for n in nomes)))
t('cada sugestão diz categoria e quanto tem livre', all({'categoria', 'livre', 'cores'} <= set(s) for s in j['sugestoes']))
t('menos de 2 letras não sugere nada', cc.get('/sap/estoque/sugestoes/?q=g').json()['sugestoes'] == [])
t('no máximo 12', len(cc.get('/sap/estoque/sugestoes/?q=a').json()['sugestoes']) <= 12)
t('quem não vê o estoque não recebe sugestão', cv.get('/sap/estoque/sugestoes/?q=gal',
                                                      HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code == 403)
html = cc.get('/sap/estoque/?aba=disponivel').content.decode()
t('o campo tem a lista de sugestões', 'id="estBusca"' in html and 'id="estSugestoes"' in html
  and '/sap/estoque/sugestoes/' in html)

print('\n== 13) PREÇO: ESCOLHER O PLANO E RESERVA PELO NOME ==')
j = cc.get(f'/sap/estoque/produto/?material={GALAXY}&minha=057E').json()
t('o preço traz os planos para o seletor (abre no PRÉ)', j['preco']['grupo'] == 'PRÉ'
  and [g['grupo'] for g in j['preco']['grupos']] == ['PRÉ', 'VIVO V'] and j['preco']['grupos'][1]['preco'] == 1749.0)
t('a tela tem o seletor de plano', 'estTrocarPlano' in html and 'est-plano' in html)
from auditoria_sap import views_estoque as ve
p_pel = est.produto(est.fotografia(), PELICULA)
ItemPreco.objects.create(categoria='ACESSÓRIOS', nome=p_pel['nome'] + ' (Demo)', cod_sap='OUTRO-SKU', valor=59.9,
                         ativo=True, importado_em=timezone.now(), extra={})
j = cc.get(f'/sap/estoque/produto/?material={PELICULA}').json()
t('sem o SKU na tabela: acha o preço pelo nome do produto', j['preco'] and j['preco']['valor'] == 59.9
  and j['preco'].get('pelo_nome') is True, j.get('preco'))
t('o SKU continua valendo antes do nome', cc.get(f'/sap/estoque/produto/?material={GALAXY}').json()['preco'].get('pelo_nome') is None)
t('nome normalizado ignora acento, caixa e parênteses', ve._nome_para_preco('iPhone 17 256GB (Demo)') == 'iphone 17 256gb'
  and ve._nome_para_preco('Película Câmera') == 'pelicula camera')

print('\n== 14) LIBERAR SÓ O ESTOQUE (25/11/2026) ==')
from users.models import UserModuleAccess
from auditoria_sap.permissions import pode_ver, pode_ver_estoque, so_estoque
csa = Client(); csa.force_login(chefe)
h = csa.get('/sap/gestores/').content.decode()
t('/sap/gestores/ tem a lista "Só o estoque"', 'Só o estoque' in h and 'name="estoque"' in h)
csa.post('/sap/gestores/', {'secao': 'estoque', 'estoque': [vendedor.pk]})
vendedor = User.objects.get(pk=vendedor.pk)
t('liberado só para o estoque', UserModuleAccess.objects.filter(user=vendedor, module_key='sap.estoque').exists()
  and pode_ver_estoque(vendedor) and not pode_ver(vendedor) and so_estoque(vendedor))
r = cv.get('/sap/estoque/?aba=disponivel')
h = r.content.decode()
nav = h.split('<nav class="flex items-center gap-1 mt-4')[1][:3000] if '<nav class="flex items-center gap-1 mt-4' in h else ''
t('ele abre o estoque', r.status_code == 200, r.status_code)
t('e só vê a aba Estoque', 'Estoque' in nav and 'Auditoria' not in nav and 'Visão Renova' not in nav, nav[:300])
t('a gaveta e as sugestões também abrem', cv.get(f'/sap/estoque/produto/?material={GALAXY}').status_code == 200
  and cv.get('/sap/estoque/sugestoes/?q=gal').status_code == 200)
t('mas a auditoria continua fechada', cv.get('/sap/').status_code == 302 and cv.get('/sap/painel/').status_code == 302)
t('o menu mostra "Estoque SAP"', 'Estoque SAP' in h)
t('liberar estoque não mexe na Visão SAP inteira', not UserModuleAccess.objects.filter(module_key='sap').exists())
csa.post('/sap/gestores/', {'secao': 'estoque'})
t('desmarcar tira o estoque', cv.get('/sap/estoque/?aba=disponivel').status_code == 302)

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
