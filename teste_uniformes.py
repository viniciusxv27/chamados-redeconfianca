"""Uniformes (10/11/2026): estoque, retirada, devolução, ajuste, ficha e acessos. Banco descartável; nada sai para a rede.

14/11/2026: novo e usado no mesmo item (saldo × saldo_usado, condição no movimento) e a migração que junta os itens "· Usado".
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: o schema sai direto dos models (`--run-syncdb`),
# nada toca o Postgres compartilhado nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-uniformes-'))
(PASTA / 'zz_uniformes_settings.py').write_text(f"""
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
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_uniformes_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import csv as _csv
import io
import pathlib as _pl
import re

import openpyxl
import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from users.models import Sector
from uniformes import importacao, servicos
from uniformes.models import AcessoUniformes, ItemUniforme, MovimentoUniforme as M
from uniformes.servicos import ErroEstoque

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

loja = Sector.objects.create(name='Loja ZZ Uniforme')
chefe = User.objects.create_user(username='zzu.chefe', email='zzu.chefe@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Che', hierarchy='SUPERADMIN', is_superuser=True)
gestora = User.objects.create_user(username='zzu.jessica', email='zzu.jessica@exemplo-teste.local', password='S3nha!teste',
                                   first_name='Jess', last_name='Gestora', hierarchy='PADRAO')
comum = User.objects.create_user(username='zzu.comum', email='zzu.comum@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Com', hierarchy='PADRAO')
gustavo = User.objects.create_user(username='zzu.gustavo', email='zzu.gustavo@exemplo-teste.local', password='S3nha!teste',
                                   first_name='Gustavo', last_name='Teste', hierarchy='PADRAO', sector=loja,
                                   uniform_size_shirt='M', uniform_size_pants='40')
cs, cg, cc, cgu = Client(), Client(), Client(), Client()
cs.force_login(chefe); cg.force_login(gestora); cc.force_login(comum); cgu.force_login(gustavo)

print('acesso')
PAGINAS = ['/uniformes/', '/uniformes/historico/', '/uniformes/retirada/', '/uniformes/devolucao/',
           '/uniformes/importar/', '/uniformes/acessos/', '/uniformes/exportar/', '/uniformes/historico/exportar/',
           f'/uniformes/colaborador/{gustavo.pk}/']
for u in PAGINAS:
    t(f'SUPERADMIN abre {u}', cs.get(u).status_code == 200, cs.get(u).status_code)
for u in PAGINAS:
    t(f'colaborador comum não vê {u}', cc.get(u).status_code == 404, cc.get(u).status_code)
t('quem não entrou vai para o login', Client().get('/uniformes/').status_code == 302)
t('gestora ainda fora da lista: 404', cg.get('/uniformes/').status_code == 404)
r = cg.post('/uniformes/acessos/', {'user': comum.pk})
t('fora da lista não mexe na lista', r.status_code == 404 and not AcessoUniformes.objects.exists())
r = cs.post('/uniformes/acessos/', {'user': gestora.pk, 'observacao': 'controla o estoque'})
t('SUPERADMIN libera a gestora', AcessoUniformes.objects.filter(user=gestora, liberado_por=chefe).exists())
for u in PAGINAS[:5] + PAGINAS[6:]:
    t(f'gestora abre {u}', cg.get(u).status_code == 200, cg.get(u).status_code)
r = cg.get('/uniformes/acessos/')
t('gestora não abre a lista de acessos (volta ao estoque)', r.status_code == 302 and r['Location'].endswith('/uniformes/'))
cg.post('/uniformes/acessos/', {'user': comum.pk})
t('gestora não libera ninguém', not AcessoUniformes.objects.filter(user=comum).exists())
acesso = AcessoUniformes.objects.get(user=gestora)
cg.post(f'/uniformes/acessos/{acesso.pk}/remover/')
t('gestora não se tira nem tira ninguém', AcessoUniformes.objects.filter(pk=acesso.pk).exists())
t('aba "Quem cuida" só para o SUPERADMIN', '/uniformes/acessos/' in cs.get('/uniformes/').content.decode()
  and '/uniformes/acessos/' not in cg.get('/uniformes/').content.decode())
menu = 'href="/uniformes/"'
t('menu: SUPERADMIN vê', menu in cs.get('/users/profile/').content.decode())
t('menu: gestora vê', menu in cg.get('/users/profile/').content.decode())
t('menu: comum não vê', menu not in cc.get('/users/profile/').content.decode())

print('tipos sugeridos (12/11/2026: sem Polo, Camiseta e Boné)')
from uniformes.models import TIPOS_SUGERIDOS  # noqa: E402
corpo = cg.get('/uniformes/').content.decode()
lista = re.search(r'<datalist id="un-tipos">(.*?)</datalist>', corpo, re.S)
sugeridos = re.findall(r'<option value="([^"]+)">', lista.group(1)) if lista else []
t('sugestões sem Polo, Camiseta e Boné', TIPOS_SUGERIDOS == ['Camisa', 'Calça', 'Jaqueta', 'Colete'], TIPOS_SUGERIDOS)
t('tela oferece só Calça, Camisa, Colete e Jaqueta', sugeridos == ['Calça', 'Camisa', 'Colete', 'Jaqueta'], sugeridos)
t('nenhum dos tipos tirados aparece no estoque vazio',
  not re.search(r'value="(Polo|Camiseta|Boné)"', corpo), re.findall(r'value="(Polo|Camiseta|Boné)"', corpo))

print('itens e entrada')
r = cg.post('/uniformes/item/novo/', {'tipo': 'camisa', 'tamanho': 'm', 'descricao': 'Polo azul', 'quantidade': '5',
                                     'estoque_minimo': '2', 'nota_fiscal': 'NF 123'})
camisa = ItemUniforme.objects.get(tipo='Camisa', tamanho='M')
t('cadastra item normalizado com quantidade inicial', camisa.saldo == 5 and camisa.descricao == 'Polo azul' and camisa.estoque_minimo == 2,
  (camisa.saldo, camisa.descricao))
ent = M.objects.get(item=camisa)
t('entrada grava quem, quando e a NF', ent.tipo == M.ENTRADA and ent.registrado_por == gestora and ent.delta == 5
  and ent.saldo_apos == 5 and ent.nota_fiscal == 'NF 123' and ent.data == timezone.localdate())
cg.post('/uniformes/item/novo/', {'tipo': 'Camisa', 'tamanho': 'M ', 'descricao': 'polo  AZUL'})
t('item igual (maiúsculas/espaços) não duplica', ItemUniforme.objects.filter(tipo__iexact='camisa').count() == 1)
calca, _ = servicos.criar_item(tipo='Calça', tamanho='40', por=chefe)
bone, _ = servicos.criar_item(tipo='Boné', por=chefe)
r = cs.post('/uniformes/entrada/', {'item': calca.pk, 'quantidade': '4', 'data': '2026-11-01', 'observacao': 'Remessa'})
calca.refresh_from_db()
t('entrada soma no saldo', calca.saldo == 4)
t('entrada guarda a data de recebimento escolhida', M.objects.filter(item=calca, tipo=M.ENTRADA).first().data.isoformat() == '2026-11-01')
cs.post('/uniformes/entrada/', {'item': calca.pk, 'quantidade': '-3'})
cs.post('/uniformes/entrada/', {'item': calca.pk, 'quantidade': '1.5'})
calca.refresh_from_db()
t('quantidade negativa ou quebrada é recusada', calca.saldo == 4 and M.objects.filter(item=calca).count() == 1)
def linhas_da_lista(resposta):
    return {int(x) for x in re.findall(r'data-editar=\'\{"id": (\d+)', resposta.content.decode())}


t('filtro "só em falta" mostra só o boné zerado', linhas_da_lista(cs.get('/uniformes/?baixo=1')) == {bone.pk},
  linhas_da_lista(cs.get('/uniformes/?baixo=1')))
