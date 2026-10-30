"""/financeiro/fechamento/ (30/10/2026): Fechamento de caixa.

Pedido: entradas, sangrias, contagem do caixa físico, AgoraCred e quantidade de Renova Allied
por loja e dia; o dia fica aberto até 10h do dia seguinte; botão que bloqueia a alteração depois
do prazo; só o SUPERADMIN vê de saída e libera pessoa a pessoa como Gestor (todas as lojas) ou
Visualizador (uma loja).

Cobre acesso e recorte, prazo (antes/depois das 10h, com a trava ligada e desligada), bloqueio
manual do dia, as sugestões que vêm da Contagem de Caixa / Sangrias / vendas, validação dos
campos, a rastreabilidade e o painel que manda direto para o fechamento quem só tem ele.

Roda num sqlite descartável; o relógio é falso (django.utils.timezone.now).
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
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-fin-fech-'))
(PASTA / 'zz_fin_fech_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ffch'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-ffch-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_fin_fech_settings'
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
from django.utils import timezone

from contagem_caixa.models import CategoriaSangria, ContagemCaixaDia, Sangria
from financeiro.models import AcessoFechamento, ConfiguracaoFechamento, FechamentoCaixa, LogFinanceiro
from users.models import Sector
from vendas.models import Venda, VendaProduto

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

TZ = timezone.get_current_timezone()
DIA = dt.date(2026, 10, 20)


def relogio(dia, hora, minuto=0):
    return mock.patch('django.utils.timezone.now', return_value=dt.datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=TZ))


gloria = Sector.objects.create(name='Loja Glória')
serra = Sector.objects.create(name='Loja Serra Sede')
escritorio = Sector.objects.create(name='Financeiro')


def usuario(nome, hierarquia='PADRAO', setor=escritorio):
    u = User.objects.create_user(username=f'zzff.{nome}', email=f'zzff.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZFF', last_name=nome, sector=setor, hierarchy=hierarquia)
    c = Client(); c.force_login(u)
    return u, c


chefe, cs = usuario('super', 'SUPERADMIN')
gestor, cg = usuario('gestor')
vis, cv = usuario('vis', setor=gloria)
nada, cn = usuario('nada')


def campos(loja, dia=DIA, **extra):
    d = {'loja': loja.id, 'data': dia.isoformat(), 'entradas': '1.234,56', 'sangrias': '100', 'caixa_fisico': '1134,56',
         'agoracred': '50,00', 'renova_allied_qtd': '2', 'renova_vini_valor': '1.150,00', 'renova_vini_qtd': '3',
         'observacao': 'ok'}
    d.update(extra)
    return d


print('Acesso')
with relogio(DIA + dt.timedelta(days=1), 9):
    t('sem liberação: não entra e não vê o Financeiro', cn.get('/financeiro/fechamento/').status_code == 302
      and 'href="/financeiro/"' not in cn.get('/', follow=True).content.decode())
    r = cs.get('/financeiro/fechamento/')
    h = r.content.decode()
    t('SUPERADMIN vê todas as lojas, já no dia anterior', r.status_code == 200 and 'Loja Glória' in h and 'Loja Serra Sede' in h
      and 'value="2026-10-20"' in h, r.status_code)
    t('a aba aparece no Financeiro', 'Fechamento de caixa</span>' in cs.get('/financeiro/').content.decode())

    r = cs.post('/financeiro/fechamento/acessos/', {'acao': 'adicionar', 'user': vis.id, 'papel': 'VISUALIZADOR'})
    t('visualizador sem loja é recusado', not AcessoFechamento.objects.filter(user=vis).exists())
    cs.post('/financeiro/fechamento/acessos/', {'acao': 'adicionar', 'user': vis.id, 'papel': 'VISUALIZADOR', 'loja': gloria.id})
    cs.post('/financeiro/fechamento/acessos/', {'acao': 'adicionar', 'user': gestor.id, 'papel': 'GESTOR', 'loja': gloria.id})
    t('SUPERADMIN libera visualizador (uma loja) e gestor (sem loja)',
      AcessoFechamento.objects.get(user=vis).loja_id == gloria.id and AcessoFechamento.objects.get(user=gestor).loja_id is None)
    t('acessos: só o SUPERADMIN', cg.get('/financeiro/fechamento/acessos/').status_code == 302)
    t('liberar fica na rastreabilidade', LogFinanceiro.objects.filter(acao='ACESSO', area='fechamento').count() == 2)

    t('quem só tem o fechamento entra pelo menu e cai nele', cv.get('/financeiro/').url == '/financeiro/fechamento/'
      and 'href="/financeiro/"' in cv.get('/rotina-adm/', follow=True).content.decode())
    h = cv.get('/financeiro/fechamento/').content.decode()
    t('visualizador vê só a loja dele, sem formulário', 'Loja Glória' in h and 'Loja Serra Sede' not in h
      and 'name="entradas"' not in h and 'value="bloquear"' not in h)
    t('e não vê outras partes do Financeiro', cv.get('/financeiro/pagar/').status_code == 302)
    r = cv.post('/financeiro/fechamento/salvar/', campos(gloria))
    t('visualizador não grava', not FechamentoCaixa.objects.exists())
    h = cg.get('/financeiro/fechamento/').content.decode()
    t('gestor vê todas e tem o formulário', 'Loja Serra Sede' in h and h.count('name="entradas"') == 2)

print('Sugestões do sistema')
cat = CategoriaSangria.objects.create(nome='ZZ Cat')
cx = ContagemCaixaDia.objects.create(loja=gloria, data=DIA, valor_sap=D('1500'), agoracred=D('40'), valor_real=D('1300'))
Sangria.objects.create(loja=gloria, data=DIA, valor=D('80'), categoria=cat, descricao='x', registrada_por=chefe)
cx.refresh_from_db()
v = Venda.objects.create(data_venda=dt.datetime(2026, 10, 20, 15, tzinfo=TZ), loja=gloria)
VendaProduto.objects.create(venda=v, nome_produto='A', renova_alied=True)
VendaProduto.objects.create(venda=v, nome_produto='B', renova_alied=True)
VendaProduto.objects.create(venda=v, nome_produto='C')
VendaProduto.objects.create(venda=v, nome_produto='E', renova_vini=True, renova_vini_valor=D('400'))
VendaProduto.objects.create(venda=v, nome_produto='F', renova_vini=True, renova_vini_valor=D('250.50'))
v2 = Venda.objects.create(data_venda=dt.datetime(2026, 10, 21, 1, tzinfo=TZ), loja=gloria)
VendaProduto.objects.create(venda=v2, nome_produto='D', renova_alied=True)
from financeiro import fechamento as fch
sug = fch.sugestoes([gloria], DIA)[gloria.id]
t('entradas e sangrias vêm da Contagem de Caixa', sug['entradas'] == cx.entrada and sug['sangrias'] == cx.sangrias_total, sug)
t('caixa físico = valor real; AgoraCred da contagem', sug['caixa_fisico'] == D('1300') and sug['agoracred'] == D('40'))
t('Renova Allied conta os itens do dia (o do dia seguinte fica fora)', sug['renova_allied_qtd'] == 2, sug)
t('Renova Vini: quantidade e soma do valor das vendas do dia', sug['renova_vini_qtd'] == 2 and sug['renova_vini_valor'] == D('650.50'), sug)
with relogio(DIA + dt.timedelta(days=1), 9):
    h = cg.get('/financeiro/fechamento/').content.decode()
t('a tela mostra as sugestões para usar', 'Valor real: R$ 1.300,00' in h and 'Vendas: 2' in h and 'Usar valores do sistema' in h
  and 'Vendas: R$ 650,50' in h and 'name="renova_vini_qtd"' in h,
  h[h.find('fch-sug'):h.find('fch-sug') + 200])

print('Prazo')
with relogio(DIA + dt.timedelta(days=1), 9, 59):
    r = cg.post('/financeiro/fechamento/salvar/', campos(gloria))
f = FechamentoCaixa.objects.filter(loja=gloria, data=DIA).first()
t('às 9h59 do dia seguinte o gestor grava', f is not None)
t('valores lidos no formato brasileiro', f and f.entradas == D('1234.56') and f.caixa_fisico == D('1134.56')
  and f.agoracred == D('50') and f.renova_allied_qtd == 2 and f.atualizado_por == gestor
  and f.renova_vini_valor == D('1150') and f.renova_vini_qtd == 3)
t('gravar fica na rastreabilidade', LogFinanceiro.objects.filter(area='fechamento', acao='CRIAR', objeto_id=str(f.pk)).exists())
with relogio(DIA + dt.timedelta(days=1), 10, 1):
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, entradas='999'))
    t('às 10h01 o gestor não altera mais', FechamentoCaixa.objects.get(pk=f.pk).entradas == D('1234.56'))
    h = cg.get('/financeiro/fechamento/').content.decode()
    t('a tela diz que o prazo acabou e some o formulário', 'Prazo encerrado' in h and h.count('name="entradas"') == 0)
    cs.post('/financeiro/fechamento/salvar/', campos(gloria, entradas='999'))
    t('o SUPERADMIN ainda altera', FechamentoCaixa.objects.get(pk=f.pk).entradas == D('999'))
    t('editar registra o antes e o depois', LogFinanceiro.objects.filter(area='fechamento', acao='EDITAR',
                                                                         antes__entradas='1234.56', depois__entradas='999.00').exists())
    cg.post('/financeiro/fechamento/regra/', {'travar_apos_prazo': '0'})
    t('só o SUPERADMIN mexe no botão do prazo', ConfiguracaoFechamento.atual().travar_apos_prazo is True)
    cs.post('/financeiro/fechamento/regra/', {'travar_apos_prazo': '0'})
    t('SUPERADMIN desliga o bloqueio depois do prazo', ConfiguracaoFechamento.atual().travar_apos_prazo is False)
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, entradas='888'))
    t('desligado, o gestor altera depois das 10h', FechamentoCaixa.objects.get(pk=f.pk).entradas == D('888'))
    cs.post('/financeiro/fechamento/regra/', {'travar_apos_prazo': '1', 'hora_limite': '12:00'})
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, entradas='777'))
    t('hora limite mudada para 12h: às 10h01 ainda altera', FechamentoCaixa.objects.get(pk=f.pk).entradas == D('777'))
    cs.post('/financeiro/fechamento/regra/', {'hora_limite': '10:00'})

print('Bloqueio do dia')
with relogio(DIA + dt.timedelta(days=1), 8):
    cg.post('/financeiro/fechamento/bloquear/', {'loja': gloria.id, 'data': DIA.isoformat(), 'acao': 'bloquear'})
    f.refresh_from_db()
    t('gestor bloqueia antes do prazo', f.bloqueado and f.bloqueado_por == gestor)
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, entradas='1'))
    t('bloqueado, o gestor não altera', FechamentoCaixa.objects.get(pk=f.pk).entradas == D('777'))
    t('visualizador não bloqueia', cv.post('/financeiro/fechamento/bloquear/', {'loja': gloria.id, 'data': DIA.isoformat(),
                                                                                'acao': 'desbloquear'}) and FechamentoCaixa.objects.get(pk=f.pk).bloqueado)
    h = cv.get('/financeiro/fechamento/').content.decode()
    t('visualizador vê o bloqueio e os valores', 'Bloqueado' in h and 'R$ 777,00' in h)
    cg.post('/financeiro/fechamento/bloquear/', {'loja': gloria.id, 'data': DIA.isoformat(), 'acao': 'desbloquear'})
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, entradas='2'))
    t('desbloqueado (no prazo), volta a alterar', FechamentoCaixa.objects.get(pk=f.pk).entradas == D('2'))
    t('bloquear e desbloquear ficam registrados', LogFinanceiro.objects.filter(area='fechamento', acao__in=('BLOQUEAR', 'DESBLOQUEAR')).count() == 2)
    cg.post('/financeiro/fechamento/bloquear/', {'loja': serra.id, 'data': DIA.isoformat(), 'acao': 'bloquear'})
    t('bloquear loja sem fechamento cria o dia bloqueado', FechamentoCaixa.objects.get(loja=serra, data=DIA).bloqueado)

print('Validação e dia futuro')
with relogio(DIA, 18):
    cg.post('/financeiro/fechamento/salvar/', campos(serra, dia=DIA + dt.timedelta(days=1)))
    t('dia que ainda não chegou não grava', not FechamentoCaixa.objects.filter(data=DIA + dt.timedelta(days=1)).exists())
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, renova_allied_qtd='2,5'))
    t('Allied quebrado é recusado', FechamentoCaixa.objects.get(pk=f.pk).renova_allied_qtd == 2)
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, renova_vini_qtd='-1'))
    t('quantidade de Vini negativa é recusada', FechamentoCaixa.objects.get(pk=f.pk).renova_vini_qtd == 3)
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, entradas='abc'))
    t('valor que não é número é recusado', FechamentoCaixa.objects.get(pk=f.pk).entradas == D('2'))
    cg.post('/financeiro/fechamento/salvar/', campos(gloria, caixa_fisico=''))
    t('caixa físico vazio fica vazio (não vira zero)', FechamentoCaixa.objects.get(pk=f.pk).caixa_fisico is None)
    r = cg.get('/financeiro/fechamento/', {'data': '2026-12-31'})
    t('pedir dia futuro abre no dia de hoje', 'value="2026-10-20"' in r.content.decode())

print('Visualizador: histórico e remoção')
with relogio(DIA + dt.timedelta(days=3), 9):
    h = cv.get('/financeiro/fechamento/').content.decode()
    t('visualizador vê os últimos fechamentos da loja', 'Últimos fechamentos · Loja Glória' in h and '20/10/2026' in h
      and 'Vini (qtd.)' in h and 'R$ 1.150,00' in h)
# O mesmo relógio do resto: a sessão do portal cai se o relógio "pula" dias (rastreio de sessão ativa).
with relogio(DIA + dt.timedelta(days=3), 9, 5):
    cs.post('/financeiro/fechamento/acessos/', {'acao': 'remover', 'acesso': AcessoFechamento.objects.get(user=vis).id})
    v2c = Client(); v2c.force_login(User.objects.get(pk=vis.pk))
    r = v2c.get('/financeiro/fechamento/')
t('tirar o acesso tira o Financeiro do visualizador', r.status_code == 302, (r.status_code, AcessoFechamento.objects.filter(user=vis).exists()))

print(f'\n{ok} OK, {fail} falha(s)')
