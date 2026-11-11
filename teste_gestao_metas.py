"""Gerenciamento de metas (10/11/2026): acesso, planilha antiga, fórmulas, Fixa, competência nova, importações e Excel.

Banco descartável (sqlite montado dos models); o MySQL de vendas é falso. A planilha de novembro/2026 fica no
scratchpad da sessão — sem ela, os testes que dependem dela são pulados e o resto roda com planilhas sintéticas.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-gestao-metas-'))
(PASTA / 'zz_gestao_metas_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-gm'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-gm-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_gestao_metas_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import io
import json
import math
import re
import warnings
from datetime import date

import openpyxl
import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from communications.models import CommunicationGroup
from gestao_metas import importacao, servicos
from gestao_metas.models import (AcessoGestaoMetas, Competencia, HistoricoMeta, ImportacaoMetas, LojaMeta, MetaLoja,
                                 Realizado)
from users.models import Sector

warnings.filterwarnings('ignore')
User = get_user_model()
ok = fail = pulados = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print('  OK   ' + nome)
    else:
        fail += 1
        print('  FALHA ' + nome + ' ' + str(extra))


def perto(a, b, tol=0.01):
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()

PLANILHA = pathlib.Path('/private/tmp/claude-501/-Users-teste-Downloads-chamados-redeconfianca/'
                        'ce7e9f64-a0a6-48dc-89fe-2170d0758a23/scratchpad/planilha/metas.xlsx')

# Setores das lojas, com o código ADABAS que dá a região (como no portal).
RJ = {'Quissama', 'Sao Fidelis', 'Miracema', 'Bom Jesus do Itabapoana', 'Santo Antonio de Padua'}
SP = {'Itaim Paulista', 'Embu das Artes', 'Carapicuíba'}
NOMES = ['Masterplace', 'Marcilio De Noronha', 'Itaciba', 'Montserrat', 'Porto Canoa', 'Jacaraípe', 'Laranjeiras',
         'Quissama', 'Sao Fidelis', 'Miracema', 'Bom Jesus do Itabapoana', 'Santo Antonio de Padua', 'Piuma',
         'Anchieta', 'Iconha', 'Centro Vila Velha', 'Centro VIX', 'Glória', 'Norte Sul', 'Serra Sede',
         'Itaim Paulista', 'Embu das Artes', 'Carapicuíba', 'Jardim Camburi']
for i, n in enumerate(NOMES):
    pref = 'RJD1220' if n in RJ else ('SPREV002' if n in SP else 'ESD0267')
    Sector.objects.create(name='Loja ' + n, adabas='{}-{:03d}'.format(pref, i))
Sector.objects.create(name='Inteligência')

chefe = User.objects.create_user(username='zzg.chefe', email='zzg.chefe@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Chefe', hierarchy='SUPERADMIN')
editor = User.objects.create_user(username='zzg.editor', email='zzg.editor@exemplo-teste.local', password='S3nha!teste',
                                  first_name='Edi', hierarchy='PADRAO')
leitor = User.objects.create_user(username='zzg.leitor', email='zzg.leitor@exemplo-teste.local', password='S3nha!teste',
                                  first_name='Lei', hierarchy='PADRAO')
fora = User.objects.create_user(username='zzg.fora', email='zzg.fora@exemplo-teste.local', password='S3nha!teste',
                                first_name='Fora', hierarchy='PADRAO')
grupo = CommunicationGroup.objects.create(name='INTELIGENCIA ZZ', created_by=chefe)
grupo.members.add(editor)

cs, ce, cl, cf = Client(), Client(), Client(), Client()
cs.force_login(chefe)
ce.force_login(editor)
cl.force_login(leitor)
cf.force_login(fora)

# ---------------------------------------------------------------------------
print('acesso')
t('quem não foi liberado recebe 404', cf.get('/gestao-metas/').status_code == 404)
t('SUPERADMIN entra', cs.get('/gestao-metas/').status_code == 200)
r = cs.get('/gestao-metas/acessos/')
t('SUPERADMIN abre a lista de acessos', r.status_code == 200)
cs.post('/gestao-metas/acessos/', {'acao': 'adicionar', 'alvo': 'g:{}'.format(grupo.id), 'papel': 'EDITOR'})
cs.post('/gestao-metas/acessos/', {'acao': 'adicionar', 'alvo': 'u:{}'.format(leitor.id), 'papel': 'VISUALIZADOR'})
t('libera grupo e pessoa', AcessoGestaoMetas.objects.count() == 2)
editor = User.objects.get(pk=editor.pk)
t('editor (pelo grupo) entra', ce.get('/gestao-metas/').status_code == 200)
t('visualizador entra', cl.get('/gestao-metas/').status_code == 200)
t('editor não mexe na lista de acessos', ce.get('/gestao-metas/acessos/').status_code == 302)
t('quem está fora não vê a lista de acessos', cf.get('/gestao-metas/acessos/').status_code == 404)
r_home = cf.get('/')
menu_fora = r_home.content.decode() if r_home.status_code == 200 else ''
menu_chefe = cs.get('/gestao-metas/').content.decode()
t('menu mostra "Gerenciamento de metas" para quem tem acesso', 'Gerenciamento de metas' in menu_chefe)
t('menu esconde de quem não tem', 'gestao-metas' not in menu_fora)
t('lojas nascem dos setores, com região pelo ADABAS',
  LojaMeta.objects.filter(chave='QUISSAMA', regiao='RJ').exists()
  and LojaMeta.objects.filter(chave='CARAPICUIBA', regiao='SP').exists()
  and LojaMeta.objects.filter(chave='MASTERPLACE', regiao='ES').exists(), list(LojaMeta.objects.values_list('chave', 'regiao'))[:5])
r = cl.post('/gestao-metas/abrir/', {'mes': '2026-01'})
t('visualizador não abre competência', r.status_code == 302 and not Competencia.objects.exists())

# ---------------------------------------------------------------------------
print('planilha antiga')
tem_planilha = PLANILHA.exists()
if not tem_planilha:
    print('  PULADO: planilha de novembro/2026 não encontrada em ' + str(PLANILHA))
    pulados += 1
else:
    wv = openpyxl.load_workbook(PLANILHA, data_only=True)
    with open(PLANILHA, 'rb') as fh:
        r = ce.post('/gestao-metas/importar/', {'tipo': 'planilha', 'arquivo': SimpleUploadedFile('metas.xlsx', fh.read())})
    imp = ImportacaoMetas.objects.filter(tipo='PLANILHA').first()
    t('planilha vira prévia (nada gravado ainda)', imp is not None and imp.status == 'PREVIA'
      and not Realizado.objects.exists(), r.status_code)
    t('prévia acha a competência 2026-11 e 23 lojas', imp.dados['competencia'] == '2026-11' and len(imp.dados['lojas']) == 23,
      (imp.dados.get('competencia'), len(imp.dados.get('lojas', []))))
    t('PDV Vivo pela maioria das abas (Serra Sede)', any(l['nome'] == 'SERRA SEDE' and l['pdv_vivo'] == 'MASTERCEL SERRA SEDE'
                                                       for l in imp.dados['lojas']))
    t('PDV Vivo que escorregou de linha não vai para outra loja (Carapicuíba)',
      any(l['nome'] == 'CARAPICUIBA' and l['pdv_vivo'] == '' for l in imp.dados['lojas']))
    t('prévia abre', ce.get('/gestao-metas/importacoes/{}/'.format(imp.pk)).status_code == 200)
    t('visualizador não grava a prévia', cl.post('/gestao-metas/importacoes/{}/'.format(imp.pk), {'acao': 'aplicar'}).status_code == 302
      and ImportacaoMetas.objects.get(pk=imp.pk).status == 'PREVIA')
    r = ce.post('/gestao-metas/importacoes/{}/'.format(imp.pk), {'acao': 'aplicar'})
    nov = Competencia.objects.filter(mes=date(2026, 11, 1)).first()
    out = Competencia.objects.filter(mes=date(2026, 10, 1)).first()
    t('gravou: novembro aberta e outubro fechada', nov and not nov.fechada and out and out.fechada, r.status_code)
    t('Jardim Camburi (fora da planilha, sem venda) ficou desativada',
      not LojaMeta.objects.get(chave='JARDIM CAMBURI').ativa and LojaMeta.objects.filter(ativa=True).count() == 23)
    t('realizado de abr/25 a out/26', Realizado.objects.filter(mes=date(2025, 4, 1)).exists()
      and Realizado.objects.filter(mes=date(2026, 10, 1)).exists())
    t('Fixa guarda TV e Fixa separados', Realizado.objects.get(loja__chave='MASTERPLACE', mes=date(2026, 10, 1), pilar='fixa').qtd_tv == 9)

    dados, ctx = servicos.montar(nov)
    t('janela: jul–out/26 e nov/25', ctx['meses_quad'] == [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
      and ctx['mes_ano'] == date(2025, 11, 1))

    def linha(pilar, nome):
        return next(l for l in dados[pilar]['linhas'] if l['loja'].chave == nome)

    def linha_planilha(aba, nome):
        ws = wv[aba]
        for r_ in range(1, ws.max_row + 1):
            if servicos.normalizar(ws.cell(r_, 2).value or '') == nome:
                return r_
        raise KeyError(nome)

    for aba, pilar in (('MÓVEL', 'movel'), ('SVA', 'sva'), ('SEGUROS', 'seguros'), ('SMARTPHONE', 'smartphone'),
                       ('ELETRONICOS', 'eletronicos')):
        ws = wv[aba]
        for nome in ('MASTERPLACE', 'GLORIA', 'QUISSAMA'):
            rr = linha_planilha(aba, nome)
            l = linha(pilar, nome)
            t('{} {}: média quad = X{}'.format(aba, nome, rr), perto(l['media_quad'], ws['X%d' % rr].value), (l['media_quad'], ws['X%d' % rr].value))
            t('{} {}: real ano anterior = AB'.format(aba, nome), perto(l['real_ano_anterior'], ws['AB%d' % rr].value))
            t('{} {}: meta Vivo e meta mês anterior'.format(aba, nome),
              perto(l['meta_vivo'], ws['Z%d' % rr].value or None, 0.001) and perto(l['meta_anterior'], ws['AA%d' % rr].value),
              (l['meta_vivo'], ws['Z%d' % rr].value, l['meta_anterior'], ws['AA%d' % rr].value))
    # SP: AVERAGE ignora os meses vazios (só out/26 tem venda).
    ws = wv['MÓVEL']
    t('MÓVEL Itaim: média de um mês só, como o AVERAGE', perto(linha('movel', 'ITAIM PAULISTA')['media_quad'], ws['X23'].value))
    t('meta vazia não compara (— em vez de -100%)', linha('movel', 'MASTERPLACE')['mxm'] is None)

    # Meta digitada: confere as fórmulas AD:AG da planilha.
    for nome, meta in (('MASTERPLACE', 17000), ('GLORIA', 15500), ('QUISSAMA', 3000)):
        rr = linha_planilha('MÓVEL', nome)
        loja = LojaMeta.objects.get(chave=nome)
        resp = ce.post('/gestao-metas/2026-11/salvar/', json.dumps({'loja': loja.id, 'pilar': 'movel', 'campo': 'meta',
                                                                  'valor': '{:,}'.format(meta).replace(',', '.')}),
                       content_type='application/json')
        corpo = resp.json()
        W, AB, X, AA = (ws['W%d' % rr].value, ws['AB%d' % rr].value, ws['X%d' % rr].value, ws['AA%d' % rr].value)
        esperado = [meta / W - 1 if W else 0, meta / AB - 1 if AB else 0, meta / X - 1 if X else 0, meta / AA - 1 if AA else 0]
        obtido = [corpo['linha'][k] for k in ('mxm', 'yxy', 'mxquad', 'mxmeta')]
        t('MÓVEL {}: meta {} → M×M, Y×Y, Meta×Quad, M×M ant. = planilha'.format(nome, meta),
          resp.status_code == 200 and all(perto(a, b, 1e-9) for a, b in zip(obtido, esperado)), (obtido, esperado))
    t('"17.000" em formato brasileiro gravou 17000', MetaLoja.objects.get(competencia=nov, loja__chave='MASTERPLACE', pilar='movel').meta == 17000)
    t('histórico registra quem mudou', HistoricoMeta.objects.filter(campo='meta', loja_nome='MASTERPLACE', por=editor).exists())

    # Subtotais e REDE.
    dados, ctx = servicos.montar(nov, ['movel'])
    rede = dados['movel']['rede']
    grupos = {g['titulo']: g for g in dados['movel']['grupos']}
    t('REDE: média quad = soma das médias (X29)', perto(rede['media_quad'], ws['X29'].value), (rede['media_quad'], ws['X29'].value))
    t('REDE: meta Vivo (Z29), meta anterior (AA29), real ano anterior (AB29)',
      perto(rede['meta_vivo'], ws['Z29'].value) and perto(rede['meta_anterior'], ws['AA29'].value) and perto(rede['real_ano_anterior'], ws['AB29'].value))
    t('REDE: out/26 = W29', perto(rede['ultimo'], ws['W29'].value))
    t('grupos RJ, ES e SP', set(grupos) == {'RJ', 'ES', 'SP'}, list(grupos))
    rj = [linha_planilha('MÓVEL', n) for n in ('QUISSAMA', 'SAO FIDELIS', 'MIRACEMA', 'BOM JESUS DO ITABAPOANA', 'SANTO ANTONIO DE PADUA')]
    t('RJ = soma das 5 lojas do RJ', perto(grupos['RJ']['media_quad'], sum(ws['X%d' % r_].value for r_ in rj)) and grupos['RJ']['qtd_lojas'] == 5)
    t('RJ + ES + SP = REDE', perto(sum(g['media_quad'] for g in grupos.values()), rede['media_quad']))
    t('REDE: meta = soma das metas e M×M sobre a soma', perto(rede['meta'], 35500) and perto(rede['mxm'], 35500 / ws['W29'].value - 1, 1e-9))

    # Fixa.
    wf = wv['FIXA']
    dados, ctx = servicos.montar(nov, ['fixa'])
    l = linha('fixa', 'MASTERPLACE')
    # A AR4 da planilha ficou um mês para trás (jun–set: a fórmula não foi arrastada); a regra é jul–out.
    quad_q = sum(wf[c + '4'].value for c in ('AJ', 'AK', 'AL', 'AM', 'AN', 'AO', 'AP', 'AQ')) / 4
    t('FIXA: média quad em quantidade = jul–out (TV+Fixa)', perto(l['media_quad'], quad_q), (l['media_quad'], quad_q, wf['AR4'].value))
    t('FIXA: meta Vivo AT4, meta anterior AU4, real ano anterior AV4',
      perto(l['meta_vivo'], wf['AT4'].value, 0.001) and perto(l['meta_anterior'], wf['AU4'].value) and perto(l['real_ano_anterior'], wf['AV4'].value),
      (l['meta_vivo'], l['meta_anterior'], l['real_ano_anterior']))
    t('FIXA: consultores e decisão vêm da planilha', l['qtd_cns'] == 6 and l['decisao'] == 'CIMA')
    t('FIXA: sem R$ de jul–out na planilha, média R$ fica vazia', l['media_quad_valor'] is None, l['media_quad_valor'])
    # Ticket: R$ ÷ (TV + Fixa) nos meses com os dois — mar a jun/26 na planilha.
    rs = sum(wf[c + '4'].value for c in ('V', 'W', 'X', 'Y'))
    qt = sum(wf[c + '4'].value for c in ('AB', 'AC', 'AD', 'AE', 'AF', 'AG', 'AH', 'AI'))
    t('FIXA: ticket médio = R$ ÷ quantidade de mar–jun/26', perto(l['ticket'], rs / qt, 1e-6), (l['ticket'], rs / qt))
    loja = LojaMeta.objects.get(chave='MASTERPLACE')
    resp = ce.post('/gestao-metas/2026-11/salvar/', json.dumps({'loja': loja.id, 'pilar': 'fixa', 'campo': 'meta', 'valor': '10500'}),
                   content_type='application/json')
    q = math.ceil(10500 / (rs / qt))
    cima, baixo = math.ceil(q / 6), math.floor(q / 6)
    li = resp.json()['linha']
    t('FIXA: conta com sobra (↑ ≠ ↓)', cima != baixo, (q, cima, baixo))
    t('FIXA: QTD = ARRED.CIMA(meta ÷ ticket), por CN ↑/↓, nova meta = ↑ × CNs',
      li['qtd'] == q and li['cima'] == cima and li['baixo'] == baixo and li['nova_meta'] == cima * 6, li)
    t('FIXA: M×M = nova meta ÷ (TV+Fixa de out) − 1', perto(li['mxm'], cima * 6 / (wf['AP4'].value + wf['AQ4'].value) - 1, 1e-9))
    t('FIXA: Y×Y e Meta×Quad sobre quantidades', perto(li['yxy'], cima * 6 / wf['AV4'].value - 1, 1e-9)
      and perto(li['mxquad'], cima * 6 / quad_q - 1, 1e-9))
    resp = ce.post('/gestao-metas/2026-11/salvar/', json.dumps({'loja': loja.id, 'pilar': 'fixa', 'campo': 'decisao', 'valor': 'BAIXO'}),
                   content_type='application/json')
    t('FIXA: decisão Baixo usa o ↓', resp.json()['linha']['nova_meta'] == baixo * 6)
    t('FIXA: nova meta fica gravada', MetaLoja.objects.get(competencia=nov, loja=loja, pilar='fixa').nova_meta == baixo * 6)
    resp = ce.post('/gestao-metas/2026-11/salvar/', json.dumps({'loja': loja.id, 'pilar': 'fixa', 'campo': 'qtd_cns', 'valor': '0'}),
                   content_type='application/json')
    t('FIXA: sem consultor, a nova meta é a QTD inteira', resp.json()['linha']['nova_meta'] == q)
    ce.post('/gestao-metas/2026-11/salvar/', json.dumps({'loja': loja.id, 'pilar': 'fixa', 'campo': 'qtd_cns', 'valor': '6'}),
            content_type='application/json')
    sp = linha('fixa', 'ITAIM PAULISTA')
    t('FIXA: loja sem histórico de R$ usa o ticket da rede', sp['ticket_da_rede'] and perto(sp['ticket'], ctx['ticket_rede']))

    # Tela.
    r = ce.get('/gestao-metas/2026-11/')
    html = r.content.decode()
    t('tela da competência abre com os 7 pilares', r.status_code == 200 and all(n in html for n in ('Móvel', 'Fixa', 'SVA', 'Seguros', 'Smartphone', 'Eletrônicos', 'Essenciais')))
    t('tela traz entradas editáveis e os totais', 'data-campo="meta"' in html and 'data-grupo="REDE"' in html and 'data-grupo="RJ"' in html)
    t('visualizador vê sem campo de digitar', 'data-campo="meta"' not in cl.get('/gestao-metas/2026-11/').content.decode())
    t('histórico abre', ce.get('/gestao-metas/2026-11/historico/').status_code == 200)
    (PASTA / 'tela.html').write_text(html)

    # Preenchimento rápido.
    r = ce.post('/gestao-metas/2026-11/preencher/', {'pilar': 'sva', 'origem': 'media_quad', 'percentual': '10', 'arredondar': '100'})
    m = MetaLoja.objects.get(competencia=nov, loja__chave='MASTERPLACE', pilar='sva')
    rr = linha_planilha('SVA', 'MASTERPLACE')
    t('preencher: média quad +10% em múltiplos de 100', m.meta == round(wv['SVA']['X%d' % rr].value * 1.1 / 100) * 100, m.meta)
    ce.post('/gestao-metas/2026-11/preencher/', {'pilar': 'sva', 'origem': 'meta_vivo', 'percentual': '0', 'arredondar': '0', 'somente_vazias': '1'})
    t('preencher só as vazias não mexe no que já tinha', MetaLoja.objects.get(pk=m.pk).meta == m.meta)

    # Excel.
    r = ce.get('/gestao-metas/2026-11/excel/')
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    t('Excel com as 7 abas na ordem da planilha', wb.sheetnames == ['MÓVEL', 'FIXA', 'SVA', 'SEGUROS', 'SMARTPHONE', 'ELETRONICOS', 'ESSENCIAIS'], wb.sheetnames)
    wsx = wb['MÓVEL']
    cabec = [c.value for c in wsx[2]]
    t('Excel MÓVEL: mesmas colunas (PDV VIVO, PDV REDE, meses, MEDIA QUAD, META VIVO … M X M)',
      cabec[:3] == ['PDV VIVO', 'PDV REDE', 'MÓVEL'] and 'MEDIA QUAD' in cabec and 'META MÊS ANTERIOR' in cabec and cabec[-1] == 'M X M', cabec)
    linha_mp = next(r_ for r_ in wsx.iter_rows(min_row=3, values_only=True) if r_[1] == 'MASTERPLACE')
    t('Excel MÓVEL: Masterplace com meta 17000', linha_mp[2] == 17000)
    t('Excel FIXA tem QTD CNS / Decisão / NOVA META', all(x in [c.value for c in wb['FIXA'][3]] for x in ('QTD CNS', 'Decisão', 'NOVA META')))
    t('Excel REDE no fim', any(r_[0] == 'REDE' for r_ in wsx.iter_rows(values_only=True)))

    # Competência nova: dezembro pega a meta final de novembro.
    r = ce.post('/gestao-metas/2026-11/fechar/', {'acao': 'fechar'})
    nov.refresh_from_db()
    t('fechar novembro', nov.fechada)
    resp = ce.post('/gestao-metas/2026-11/salvar/', json.dumps({'loja': loja.id, 'pilar': 'movel', 'campo': 'meta', 'valor': '1'}),
                   content_type='application/json')
    t('competência fechada recusa mudança (409)', resp.status_code == 409)
    t('editor não reabre (só SUPERADMIN)', ce.post('/gestao-metas/2026-11/fechar/', {'acao': 'reabrir'}) and Competencia.objects.get(pk=nov.pk).fechada)
    ce.post('/gestao-metas/abrir/', {'mes': '2026-12'})
    dez = Competencia.objects.get(mes=date(2026, 12, 1))
    dados, ctx = servicos.montar(dez, ['movel', 'fixa'])
    t('dezembro: meta mês anterior = meta de novembro', perto(linha('movel', 'MASTERPLACE')['meta_anterior'], 17000))
    t('dezembro: Fixa usa a nova meta (quantidade) como meta anterior', linha('fixa', 'MASTERPLACE')['meta_anterior'] == baixo * 6)
    t('dezembro: consultores e decisão herdados', linha('fixa', 'MASTERPLACE')['qtd_cns'] == 6 and linha('fixa', 'MASTERPLACE')['decisao'] == 'BAIXO')
    t('dezembro: janela andou (ago–nov) e ano anterior = dez/25',
      ctx['meses_quad'][0] == date(2026, 8, 1) and ctx['mes_ano'] == date(2025, 12, 1)
      and perto(linha('movel', 'MASTERPLACE')['real_ano_anterior'], ws['M3'].value))
    t('dezembro: novembro sem venda fica fora da média', perto(linha('movel', 'MASTERPLACE')['media_quad'],
                                                             (ws['U3'].value + ws['V3'].value + ws['W3'].value) / 3))
    r = cs.post('/gestao-metas/2026-11/fechar/', {'acao': 'reabrir'})
    t('SUPERADMIN reabre', not Competencia.objects.get(pk=nov.pk).fechada)

# ---------------------------------------------------------------------------
print('salvamento: validação')
comp, _ = servicos.abrir_competencia(date(2027, 3, 1), chefe)
loja = LojaMeta.objects.get(chave='MASTERPLACE')


def salvar(cliente, **corpo):
    return cliente.post('/gestao-metas/2027-03/salvar/', json.dumps(corpo), content_type='application/json')


t('visualizador: 403', salvar(cl, loja=loja.id, pilar='movel', campo='meta', valor='10').status_code == 403)
t('fora: 404', salvar(cf, loja=loja.id, pilar='movel', campo='meta', valor='10').status_code == 404)
t('texto inválido: 400', salvar(ce, loja=loja.id, pilar='movel', campo='meta', valor='abc').status_code == 400)
t('negativo: 400', salvar(ce, loja=loja.id, pilar='movel', campo='meta', valor='-5').status_code == 400)
t('campo da Fixa em outro pilar: 400', salvar(ce, loja=loja.id, pilar='movel', campo='qtd_cns', valor='3').status_code == 400)
t('campo desconhecido: 400', salvar(ce, loja=loja.id, pilar='movel', campo='meta_vivo', valor='3').status_code == 400)
t('pilar desconhecido: 400', salvar(ce, loja=loja.id, pilar='xpto', campo='meta', valor='3').status_code == 400)
t('loja inexistente: 400', salvar(ce, loja=999999, pilar='movel', campo='meta', valor='3').status_code == 400)
t('consultores fracionado: 400', salvar(ce, loja=loja.id, pilar='fixa', campo='qtd_cns', valor='2,5').status_code == 400)
t('apagar a meta (vazio) volta a "—"', salvar(ce, loja=loja.id, pilar='movel', campo='meta', valor='').json()['linha']['mxm'] is None)
t('GET no salvar não grava', ce.get('/gestao-metas/2027-03/salvar/').status_code == 405)
t('competência inexistente: 404', ce.get('/gestao-metas/2031-01/').status_code == 404)

# ---------------------------------------------------------------------------
print('bases de vendas')
wb = openpyxl.Workbook()
ws = wb.active
ws.append(['PDV', 'pilar', 'data_da_venda', 'Venda ativa', 'Status do Serviço', 'Serviço Técnico', 'Receita', 'receita_calculada'])
linhas_base = [
    ('MASTERPLACE', 'Móvel', '05/02/2027', '1', 'Confirmado', 'Alta Controle', 100.0),
    ('MASTERPLACE', 'Móvel', '06/02/2027', '1', 'Cancelado', 'Alta Controle', 999.0),
    ('MASTERPLACE', 'Móvel', '06/02/2027', '0', 'Confirmado', 'Alta Controle', 999.0),
    ('MASTERPLACE', 'Fixa', '07/02/2027', '1', 'Confirmado', 'Alta Banda Larga', 90.0),
    ('MASTERPLACE', 'Fixa', '07/02/2027', '1', 'Confirmado', 'Portabilidade Fixa', 100.0),
    ('MASTERPLACE', 'Fixa', '07/02/2027', '1', 'Confirmado', 'Alta TV', 120.0),
    ('MASTERPLACE', 'Fixa', '07/02/2027', '1', 'Confirmado', 'Upgrade Fixa', 20.0),
    ('MASTERPLACE', 'Pré Pago', '07/02/2027', '1', 'Confirmado', '', 0),
    ('Loja Glória', 'SVA', '2027-02-10', '1', 'Confirmado', 'SVA Móvel', 35.5),
    ('LOJA FANTASMA', 'Móvel', '2027-02-10', '1', 'Confirmado', 'Alta Pós', 50),
]
for l_ in linhas_base:
    ws.append(list(l_) + [1.0])
b = io.BytesIO()
wb.save(b)
csv_prod = ('pdv;pilar;data_da_venda;venda_ativa;valor_líquido_de_venda_do_produto\n'
            'MASTERPLACE;Smartphone;2027-02-03;1;1.500,50\nMASTERPLACE;Smartphone;2027-02-04;1;1000\n'
            'MASTERPLACE;Eletrônicos;2027-02-04;1;300\nMASTERPLACE;Erro;2027-02-04;1;10\n').encode('latin-1')
r = ce.post('/gestao-metas/importar/', {'tipo': 'base', 'substituir': '1',
                                        'arquivos': [SimpleUploadedFile('base_servicos.xlsx', b.getvalue()),
                                                     SimpleUploadedFile('base_produtos.csv', csv_prod)]})
imp = ImportacaoMetas.objects.filter(tipo='BASE').first()
t('bases viram prévia com as duas', imp and imp.dados['bases'] == ['serviços', 'produtos'], getattr(imp, 'dados', None))
t('prévia aponta a loja não reconhecida', imp and imp.dados['nao_reconhecidas'] == ['LOJA FANTASMA'])
t('prévia da base abre', ce.get('/gestao-metas/importacoes/{}/'.format(imp.pk)).status_code == 200)
ce.post('/gestao-metas/importacoes/{}/'.format(imp.pk), {'acao': 'aplicar'})
fev = date(2027, 2, 1)
rm = Realizado.objects.get(loja=loja, mes=fev, pilar='movel')
rf = Realizado.objects.get(loja=loja, mes=fev, pilar='fixa')
t('serviço: só ativa e confirmada, pela Receita', rm.valor == 100 and rm.fonte == 'BASE', rm.valor)
t('Fixa: R$ do pilar inteiro; TV = Alta TV; Fixa = Banda Larga + Portabilidade', rf.valor == 330 and rf.qtd_tv == 1 and rf.qtd_fixa == 2,
  (rf.valor, rf.qtd_tv, rf.qtd_fixa))
t('"Loja Glória" reconhecida como GLÓRIA', float(Realizado.objects.get(loja__chave='GLORIA', mes=fev, pilar='sva').valor) == 35.5)
t('produto (csv ; latin-1, vírgula decimal)', float(Realizado.objects.get(loja=loja, mes=fev, pilar='smartphone').valor) == 2500.5
  and float(Realizado.objects.get(loja=loja, mes=fev, pilar='eletronicos').valor) == 300)
try:
    importacao.ler_base(io.BytesIO(b'a;b\n1;2\n'), 'x.csv')
    recusou = False
except ValueError:
    recusou = True
t('arquivo sem PDV/Pilar é recusado com aviso', recusou)

# ---------------------------------------------------------------------------
print('banco de vendas (falso)')


class CursorFalso:
    def __init__(self):
        self.consultas = []
        self.ultima = ''

    def execute(self, sql, params=None):
        self.consultas.append((sql, params))
        self.ultima = sql

    def fetchall(self):
        if 'vendas_produto' in self.ultima:
            return [('MASTERPLACE', 'Smartphone', 5000), ('SEDE ADMINISTRATIVA', 'Smartphone', 10)]
        return [('MASTERPLACE', 'Móvel', 'Alta Pós', 700, 3), ('MASTERPLACE', 'Fixa', 'Alta TV', 240, 2),
                ('MASTERPLACE', 'Fixa', 'Alta Banda Larga', 900, 9), ('MASTERPLACE', 'Pré Pago', '', 0, 40)]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ConexaoFalsa:
    def __init__(self):
        self.c = CursorFalso()

    def cursor(self):
        return self.c

    def close(self):
        pass


conexao = ConexaoFalsa()
with mock.patch('pymysql.connect', return_value=conexao):
    r = ce.post('/gestao-metas/importar/', {'tipo': 'banco', 'meses': ['2027-01'], 'substituir': '1'})
imp = ImportacaoMetas.objects.filter(tipo='BANCO').first()
t('banco: uma prévia por busca', imp is not None and imp.dados['meses'] == ['2027-01'], r.status_code)
t('banco: só leitura (só SELECT) e por intervalo de data', all(s.strip().upper().startswith('SELECT') and 'data_da_venda >=' in s
                                                             for s, _ in conexao.c.consultas))
t('banco: "SEDE ADMINISTRATIVA" não vira loja desconhecida', imp.dados['nao_reconhecidas'] == [])
ce.post('/gestao-metas/importacoes/{}/'.format(imp.pk), {'acao': 'aplicar'})
jan = date(2027, 1, 1)
rf = Realizado.objects.get(loja=loja, mes=jan, pilar='fixa')
t('banco: Fixa com R$ e quantidades', float(rf.valor) == 1140 and rf.qtd_tv == 2 and rf.qtd_fixa == 9 and rf.fonte == 'BANCO')
t('banco: produto e serviço', float(Realizado.objects.get(loja=loja, mes=jan, pilar='smartphone').valor) == 5000
  and float(Realizado.objects.get(loja=loja, mes=jan, pilar='movel').valor) == 700)
with mock.patch('pymysql.connect', side_effect=OSError('sem rota')):
    r = ce.post('/gestao-metas/importar/', {'tipo': 'banco', 'meses': ['2027-01']}, follow=True)
t('banco fora do ar: avisa e não cria prévia', ImportacaoMetas.objects.filter(tipo='BANCO').count() == 1
  and 'não respondeu' in r.content.decode())

# ---------------------------------------------------------------------------
print('arquivo de metas da Vivo')
wb = openpyxl.Workbook()
w1 = wb.active
w1.title = 'METAS  CN REAL'
w1.append(['CONSULTOR', 'PDV', 'MOVEL', 'FIXA'])
w1.append(['FULANO', 'MASTERPLACE', 1, 1])
w2 = wb.create_sheet('META PDV REAL')
w2.append(['CN', 'PDV', 'SVA', 'FIXA', 'MOVEL', 'SEGURO', 'ESSENCIAIS', 'SMARTPHONE', 'ELETRONICOS', 'COORDENAÇÃO'])
w2.append([4, 'MASTERPLACE', 1750, 96.13, 16753.82, 1800, 15000, 325000, 45000, 'GRANDE VITÓRIA'])
w2.append([1, 'EA MASTERCEL VIANA II ES', 400, 28, 4800, 350, 3900, 55000, 8000, 'GRANDE VITÓRIA'])
w2.append([None, 'REDE', 99999, 9, 9, 9, 9, 9, 9, ''])
b = io.BytesIO()
wb.save(b)
Sector.objects.filter(name='Loja Marcilio De Noronha').update(nome_loja_vivo='EA MASTERCEL VIANA II ES')
r = ce.post('/gestao-metas/importar/', {'tipo': 'meta_vivo', 'competencia_id': comp.id, 'arquivo': SimpleUploadedFile('vivo.xlsx', b.getvalue())})
imp = ImportacaoMetas.objects.filter(tipo='META_VIVO').first()
t('meta Vivo: lê a aba de PDV (ignora a de consultor)', imp and imp.dados['aba'] == 'META PDV REAL', getattr(imp, 'dados', None))
t('meta Vivo: prévia abre', ce.get('/gestao-metas/importacoes/{}/'.format(imp.pk)).status_code == 200)
ce.post('/gestao-metas/importacoes/{}/'.format(imp.pk), {'acao': 'aplicar'})
mv = MetaLoja.objects.get(competencia=comp, loja=loja, pilar='movel')
t('meta Vivo gravada (Móvel, Fixa, Seguros)', float(mv.meta_vivo) == 16753.82
  and float(MetaLoja.objects.get(competencia=comp, loja=loja, pilar='fixa').meta_vivo) == 96.13
  and float(MetaLoja.objects.get(competencia=comp, loja=loja, pilar='seguros').meta_vivo) == 1800)
t('meta Vivo: loja pelo nome Vivo do setor', MetaLoja.objects.filter(competencia=comp, loja__chave='MARCILIO DE NORONHA', pilar='sva', meta_vivo=400).exists())
t('importação aplicada não aplica de novo', ce.post('/gestao-metas/importacoes/{}/'.format(imp.pk), {'acao': 'aplicar'}).status_code == 302
  and ImportacaoMetas.objects.get(pk=imp.pk).status == 'APLICADA')

# ---------------------------------------------------------------------------
print('lojas')
r = ce.post('/gestao-metas/lojas/', {'acao': 'nova', 'nome': 'Loja Nova Teste'})
t('inclui loja', LojaMeta.objects.filter(chave='NOVA TESTE').exists())
nova = LojaMeta.objects.get(chave='NOVA TESTE')
ce.post('/gestao-metas/lojas/', {'l{}_nome'.format(nova.id): 'NOVA TESTE', 'l{}_regiao'.format(nova.id): 'SP',
                                 'l{}_apelidos'.format(nova.id): 'NT 01, LOJA NT', 'l{}_ordem'.format(nova.id): '5'})
nova.refresh_from_db()
t('edita região/apelidos e desativa (checkbox desmarcado)', nova.regiao == 'SP' and not nova.ativa and nova.ordem == 5)
t('apelido reconhece a loja', servicos.MapaLojas().achar('loja nt') == nova)

# ---------------------------------------------------------------------------
print('números digitados')
for texto, esperado in (('17.000', 17000), ('1.500,50', 1500.5), ('15700.5', 15700.5), ('0.500', 0.5), ('R$ 2.345.678', 2345678),
                        ('1,25', 1.25), ('', None)):
    v = servicos.numero(texto)
    t('"{}" → {}'.format(texto, esperado), (v is None and esperado is None) or (v is not None and float(v) == esperado), v)

print('higiene dos templates')
ruins = []
for arq in pathlib.Path('templates/gestao_metas').glob('*.html'):
    for n, linha_ in enumerate(arq.read_text().splitlines(), 1):
        if '{#' in linha_ and '#}' not in linha_:
            ruins.append('{}:{}'.format(arq.name, n))
t('comentário {# #} só de uma linha', not ruins, ruins)
fstr = []
for arq in list(pathlib.Path('gestao_metas').rglob('*.py')):
    for n, linha_ in enumerate(arq.read_text().splitlines(), 1):
        if re.search(r"f'[^']*\{[^}]*'[^}]*\}", linha_) or re.search(r'f"[^"]*\{[^}]*"[^}]*\}', linha_):
            fstr.append('{}:{}'.format(arq, n))
t('f-strings compatíveis com Python 3.11', not fstr, fstr)

print('\n{} ok, {} falha(s), {} bloco(s) pulado(s)'.format(ok, fail, pulados))
sys.exit(1 if fail else 0)