t('filtro por tipo', linhas_da_lista(cs.get('/uniformes/?tipo=Calça')) == {calca.pk})
t('filtro por tamanho', linhas_da_lista(cs.get('/uniformes/?tamanho=M')) == {camisa.pk})

print('retirada')
r = cg.post('/uniformes/retirada/', {'colaborador': gustavo.pk, 'item': [camisa.pk, calca.pk], 'quantidade': ['2', '2'],
                                     'data_entrega': '2026-11-05', 'observacao': 'kit de admissão'})
camisa.refresh_from_db(); calca.refresh_from_db()
t('retirada abate ao vivo', camisa.saldo == 3 and calca.saldo == 2, (camisa.saldo, calca.saldo))
movs = list(M.objects.filter(tipo=M.RETIRADA))
t('retirada grava para quem, quem retirou, data da retirada e da entrega',
  len(movs) == 2 and all(m.colaborador == gustavo and m.registrado_por == gestora and m.registrado_em
                         and m.data.isoformat() == '2026-11-05' and m.delta == -2 for m in movs))
t('as peças da mesma retirada viram um lote (um termo)', movs[0].lote == movs[1].lote)
t('depois da retirada vai para a pessoa com o termo', r.status_code == 302 and f'/uniformes/colaborador/{gustavo.pk}/?termo=' in r['Location'])
r = cg.get(r['Location'])
t('tela da pessoa oferece o termo', 'Abrir termo' in r.content.decode())
r = cg.get(f'/uniformes/termo/{movs[0].lote}/')
corpo = r.content.decode()
t('termo lista peças, quantidades e data', r.status_code == 200 and 'Gustavo' in corpo and 'Polo azul' in corpo
  and '05/11/2026' in corpo and 'Total de peças' in corpo and '>4<' in corpo)
t('termo não abre para comum', cc.get(f'/uniformes/termo/{movs[0].lote}/').status_code == 404)

r = cg.post('/uniformes/retirada/', {'colaborador': gustavo.pk, 'item': [camisa.pk, calca.pk], 'quantidade': ['1', '3']})
camisa.refresh_from_db(); calca.refresh_from_db()
t('sem saldo numa linha: nada é lançado (tudo ou nada)', camisa.saldo == 3 and calca.saldo == 2
  and M.objects.filter(tipo=M.RETIRADA).count() == 2)
r = cg.post('/uniformes/retirada/', {'colaborador': '', 'item': [camisa.pk], 'quantidade': ['1']})
t('retirada sem colaborador é recusada', M.objects.filter(tipo=M.RETIRADA).count() == 2)
gustavo2 = User.objects.create_user(username='zzu.inativo', email='zzu.inativo@exemplo-teste.local', password='x', is_active=False)
cg.post('/uniformes/retirada/', {'colaborador': gustavo2.pk, 'item': [camisa.pk], 'quantidade': ['1']})
t('retirada para inativo é recusada', M.objects.filter(colaborador=gustavo2).count() == 0)

print('estoque nunca negativo / caminho travado')
velho = ItemUniforme.objects.get(pk=calca.pk)          # saldo 2 lido "por outra tela"
servicos.registrar_retirada(linhas=[(calca.pk, 2)], colaborador=gustavo, por=chefe)
try:
    velho.saldo = 2   # a outra tela ainda acha que há 2
    servicos.registrar_retirada(linhas=[(velho.pk, 1)], colaborador=gustavo, por=chefe)
    recusou = False
except ErroEstoque:
    recusou = True
calca.refresh_from_db()
t('segunda retirada concorrente relê o saldo travado e é recusada', recusou and calca.saldo == 0)
chamadas = []
original = type(ItemUniforme.objects.all()).select_for_update


def espiao(self, *a, **k):
    chamadas.append(self.model)
    return original(self, *a, **k)


with mock.patch.object(type(ItemUniforme.objects.all()), 'select_for_update', espiao):
    servicos.registrar_entrada(item_id=calca.pk, quantidade=1, por=chefe)
t('lançamento trava o item (select_for_update)', ItemUniforme in chamadas)
try:
    with transaction.atomic():
        ItemUniforme.objects.filter(pk=calca.pk).update(saldo=-1)
    barrou = False
except IntegrityError:
    barrou = True
t('o banco também barra saldo negativo', barrou)
calca.refresh_from_db()
soma = sum(M.objects.filter(item=calca).values_list('delta', flat=True))
t('saldo do item = soma do livro', soma == calca.saldo, (soma, calca.saldo))

print('devolução e ajuste')
t('posse do Gustavo', servicos.posse(gustavo) == {camisa.pk: 2, calca.pk: 4}, servicos.posse(gustavo))
r = cg.post('/uniformes/devolucao/', {'colaborador': gustavo.pk, 'item': [calca.pk], 'quantidade': ['5']})
calca.refresh_from_db()
t('não devolve mais do que a pessoa tem', calca.saldo == 1 and not M.objects.filter(tipo=M.DEVOLUCAO).exists())
r = cg.post('/uniformes/devolucao/', {'colaborador': gustavo.pk, 'item': [calca.pk, camisa.pk], 'quantidade': ['1', '0'],
                                      'observacao': 'desligamento'})
calca.refresh_from_db()
dev = M.objects.get(tipo=M.DEVOLUCAO)
t('devolução volta ao estoque como USADA, com quem e quando', calca.saldo == 1 and calca.saldo_usado == 1
  and dev.condicao == M.USADO and dev.saldo_apos == 1 and dev.colaborador == gustavo and dev.registrado_por == gestora
  and dev.data == timezone.localdate() and dev.delta == 1, (calca.saldo, calca.saldo_usado, dev.condicao))
t('posse cai depois da devolução', servicos.posse(gustavo) == {camisa.pk: 2, calca.pk: 3})
r = cg.get(f'/uniformes/devolucao/?colaborador={gustavo.pk}')
t('tela de devolução lista o que a pessoa tem', 'Polo azul' in r.content.decode() and 'Gustavo' in r.content.decode())
cg.post('/uniformes/ajuste/', {'item': camisa.pk, 'contagem': '1', 'motivo': ''})
camisa.refresh_from_db()
t('ajuste sem motivo é recusado', camisa.saldo == 3)
cg.post('/uniformes/ajuste/', {'item': camisa.pk, 'contagem': '1', 'motivo': 'inventário: 2 manchadas'})
camisa.refresh_from_db()
aj = M.objects.get(tipo=M.AJUSTE)
t('ajuste acerta o saldo e registra quem mudou a contagem', camisa.saldo == 1 and aj.delta == -2 and aj.quantidade == 2
  and aj.registrado_por == gestora and aj.observacao == 'inventário: 2 manchadas')
cg.post('/uniformes/ajuste/', {'item': camisa.pk, 'contagem': '-1', 'motivo': 'x'})
camisa.refresh_from_db()
t('ajuste para negativo é recusado', camisa.saldo == 1)
t('ajuste não mexe na posse', servicos.posse(gustavo)[camisa.pk] == 2)

print('data de entrega')
ret = M.objects.filter(tipo=M.RETIRADA, colaborador=gustavo).order_by('id').first()
cg.post(f'/uniformes/movimento/{ret.pk}/data-entrega/', {'data_entrega': '2026-11-07'})
t('data de entrega muda no lote inteiro e registra quem mudou',
  all(m.data.isoformat() == '2026-11-07' and m.data_alterada_por == gestora for m in M.objects.filter(lote=ret.lote)))
cg.post(f'/uniformes/movimento/{ret.pk}/data-entrega/', {'data_entrega': ''})
t('data de entrega pode ficar a confirmar', M.objects.get(pk=ret.pk).data is None)
cg.post(f'/uniformes/movimento/{ret.pk}/data-entrega/', {'data_entrega': '2026-11-05'})

print('ficha do colaborador')
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.template import Context, Template
with CaptureQueriesContext(connection) as cq:
    dados = servicos.uniformes_da_ficha(gustavo)
