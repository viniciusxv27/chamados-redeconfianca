"""Uniformes (10/11/2026): estoque, retirada, devolução, ajuste, ficha e acessos. Banco descartável; nada sai para a rede."""
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
t('devolução volta ao estoque com quem e quando', calca.saldo == 2 and dev.colaborador == gustavo and dev.registrado_por == gestora
  and dev.data == timezone.localdate() and dev.delta == 1)
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

print('exportação')
r = cg.get('/uniformes/exportar/')
wb = openpyxl.load_workbook(io.BytesIO(r.content))
linhas = list(wb.active.iter_rows(values_only=True))
t('exporta o estoque em xlsx', r['Content-Type'].startswith('application/vnd.openxmlformats') and linhas[0][0] == 'Tipo'
  and any(l[0] == 'Jaqueta' and l[3] == 5 for l in linhas[1:]), linhas[:3])
r = cg.get(f'/uniformes/historico/exportar/?colaborador={gustavo.pk}&tipo=RETIRADA')
linhas = list(openpyxl.load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True))
t('exporta o histórico filtrado', len(linhas) == 4 and all(l[1] == 'Retirada' and 'Gustavo' in l[8] for l in linhas[1:]), linhas)
r = cg.get(f'/uniformes/historico/?colaborador={gustavo.pk}&tipo=DEVOLUCAO')
t('histórico filtra por colaborador e tipo', '1 lançamento' in r.content.decode())

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