t('ficha monta tudo com uma consulta', len(cq.captured_queries) == 1, len(cq.captured_queries))
t('ficha: o que ele tem e quando foi entregue', {(p['item'].pk, p['quantidade'], p['ultima_entrega'].isoformat() if p['ultima_entrega'] else None)
                                                 for p in dados['pecas']} == {(camisa.pk, 2, '2026-11-05'), (calca.pk, 3, timezone.localdate().isoformat())},
  [(p['item'].nome, p['quantidade'], p['ultima_entrega']) for p in dados['pecas']])
r = cs.get(f'/users/manage/users/{gustavo.pk}/profile/')
corpo = r.content.decode()
t('perfil na gestão mostra bloco Uniformes com peças e data', r.status_code == 200 and 'Uniformes' in corpo
  and 'Camisa · Polo azul · M' in corpo and 'entregue 05/11/2026' in corpo and '5 peças' in corpo, r.status_code)
t('perfil mostra link de retirada para quem gere', f'/uniformes/retirada/?colaborador={gustavo.pk}' in corpo)
r = cs.get(f'/users/manage/users/{gustavo.pk}/profile/print/')
corpo = r.content.decode()
t('ficha impressa mostra os uniformes', r.status_code == 200 and '<h2>Uniformes</h2>' in corpo and 'Camisa · Polo azul · M' in corpo
  and '05/11/2026' in corpo)
r = cgu.get('/users/profile/')
corpo = r.content.decode()
t('Meu perfil mostra os meus uniformes sem links de gestão', r.status_code == 200 and 'Meus uniformes' in corpo
  and 'Camisa · Polo azul · M' in corpo and '/uniformes/retirada/' not in corpo and 'href="/uniformes/"' not in corpo)
r = cc.get('/users/profile/')
t('Meu perfil de quem não tem uniforme', 'Você não está com nenhum uniforme' in r.content.decode())

print('importação')


def xlsx(linhas):
    wb = openpyxl.Workbook(); ws = wb.active
    for linha in linhas:
        ws.append(linha)
    b = io.BytesIO(); wb.save(b); b.seek(0)
    return b


planilha = xlsx([['Controle de uniformes da rede'], [], ['PEÇA', 'Tam.', 'Qtde', 'Modelo', 'Estoque Mínimo'],
                 ['Camisa', 'M', 3, 'polo azul', 2], ['Jaqueta', 'G', '2,0', '', ''], ['Jaqueta', 'g', 1, None, None],
                 ['Calça', 40.0, 'abc', '', ''], [None, None, None, None, None], ['', 'P', 3, '', '']])
lida = importacao.ler_planilha(planilha, 'estoque.xlsx')
por_tipo = {(l['tipo'], l['tamanho']): l for l in lida['linhas']}
t('acha o cabeçalho com sinônimos, acento e caixa', set(lida['colunas']) == {'tipo', 'tamanho', 'quantidade', 'descricao', 'minimo'}, lida['colunas'])
t('casa item existente ignorando caixa', por_tipo[('Camisa', 'M')]['item_id'] == camisa.pk)
t('soma linha repetida e aceita "2,0"', por_tipo[('Jaqueta', 'G')]['quantidade'] == 3)
t('avisa quantidade inválida e linha sem tipo', any('abc' in a for a in lida['avisos']) and any('sem tipo' in a for a in lida['avisos']), lida['avisos'])
try:
    importacao.ler_planilha(xlsx([['Nome', 'Outra']]), 'x.xlsx'); recusou = False
except ErroEstoque:
    recusou = True
t('planilha sem colunas reconhecíveis é recusada', recusou)
b = io.StringIO(); w = _csv.writer(b, delimiter=';'); w.writerow(['Descrição', 'Quantidade']); w.writerow(['Colete', '4'])
lida_csv = importacao.ler_planilha(io.BytesIO(b.getvalue().encode('latin-1')), 'e.csv')
t('csv com ; e só descrição vira tipo', lida_csv['linhas'][0]['tipo'] == 'Colete' and lida_csv['linhas'][0]['quantidade'] == 4)

r = cg.post('/uniformes/importar/', {'acao': 'ler', 'planilha': SimpleUploadedFile('estoque.xlsx', xlsx(
    [['Tipo', 'Tamanho', 'Quantidade', 'Descrição'], ['Camisa', 'M', 3, 'Polo azul'], ['Jaqueta', 'G', 2, '']]).getvalue())})
r = cg.get('/uniformes/importar/')
t('prévia na tela, nada gravado ainda', 'Prévia de estoque.xlsx' in r.content.decode() and not ItemUniforme.objects.filter(tipo='Jaqueta').exists())
camisa.refresh_from_db(); antes = camisa.saldo
r = cg.post('/uniformes/importar/', {'acao': 'confirmar', 'modo': 'ENTRADA'})
camisa.refresh_from_db()
jaq = ItemUniforme.objects.get(tipo='Jaqueta')
t('confirmar como entrada soma e cria item novo', camisa.saldo == antes + 3 and jaq.saldo == 2
  and M.objects.filter(item=jaq, tipo=M.ENTRADA, registrado_por=gestora, observacao__contains='estoque.xlsx').exists())
cg.post('/uniformes/importar/', {'acao': 'ler', 'planilha': SimpleUploadedFile('inventario.xlsx', xlsx(
    [['Tipo', 'Tamanho', 'Quantidade'], ['Jaqueta', 'G', 5], ['Boné', '', 0]]).getvalue())})
cg.post('/uniformes/importar/', {'acao': 'confirmar', 'modo': 'ACERTAR'})
jaq.refresh_from_db()
t('confirmar acertando saldo vira ajuste com motivo', jaq.saldo == 5 and M.objects.filter(item=jaq, tipo=M.AJUSTE, delta=3,
  observacao__contains='inventario.xlsx').exists())

print('importação: planilha de conferência da rede (12/11/2026)')
from datetime import date as _date, datetime as _dt  # noqa: E402

CAB_CONF = ['UNIFORME / TAMANHO', 'SALDO NOVOS', 'SALDO USADOS', 'DATA AJUSTE', 'OBS:', None, None]


def conferencia():
    """O desenho da "RELAÇÃO UNIFORMES - ES - ESTOQUE": seção, quadro de totais à direita, novos × usados."""
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Conferência Uniformes'
    for linha in [
        CAB_CONF,
        ['UNIFORMES FEMININOS', None, None, None, None, None, None],
        ['Camisa CN - Feminino - PP', 19.0, 0.0, _dt(2026, 11, 4), None, 'AJUSTAR FÓRMULAS', None],
        ['Camisa CN - Feminino - P', 0.0, 15.0, _dt(2026, 10, 23), None, 'TOTAL UNIFORMES:', 675],
        ['Camisa CN - Feminino - M', 9.0, 21.0, _dt(2026, 11, 4), 'Jéssica', None, None],
        ['Jaqueta GER - Feminino - P ', 1.0, 0.0, _dt(2026, 10, 23), 'Jéssica', 'TOTAL UNIFORMES FEM. GER:', 51],
        ['Calça CN - Feminino - 38', 27.0, 0.0, '11/11/2026', 'Gus', None, None],
        ['UNIFORMES MASCULINOS', None, None, None, None, None, None],
        ['Camisa CN - Masculino - EG1', 0.0, 2.0, _dt(2026, 10, 23), None, None, None],
        ['Calça GER - Masculino - 58', 1.0, 0.0, _dt(2026, 10, 30), 'Jéssica', None, None],
        ['Camisa GER - Masculino - G4', 'dois', 1.0, _dt(2026, 10, 30), None, None, None],
    ]:
        ws.append(linha)
    ws.merge_cells('A2:D2')
    b = io.BytesIO(); wb.save(b); b.seek(0)
    return b


t('nome da conferência vira peça, modelo e tamanho', importacao.separar_nome('Camisa CN - Feminino - PP') == ('Camisa', 'CN Feminino', 'PP')
  and importacao.separar_nome('Jaqueta GER - Feminino - P ') == ('Jaqueta', 'GER Feminino', 'P')
  and importacao.separar_nome('Calça CN - Masculino - 58') == ('Calça', 'CN Masculino', '58')
  and importacao.separar_nome('Camisa CN - Masculino - EG1') == ('Camisa', 'CN Masculino', 'EG1')
  and importacao.separar_nome('Boné') == ('Boné', '', '') and importacao.separar_nome('Polo-Azul - M') == ('Polo-Azul', '', 'M'),
  [importacao.separar_nome(x) for x in ('Camisa CN - Feminino - PP', 'Jaqueta GER - Feminino - P ', 'Polo-Azul - M')])
lida = importacao.ler_planilha(conferencia(), 'RELAÇÃO UNIFORMES - ES - ESTOQUE.xlsx')
por = {(l['tipo'], l['descricao'], l['tamanho']): l for l in lida['linhas']}
t('acha o formato da conferência e sugere acertar o saldo', lida['formato'] == importacao.FORMATO_CONFERENCIA
  and lida['modo_sugerido'] == importacao.MODO_ACERTAR and set(lida['colunas']) == {'nome', 'novos', 'usados', 'data', 'obs'}, lida['colunas'])
t('novos e usados na MESMA linha (um item por peça)', por[('Camisa', 'CN Feminino', 'M')]['quantidade'] == 9
  and por[('Camisa', 'CN Feminino', 'M')]['usados'] == 21 and len(lida['linhas']) == 7
  and not any('usado' in l['descricao'].lower() for l in lida['linhas']), [(k, v['quantidade'], v['usados']) for k, v in por.items()])
t('linha só com usados também é o item da peça', por[('Camisa', 'CN Feminino', 'P')]['quantidade'] == 0
  and por[('Camisa', 'CN Feminino', 'P')]['usados'] == 15 and por[('Camisa', 'CN Masculino', 'EG1')]['usados'] == 2)
t('seções e o quadro de totais à direita ficam de fora', lida['totais']['secoes'] == 2
  and not any('TOTAL' in l['tipo'].upper() or 'UNIFORMES' in l['tipo'].upper() for l in lida['linhas']))
t('data (datetime ou texto) e quem conferiu vão junto', por[('Camisa', 'CN Feminino', 'PP')]['data'] == '2026-11-04'
  and por[('Calça', 'CN Feminino', '38')]['data'] == '2026-11-11' and por[('Calça', 'CN Feminino', '38')]['obs'] == 'Gus')
t('saldo que não é número inteiro é avisado e a linha fica de fora', any('G4' in a and 'dois' in a for a in lida['avisos'])
  and not any(l['tamanho'] == 'G4' for l in lida['linhas']), lida['avisos'])
t('totais batem com a planilha (novos + usados)', lida['totais'] == {'novos': 57, 'usados': 38, 'secoes': 2, 'pecas': 95}, lida['totais'])

r = cg.post('/uniformes/importar/', {'acao': 'ler', 'planilha': SimpleUploadedFile(
    'RELAÇÃO UNIFORMES - ES - ESTOQUE.xlsx', conferencia().getvalue())})
html = cg.get('/uniformes/importar/').content.decode()
linha_m = re.search(r'<tr data-linha-previa>(?:(?!</tr>).)*?CN Feminino(?:(?!</tr>).)*?>M<(?:(?!</tr>).)*?</tr>', html, re.S)
t('prévia mostra o total da conferência e já marca "acertar o saldo"', 'data-totais-conferencia' in html and '<b>95</b> peças' in html
  and 'value="ACERTAR" checked' in html and 'value="ENTRADA" checked' not in html
  and '11/11/2026 · Gus' in html, html[html.find('data-totais'):html.find('data-totais') + 300])
t('prévia mostra novos e usados na mesma linha', bool(linha_m) and 'data-novos>9<' in linha_m.group(0)
  and 'data-usados>21<' in linha_m.group(0) and '>Novos<' in html and '>Usados<' in html,
  linha_m.group(0) if linha_m else html[html.find('<tbody'):html.find('<tbody') + 600])
r = cg.post('/uniformes/importar/', {'acao': 'confirmar', 'modo': 'ACERTAR'})
cm = ItemUniforme.objects.get(tipo='Camisa', descricao='CN Feminino', tamanho='M')
calca = ItemUniforme.objects.get(tipo='Calça', descricao='CN Feminino', tamanho='38')
cp = ItemUniforme.objects.get(tipo='Camisa', descricao='CN Feminino', tamanho='P')
t('confirmar põe novos no estoque novo e usados nos usados do MESMO item', cm.saldo == 9 and cm.saldo_usado == 21
  and calca.saldo == 27 and calca.saldo_usado == 0 and cp.saldo == 0 and cp.saldo_usado == 15,
  [(i.nome, i.saldo, i.saldo_usado) for i in (cm, calca, cp)])
t('nenhum item "· Usado" criado', not ItemUniforme.objects.filter(descricao__icontains='usad').exists(),
  list(ItemUniforme.objects.filter(descricao__icontains='usad').values_list('descricao', flat=True)))
ajustes_cm = {m.condicao: m for m in M.objects.filter(item=cm, tipo=M.AJUSTE)}
t('um ajuste por condição, cada um com o saldo da sua condição', set(ajustes_cm) == {M.NOVO, M.USADO}
  and ajustes_cm[M.NOVO].delta == 9 and ajustes_cm[M.NOVO].saldo_apos == 9
  and ajustes_cm[M.USADO].delta == 21 and ajustes_cm[M.USADO].saldo_apos == 21,
  [(m.condicao, m.delta, m.saldo_apos) for m in ajustes_cm.values()])
mov = M.objects.get(item=calca, tipo=M.AJUSTE)
t('o ajuste leva a data da conferência e quem conferiu (usado 0 não vira lançamento)', mov.data == _date(2026, 11, 11)
  and 'por Gus' in mov.observacao and mov.condicao == M.NOVO
  and 'conferência de 11/11/2026' in mov.observacao and mov.registrado_por == gestora, (mov.data, mov.observacao))
confs = ItemUniforme.objects.filter(descricao__regex=r'^(CN|GER) (Feminino|Masculino)$')
conf_novos, conf_usados = sum(i.saldo for i in confs), sum(i.saldo_usado for i in confs)
t('total importado = total da planilha (57 novas + 38 usadas = 95) em 7 itens', (conf_novos, conf_usados) == (57, 38)
  and confs.count() == 7, (conf_novos, conf_usados, confs.count()))
movs_antes = M.objects.count()
cg.post('/uniformes/importar/', {'acao': 'ler', 'planilha': SimpleUploadedFile('conf.xlsx', conferencia().getvalue())})
lida2 = importacao.ler_planilha(conferencia(), 'conf.xlsx')
t('2ª leitura reconhece os itens que já existem (com novas e usadas de hoje)', all(l['item_id'] for l in lida2['linhas'])
  and {(l['saldo_atual'], l['usado_atual']) for l in lida2['linhas'] if l['tamanho'] == 'M'} == {(9, 21)})
cg.post('/uniformes/importar/', {'acao': 'confirmar', 'modo': 'ACERTAR'})
cm.refresh_from_db()
t('reimportar a mesma conferência não muda nada', (cm.saldo, cm.saldo_usado) == (9, 21) and M.objects.count() == movs_antes,
  (cm.saldo, cm.saldo_usado, M.objects.count() - movs_antes))
resumo_entrada = importacao.aplicar(importacao.ler_planilha(conferencia(), 'conf.xlsx')['linhas'], modo=importacao.MODO_ENTRADA,
                                    por=gestora, nome_arquivo='remessa.xlsx')
cm.refresh_from_db()
t('como entrada, novas e usadas somam cada uma no seu saldo', (cm.saldo, cm.saldo_usado) == (18, 42)
  and resumo_entrada['pecas'] == 95 and M.objects.filter(item=cm, tipo=M.ENTRADA, condicao=M.USADO, delta=21, saldo_apos=42).exists(),
  (cm.saldo, cm.saldo_usado, resumo_entrada))
importacao.aplicar(importacao.ler_planilha(conferencia(), 'conf.xlsx')['linhas'], modo=importacao.MODO_ACERTAR,
                   por=gestora, nome_arquivo='volta.xlsx')
cm.refresh_from_db()
t('acertar de novo volta ao da planilha', (cm.saldo, cm.saldo_usado) == (9, 21))
gen = importacao.ler_planilha(xlsx([['Tipo', 'Tamanho', 'Descrição', 'Quantidade'], ['Camisa', 'M', 'CN Feminino', 4],
                                    ['Camisa', 'M', 'CN Feminino - Usado', 3]]), 'g.xlsx')
t('genérico: linha "… - Usado" vai para os usados do item (não vira item)', len(gen['linhas']) == 1
  and gen['linhas'][0]['quantidade'] == 4 and gen['linhas'][0]['usados'] == 3 and gen['linhas'][0]['item_id'] == cm.pk, gen['linhas'])
gen2 = importacao.ler_planilha(xlsx([['Tipo', 'Tamanho', 'Descrição', 'Quantidade'], ['Camisa', 'M', 'CN Feminino', 4]]), 'g.xlsx')
importacao.aplicar(gen2['linhas'], modo=importacao.MODO_ACERTAR, por=gestora, nome_arquivo='g.xlsx')
cm.refresh_from_db()
t('genérico só com novos acerta o estoque novo e não toca nos usados', (cm.saldo, cm.saldo_usado) == (4, 21), (cm.saldo, cm.saldo_usado))
importacao.aplicar(importacao.ler_planilha(conferencia(), 'conf.xlsx')['linhas'], modo=importacao.MODO_ACERTAR,
                   por=gestora, nome_arquivo='volta.xlsx')
def conf_extra(linhas, cab=CAB_CONF):
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(cab)
    for linha in linhas:
        ws.append(linha)
    b = io.BytesIO(); wb.save(b); b.seek(0)
    return b


lx = importacao.ler_planilha(conf_extra([['Camisa CN - Feminino - P', 'inf', 0, None, None],
                                         ['Camisa CN - Feminino - M', '1e400', 0, None, None],
                                         ['Camisa CN - Feminino - G', 3, 0, 46330, 'Gus'],
                                         ['TOTAL', 3, 0, None, None]]), 'x.xlsx')
t('"inf"/"1e400" no saldo viram aviso (antes: erro 500)', sum('não é número inteiro' in a for a in lx['avisos']) == 2
  and [l['tamanho'] for l in lx['linhas']] == ['G'], lx['avisos'])
t('data como nº de série do Excel é lida; linha TOTAL não vira peça', lx['linhas'][0]['data'] == '2026-11-04'
  and lx['totais']['secoes'] == 1 and lx['totais']['pecas'] == 3, (lx['linhas'], lx['totais']))
ly = importacao.ler_planilha(conf_extra([['Camisa CN - Feminino - G', 3, 1, None, None]],
                                        cab=['UNIFORME / TAMANHO', 'SALDO (NOVOS)', 'SALDO USADOS']), 'y.xlsx')
t('coluna de novos não reconhecida: aviso em destaque (não zera calado)', any('SALDO NOVOS' in a and 'ATENÇÃO' in a for a in ly['avisos']), ly['avisos'])
try:
    importacao.ler_planilha(xlsx([['Tipo', 'Quantidade'], ['Boné', 'inf']]), 'g.xlsx'); gen_ok = True
except Exception as exc:  # noqa: BLE001
    gen_ok = isinstance(exc, ErroEstoque)
t('formato genérico também não cai com "inf"', gen_ok)

caminho_real = pathlib.Path.home() / 'Downloads' / 'RELAÇÃO UNIFORMES - ES - ESTOQUE - ATUALIZADA (1).xlsx'
if caminho_real.exists():
    real = importacao.ler_planilha(open(caminho_real, 'rb'), caminho_real.name)
    grupos = {}
    for l in real['linhas']:
        grupos[l['descricao']] = grupos.get(l['descricao'], 0) + l['quantidade'] + l['usados']
    t('planilha real: 675 peças (560 novas + 115 usadas) em 81 itens, iguais ao quadro de totais dela',
      (real['totais']['pecas'], real['totais']['novos'], real['totais']['usados'], len(real['linhas'])) == (675, 560, 115, 81)
      and grupos == {'CN Feminino': 278, 'CN Masculino': 282, 'GER Feminino': 51, 'GER Masculino': 64} and not real['avisos'],
      (real['totais'], len(real['linhas']), grupos, real['avisos']))
else:
    print('  (planilha real não está em ~/Downloads: conferência real pulada)')

print('exportação')
r = cg.get('/uniformes/exportar/')
wb = openpyxl.load_workbook(io.BytesIO(r.content))
linhas = list(wb.active.iter_rows(values_only=True))
t('exporta o estoque em xlsx', r['Content-Type'].startswith('application/vnd.openxmlformats') and linhas[0][0] == 'Tipo'
  and any(l[0] == 'Jaqueta' and l[3] == 5 for l in linhas[1:]), linhas[:3])
t('exportação do estoque tem Estoque novo, Quantidade usados e Com colaboradores (e o total)',
  list(linhas[0][3:7]) == ['Estoque novo', 'Quantidade usados', 'Com colaboradores', 'Total em estoque (novo + usados)']
  and any(l[0] == 'Camisa' and l[2] == 'CN Feminino' and l[1] == 'M' and l[3:7] == (9, 21, 0, 30) for l in linhas[1:]),
  [l for l in linhas if l[2] == 'CN Feminino'][:3])
r = cg.get(f'/uniformes/historico/exportar/?colaborador={gustavo.pk}&tipo=RETIRADA')
linhas = list(openpyxl.load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True))
t('exporta o histórico filtrado', len(linhas) == 4 and all(l[1] == 'Retirada' and 'Gustavo' in l[9] for l in linhas[1:]), linhas)
exp = importacao.ler_planilha(io.BytesIO(cg.get('/uniformes/exportar/').content), 'uniformes_estoque.xlsx')
linha_cm = next(l for l in exp['linhas'] if l['item_id'] == cm.pk)
t('a exportação do estoque relida: novas e usadas de volta, sugere acertar e avisa que somar dobra',
  (linha_cm['quantidade'], linha_cm['usados']) == (9, 21) and exp['modo_sugerido'] == importacao.MODO_ACERTAR
  and 'DOBRA' in exp['avisos'][0] and set(exp['colunas']) >= {'quantidade', 'usados'}, (linha_cm, exp['avisos'][:1], exp['colunas']))
movs_antes = M.objects.count()
res_exp = importacao.aplicar(exp['linhas'], modo=importacao.MODO_ACERTAR, por=gestora, nome_arquivo='uniformes_estoque.xlsx')
t('acertar com a própria exportação não muda nada', M.objects.count() == movs_antes and res_exp['ajustes'] == 0, res_exp)
t('histórico exportado traz a condição', linhas[0][5] == 'Condição' and linhas[0][8] == 'Saldo depois (da condição)'
  and all(l[5] == 'Novo' for l in linhas[1:]), linhas[0])
r = cg.get(f'/uniformes/historico/?colaborador={gustavo.pk}&tipo=DEVOLUCAO')
t('histórico filtra por colaborador e tipo', '1 lançamento' in r.content.decode())

print('novo e usado no mesmo item (14/11/2026)')
r = cg.post('/uniformes/item/novo/', {'tipo': 'Colete', 'tamanho': 'G', 'descricao': 'Vivo', 'quantidade': '3', 'condicao': 'NOVO'})
colete = ItemUniforme.objects.get(tipo='Colete', tamanho='G', descricao='Vivo')
cg.post('/uniformes/item/novo/', {'tipo': 'Colete', 'tamanho': 'g', 'descricao': 'Vivo - Usado', 'quantidade': '2'})
colete.refresh_from_db()
t('cadastrar "Vivo - Usado" não cria item: entra nos usados do mesmo item',
  ItemUniforme.objects.filter(tipo='Colete').count() == 1 and (colete.saldo, colete.saldo_usado) == (3, 2),
  list(ItemUniforme.objects.filter(tipo='Colete').values_list('descricao', 'saldo', 'saldo_usado')))
cg.post('/uniformes/entrada/', {'item': colete.pk, 'quantidade': '1', 'condicao': 'USADO', 'nota_fiscal': 'NF 9'})
colete.refresh_from_db()
ent_u = M.objects.filter(item=colete, tipo=M.ENTRADA).order_by('-id').first()
t('entrada de usadas soma só nos usados (saldo_apos é o dos usados)', (colete.saldo, colete.saldo_usado) == (3, 3)
  and ent_u.condicao == M.USADO and ent_u.saldo_apos == 3 and ent_u.nota_fiscal == 'NF 9')
cg.post('/uniformes/entrada/', {'item': colete.pk, 'quantidade': '1', 'condicao': 'quebrado'})
colete.refresh_from_db()
t('condição inválida é recusada', (colete.saldo, colete.saldo_usado) == (3, 3))
corpo = cg.get('/uniformes/?tipo=Colete').content.decode()
linha_colete = re.search(r'data-saldos-item="%d">(.*?)</div>\s*</div>\s*<div class="md:col-span-3' % colete.pk, corpo, re.S)
cab = re.search(r'data-colunas-estoque>(.*?)Ações', corpo, re.S)
t('lista do estoque: colunas Estoque novo, Quantidade usados e Com colaboradores', bool(cab)
  and cab.group(1).index('Estoque novo') < cab.group(1).index('Quantidade usados') < cab.group(1).index('Com colaboradores'))
t('lista do estoque: o item mostra 3 novas, 3 usadas e 0 com colaboradores', bool(linha_colete)
  and 'data-novo>3<' in linha_colete.group(1) and 'data-usado>3<' in linha_colete.group(1) and 'data-posse>0<' in linha_colete.group(1),
  linha_colete.group(1)[:400] if linha_colete else corpo[corpo.find('data-saldos-item'):][:400])
t('resumo do estoque separa novas e usadas', 'data-resumo="novos"' in corpo and 'data-resumo="usados"' in corpo
  and 'Quantidade usados' in corpo and 'Estoque novo' in corpo)

r = cg.post('/uniformes/retirada/', {'colaborador': gustavo.pk, 'item': [colete.pk, colete.pk], 'quantidade': ['1', '2'],
                                     'condicao': ['NOVO', 'USADO'], 'data_entrega': '2026-11-14'})
colete.refresh_from_db()
ret_c = {m.condicao: m for m in M.objects.filter(item=colete, tipo=M.RETIRADA)}
t('retirada por condição: nova sai das novas, usada das usadas', (colete.saldo, colete.saldo_usado) == (2, 1)
  and set(ret_c) == {M.NOVO, M.USADO} and ret_c[M.NOVO].saldo_apos == 2 and ret_c[M.USADO].saldo_apos == 1
  and ret_c[M.NOVO].lote == ret_c[M.USADO].lote, ((colete.saldo, colete.saldo_usado), [(m.condicao, m.saldo_apos) for m in ret_c.values()]))
termo_c = cg.get(f'/uniformes/termo/{ret_c[M.NOVO].lote}/').content.decode()
t('termo mostra a condição de cada peça', '<th>Condição</th>' in termo_c and 'data-condicao="USADO">Usada<' in termo_c
  and 'data-condicao="NOVO">Nova<' in termo_c and 'novos ou usados' in termo_c)
r = cg.post('/uniformes/retirada/', {'colaborador': gustavo.pk, 'item': [colete.pk], 'quantidade': ['2'], 'condicao': ['USADO']})
colete.refresh_from_db()
t('retirada de usadas sem saldo de usadas é recusada (mesmo com novas sobrando)', (colete.saldo, colete.saldo_usado) == (2, 1)
  and M.objects.filter(item=colete, tipo=M.RETIRADA).count() == 2)
try:
    servicos.registrar_retirada(linhas=[(colete.pk, 2, 'USADO')], colaborador=gustavo, por=chefe)
    msg = ''
except ErroEstoque as exc:
    msg = str(exc)
t('o aviso diz que faltam usadas', 'usada' in msg and 'há 1' in msg, msg)
cg.post('/uniformes/retirada/', {'colaborador': gustavo.pk, 'item': [colete.pk], 'quantidade': ['1']})
colete.refresh_from_db()
t('retirada sem condição tira das novas', (colete.saldo, colete.saldo_usado) == (1, 1))
try:
    with transaction.atomic():
        ItemUniforme.objects.filter(pk=colete.pk).update(saldo_usado=-1)
    barrou = False
except IntegrityError:
    barrou = True
t('o banco também barra usados negativo', barrou)
r = cg.get('/uniformes/retirada/')
t('tela de retirada leva novas e usadas de cada item e o seletor de condição',
  '"saldo_usado": 1' in r.content.decode() and "cond.name = 'condicao'" in r.content.decode())

print('devolução por condição')
t('posse do colete: 4 (2 novas + 2 usadas entregues)', servicos.posse(gustavo, [colete.pk]) == {colete.pk: 4})
r = cg.post('/uniformes/devolucao/', {'colaborador': gustavo.pk, 'item': [colete.pk, colete.pk], 'quantidade': ['3', '2'],
                                      'condicao': ['USADO', 'NOVO']})
colete.refresh_from_db()
t('devolver mais que a posse somando as condições é recusado', (colete.saldo, colete.saldo_usado) == (1, 1)
  and not M.objects.filter(item=colete, tipo=M.DEVOLUCAO).exists())
r = cg.post('/uniformes/devolucao/', {'colaborador': gustavo.pk, 'item': [colete.pk], 'quantidade': ['2']})
colete.refresh_from_db()
t('devolução sem escolher volta como usada', (colete.saldo, colete.saldo_usado) == (1, 3)
  and M.objects.filter(item=colete, tipo=M.DEVOLUCAO, condicao=M.USADO, saldo_apos=3).exists())
cg.post('/uniformes/devolucao/', {'colaborador': gustavo.pk, 'item': [colete.pk], 'quantidade': ['1'], 'condicao': ['NOVO']})
colete.refresh_from_db()
t('devolução pode voltar como nova se escolhida', (colete.saldo, colete.saldo_usado) == (2, 3)
  and servicos.posse(gustavo, [colete.pk]) == {colete.pk: 1})
corpo = cg.get(f'/uniformes/devolucao/?colaborador={gustavo.pk}').content.decode()
t('tela de devolução já vem com "Volta usada" marcado', '<option value="USADO" selected>Volta usada</option>' in corpo
  and 'name="condicao"' in corpo)

print('ajuste por condição')
cg.post('/uniformes/ajuste/', {'item': colete.pk, 'novos': '5', 'usados': '0', 'motivo': 'inventário de novembro'})
colete.refresh_from_db()
aj_c = {m.condicao: m for m in M.objects.filter(item=colete, tipo=M.AJUSTE)}
t('inventário com novas e usadas: um ajuste por condição', (colete.saldo, colete.saldo_usado) == (5, 0)
  and aj_c[M.NOVO].delta == 3 and aj_c[M.NOVO].saldo_apos == 5 and aj_c[M.USADO].delta == -3 and aj_c[M.USADO].saldo_apos == 0,
  [(m.condicao, m.delta, m.saldo_apos) for m in aj_c.values()])
n = M.objects.filter(item=colete).count()
cg.post('/uniformes/ajuste/', {'item': colete.pk, 'novos': '5', 'usados': '0', 'motivo': 'de novo'})
cg.post('/uniformes/ajuste/', {'item': colete.pk, 'novos': '5', 'usados': '-2', 'motivo': 'x'})
cg.post('/uniformes/ajuste/', {'item': colete.pk, 'novos': '6', 'usados': '1', 'motivo': ''})
colete.refresh_from_db()
t('sem mudança, contagem negativa ou sem motivo: nada lançado', M.objects.filter(item=colete).count() == n
  and (colete.saldo, colete.saldo_usado) == (5, 0))
cg.post('/uniformes/ajuste/', {'item': colete.pk, 'novos': '', 'usados': '2', 'motivo': 'achei 2 usadas'})
colete.refresh_from_db()
t('só os usados contados: as novas não mudam', (colete.saldo, colete.saldo_usado) == (5, 2)
  and M.objects.filter(item=colete).count() == n + 1)
cg.post('/uniformes/ajuste/', {'item': colete.pk, 'contagem': '1', 'condicao': 'USADO', 'motivo': 'peça rasgada'})
colete.refresh_from_db()
t('ajuste de uma condição só (contagem + condição) continua valendo', (colete.saldo, colete.saldo_usado) == (5, 1))
corpo = cg.get('/uniformes/').content.decode()
t('janela de ajuste pede novas e usadas contadas', 'name="novos"' in corpo and 'name="usados"' in corpo
  and f'data-novos="5" data-usados="1"' in corpo)
t('janela de entrada escolhe novas ou usadas', 'name="condicao" value="USADO"' in corpo)


def livro_bate():
    erros = []
    for item in ItemUniforme.objects.all():
        for condicao, saldo in ((M.NOVO, item.saldo), (M.USADO, item.saldo_usado)):
            seq, soma = list(M.objects.filter(item=item, condicao=condicao).order_by('registrado_em', 'id')), 0
            for mov in seq:
                soma += mov.delta
                if mov.saldo_apos != soma:
                    erros.append((item.nome, condicao, mov.pk, mov.saldo_apos, soma))
            if soma != saldo:
                erros.append((item.nome, condicao, 'saldo', saldo, soma))
    return erros


t('em todo item, cada saldo = soma do livro daquela condição (e saldo_apos em sequência)', not livro_bate(), livro_bate())

print('histórico, ficha e termo com a condição')
corpo = cg.get('/uniformes/historico/?condicao=USADO&item=%d' % colete.pk).content.decode()
t('histórico filtra por condição e mostra a etiqueta', 'data-condicao="USADO"' in corpo and 'data-condicao="NOVO"' not in corpo
  and '→ 1 usada' in corpo, re.findall(r'→ \d+ \w+', corpo))
corpo = cg.get(f'/uniformes/colaborador/{gustavo.pk}/').content.decode()
t('tela do colaborador mostra se a peça entregue era nova ou usada', '(usadas)' in corpo and '(nova)' in corpo,
  re.findall(r'\((?:nova|usada)s?\)', corpo))
corpo = cs.get(f'/users/manage/users/{gustavo.pk}/profile/').content.decode()
t('ficha do colaborador (perfil) mostra a condição das entregas', 'data-condicao="USADO"' in corpo)

print('migração de dados: junta os itens "· Usado" / "- Usado" (0004)')
import importlib  # noqa: E402
mig = importlib.import_module('uniformes.migrations.0004_juntar_itens_usados')
t('sufixo reconhecido sem diferença de maiúscula, acento e espaço',
  [mig.separar_usado(x) for x in ('CN Feminino · Usado', 'CN Feminino - Usado', 'cn  feminino -usado', 'Usado', 'Polo Usádo',
                                  'Abusado', 'CN Feminino')]
  == [('CN Feminino', True), ('CN Feminino', True), ('cn feminino', True), ('', True), ('Polo', True),
      ('Abusado', False), ('CN Feminino', False)])
t('a tela usa a mesma regra do sufixo', all(servicos.separar_usado(x) == mig.separar_usado(x)
                                            for x in ('CN Feminino · Usado', 'X - usados', 'Abusado', 'Polo Usádo')))


def velho(tipo, descricao, tamanho, *lancamentos):
    """Item como o código antigo criava (usado à parte), com o livro dele."""
    item = ItemUniforme.objects.create(tipo=tipo, descricao=descricao, tamanho=tamanho, criado_por=chefe)
    for qtd, autor, obs in lancamentos:
        if qtd > 0:
            servicos.registrar_entrada(item_id=item.pk, quantidade=qtd, por=autor, observacao=obs,
                                       data=_date(2026, 11, 12))
        else:
            servicos.registrar_ajuste(item_id=item.pk, contagem=ItemUniforme.objects.get(pk=item.pk).saldo + qtd,
                                      por=autor, motivo=obs)
    return item


base_m = velho('Calça', 'ZZ Mig', '40', (5, chefe, 'novas'))
u1 = velho('Calça', 'ZZ Mig · Usado', '40', (3, gestora, 'usadas A'), (-1, chefe, 'uma rasgou'))
u2 = velho('Calça', 'zz mig - usado', '40', (4, gestora, 'usadas B'))
sem_base = velho('Jaqueta', 'ZZ Sem Base - Usado', 'M', (2, gestora, 'só usadas'))
so_usado = velho('Boné', 'Usado', 'U', (1, chefe, 'boné usado'))
dupla_a = velho('Camisa', 'ZZ Dupla · Usado', 'G', (2, chefe, 'a'))
dupla_b = velho('Camisa', 'ZZ Dupla - Usado', 'G', (5, gestora, 'b'))
movs_velhos = {m.pk: (m.registrado_por_id, m.registrado_em, m.observacao, m.data, m.delta)
               for m in M.objects.filter(item__in=[u1, u2, sem_base, so_usado, dupla_a, dupla_b])}


def totais_por_peca():
    soma = {}
    for i in ItemUniforme.objects.all():
        base, _ = mig.separar_usado(i.descricao)
        chave = mig._chave(i.tipo, i.tamanho, base)
        soma[chave] = soma.get(chave, 0) + i.saldo + i.saldo_usado
    return soma


antes_tot, antes_ids = totais_por_peca(), set(M.objects.values_list('id', flat=True))
rel = mig.juntar(ItemUniforme, M)
base_m.refresh_from_db()
t('junta no base: novas ficam, usadas somam (2 + 4) e os itens usados somem', (base_m.saldo, base_m.saldo_usado) == (5, 6)
  and not ItemUniforme.objects.filter(pk__in=[u1.pk, u2.pk]).exists(), (base_m.saldo, base_m.saldo_usado))
movs_base = list(M.objects.filter(item=base_m, condicao=M.USADO).order_by('registrado_em', 'id'))
t('movimentos do usado vão para o base como USADO, saldo_apos refeito na ordem', [m.saldo_apos for m in movs_base] == [3, 2, 6]
  and M.objects.filter(item=base_m, condicao=M.NOVO).count() == 1, [(m.delta, m.saldo_apos) for m in movs_base])
t('datas, autores, observações e deltas preservados', all(
    (m.registrado_por_id, m.registrado_em, m.observacao, m.data, m.delta) == movs_velhos[m.pk]
    for m in M.objects.filter(pk__in=movs_velhos)))
sem_base.refresh_from_db(); so_usado.refresh_from_db()
t('sem base: o próprio item vira a peça (sem sufixo, tudo em usados)', (sem_base.descricao, sem_base.saldo, sem_base.saldo_usado)
  == ('ZZ Sem Base', 0, 2) and (so_usado.descricao, so_usado.saldo, so_usado.saldo_usado) == ('', 0, 1)
  and set(M.objects.filter(item=sem_base).values_list('condicao', flat=True)) == {M.USADO})
dupla = ItemUniforme.objects.filter(tipo='Camisa', tamanho='G', descricao='ZZ Dupla')
t('dois usados da mesma peça sem base viram um item só', dupla.count() == 1 and dupla[0].saldo_usado == 7
  and not ItemUniforme.objects.filter(pk=dupla_b.pk).exists() and dupla[0].pk == dupla_a.pk,
  list(ItemUniforme.objects.filter(tipo='Camisa', tamanho='G').values_list('descricao', 'saldo', 'saldo_usado')))
t('relatório da junção', (rel['juntados'], rel['transformados']) == (3, 3), rel)
t('nenhum movimento perdido e totais por peça iguais', set(M.objects.values_list('id', flat=True)) == antes_ids
  and totais_por_peca() == antes_tot)
t('outros movimentos continuam NOVO', set(M.objects.filter(item=base_m, delta=5).values_list('condicao', flat=True)) == {M.NOVO})
t('nenhum item com o sufixo sobrou', not any(mig.separar_usado(d)[1] for d in ItemUniforme.objects.values_list('descricao', flat=True)))
t('livro bate depois da junção', not livro_bate(), livro_bate())
foto = (sorted(ItemUniforme.objects.values_list('id', 'descricao', 'saldo', 'saldo_usado')),
        sorted(M.objects.values_list('id', 'item_id', 'condicao', 'saldo_apos')))
rel2 = mig.juntar(ItemUniforme, M)
t('rodar de novo não muda nada (idempotente)', (rel2['juntados'], rel2['transformados'], rel2['movimentos']) == (0, 0, 0)
  and foto == (sorted(ItemUniforme.objects.values_list('id', 'descricao', 'saldo', 'saldo_usado')),
               sorted(M.objects.values_list('id', 'item_id', 'condicao', 'saldo_apos'))))
mig.separar(ItemUniforme, M)
t('a volta separa os usados em itens "· Usado" de novo', ItemUniforme.objects.filter(descricao='ZZ Mig · Usado', saldo=6).exists()
  and ItemUniforme.objects.get(pk=sem_base.pk).descricao == 'ZZ Sem Base · Usado'
  and not ItemUniforme.objects.filter(saldo_usado__gt=0).exists() and not M.objects.filter(condicao=M.USADO).exists()
  and totais_por_peca() == antes_tot and not livro_bate(), livro_bate())
mig.juntar(ItemUniforme, M)
t('ida → volta → ida dá o mesmo estado', foto == (sorted(ItemUniforme.objects.values_list('id', 'descricao', 'saldo', 'saldo_usado')),
                                                  sorted(M.objects.values_list('id', 'item_id', 'condicao', 'saldo_apos'))))
inconsistente = velho('Calça', 'ZZ Ruim · Usado', '42', (2, chefe, 'x'))
ItemUniforme.objects.filter(pk=inconsistente.pk).update(saldo=5)    # saldo que o livro não explica
try:
    with transaction.atomic():
        mig.juntar(ItemUniforme, M)
    parou = False
except RuntimeError:
    parou = True
t('livro que não bate para a migração e nada muda', parou and ItemUniforme.objects.get(pk=inconsistente.pk).descricao == 'ZZ Ruim · Usado')
M.objects.filter(item=inconsistente).delete(); inconsistente.delete()

print('tema escuro e 375 px nas telas mexidas')
User.objects.filter(pk=gestora.pk).update(theme='dark')
cg.post('/uniformes/importar/', {'acao': 'ler', 'planilha': SimpleUploadedFile('conf.xlsx', conferencia().getvalue())})
TELAS = ['/uniformes/', '/uniformes/retirada/', f'/uniformes/devolucao/?colaborador={gustavo.pk}', '/uniformes/historico/',
         '/uniformes/importar/', f'/uniformes/colaborador/{gustavo.pk}/']
escuras = {u: cg.get(u) for u in TELAS}
t('telas abrem no tema escuro', all(r.status_code == 200 and '<html lang="pt-br" class="dark">' in r.content.decode()
                                    for r in escuras.values()), {u: r.status_code for u, r in escuras.items()})
cg.post('/uniformes/importar/', {'acao': 'cancelar'})
User.objects.filter(pk=gestora.pk).update(theme='light')
css_escuro = _pl.Path('static/css/tema-escuro.css').read_text()
cobertas = set(re.findall(r'\.((?:bg|text|border)-[a-z]+-\d+)', css_escuro)) | {'bg-white'}
sem_tema = {}
for nome in ('estoque.html', 'retirada.html', 'devolucao.html', '_movimento_linha.html', 'colaborador.html', '_ficha.html',
             'historico.html', 'importar.html'):
    fonte = (_pl.Path('templates/uniformes') / nome).read_text()
    for classe in set(re.findall(r'(?<![\w:-])((?:bg|text|border)-(?:gray|amber|emerald|sky|violet|red)-\d+)', fonte)):
        solida = classe.startswith('bg-') and int(classe.rsplit('-', 1)[1]) >= 600   # botão cheio com texto branco
        if classe not in cobertas and not solida:
            sem_tema.setdefault(nome, []).append(classe)
t('cores claras das telas mexidas têm par no tema escuro', not sem_tema, sem_tema)
largas = {}
for nome in ('estoque.html', 'retirada.html', 'devolucao.html', 'historico.html', 'importar.html', '_movimento_linha.html'):
    fonte = (_pl.Path('templates/uniformes') / nome).read_text()
    for w in re.findall(r'(?<![\w:-])(?:min-)?w-(\d+)\b', fonte):
        if int(w) * 4 > 200:
            largas.setdefault(nome, []).append(w)
    for px in re.findall(r'(?<![\w:-])(?:min-)?w-\[(\d+)px\]', fonte):
        largas.setdefault(nome, []).append(px + 'px')
t('375 px: nenhuma largura fixa maior que 200 px fora de breakpoint', not largas, largas)
fonte_ret = _pl.Path('templates/uniformes/retirada.html').read_text()
t('375 px: na retirada a peça ocupa a linha toda e condição/quantidade vão embaixo',
  "'flex flex-wrap sm:flex-nowrap gap-2 items-center'" in fonte_ret and "'basis-full sm:basis-auto" in fonte_ret)
fonte_est = _pl.Path('templates/uniformes/estoque.html').read_text()
t('375 px: no estoque os 3 números ficam lado a lado com rótulo próprio',
  'md:col-span-6 grid grid-cols-3' in fonte_est and fonte_est.count('md:hidden block text-[11px]') == 3)
cg.post('/uniformes/importar/', {'acao': 'ler', 'planilha': SimpleUploadedFile('conf.xlsx', conferencia().getvalue())})
html = cg.get('/uniformes/importar/').content.decode()
cg.post('/uniformes/importar/', {'acao': 'cancelar'})
thead = re.search(r'<thead.*?</thead>', html, re.S).group(0)
visiveis = [th for th in re.findall(r'<th class="([^"]*)"', thead) if 'hidden' not in th.split()]
t('375 px: a prévia mostra 5 colunas no celular (tipo, tam., novos, usados, hoje)', len(visiveis) == 5, visiveis)
fonte_dev = _pl.Path('templates/uniformes/devolucao.html').read_text()
t('375 px: na devolução o nome vai em cima e condição/quantidade embaixo', 'basis-full sm:basis-auto sm:flex-1' in fonte_dev)

print('higiene dos templates')
pasta = _pl.Path('templates/uniformes')
ruins = []
for arq in list(pasta.glob('*.html')) + [_pl.Path('templates/admin/user_profile.html'), _pl.Path('templates/users/profile.html'),
                                         _pl.Path('templates/admin/user_registration_form.html')]:
    for n, linha in enumerate(arq.read_text().splitlines(), 1):
        if '{#' in linha and '#}' not in linha:
            ruins.append(f'{arq}:{n}')
t('comentário {# #} sempre em uma linha', not ruins, ruins)
vazou = []
for u in ['/uniformes/', '/uniformes/historico/', '/uniformes/retirada/', f'/uniformes/devolucao/?colaborador={gustavo.pk}',
          '/uniformes/importar/', '/uniformes/acessos/', f'/uniformes/colaborador/{gustavo.pk}/', f'/uniformes/termo/{ret.lote}/',
          f'/users/manage/users/{gustavo.pk}/profile/', f'/users/manage/users/{gustavo.pk}/profile/print/']:
    corpo = cs.get(u).content.decode()
    if '{#' in corpo or '{% comment' in corpo or '#}' in corpo:
        vazou.append(u)
t('nenhum comentário de template vaza na tela', not vazou, vazou)

print(f'\n{ok} OK, {fail} falha(s)')
