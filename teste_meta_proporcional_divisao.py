"""Meta proporcional do consultor no /quadro-hc/ (100/80/60%) e o botão "Salvar" do /gestao-metas/<mês>/ (17/11/2026).

O botão divide a meta da loja entre os consultores do Quadro de HC: base = total ÷ N; quem tem 80% (60%) fica com
0,8 (0,6) × base; a sobra vai em partes iguais para quem tem meta cheia. Banco descartável (sqlite montado dos models).
"""
import os
import pathlib
import random
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-meta-prop-'))
(PASTA / 'zz_meta_prop_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-mp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-mp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_meta_prop_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import json
from datetime import date, timedelta
from decimal import Decimal

import requests
from django.contrib.auth import get_user_model
from django.utils import timezone

from communications.models import CommunicationGroup
from gestao_metas import servicos as gm
from gestao_metas.models import AcessoGestaoMetas, Competencia, HistoricoMeta, LojaMeta, MetaConsultor, MetaLoja, Realizado
from quadro_hc import servicos as hc
from quadro_hc.models import AcessoQuadroHC, AjusteHC, MetaProporcional
from users.models import Sector

User = get_user_model()
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print('  OK   ' + nome)
    else:
        fail += 1
        print('  FALHA ' + nome + ' ' + str(extra))


def sem_rede(*a, **k):
    raise AssertionError('teste tentou sair para a rede')


for nome in ('get', 'post', 'request'):
    mock.patch.object(requests, nome, sem_rede).start()

D = Decimal
hoje = timezone.localdate()

# ---------------------------------------------------------------------------
print('== DIVISÃO (função pura) ==')
r = gm.dividir_meta_loja(10000, [('a', 100), ('b', 80), ('c', 100), ('d', 100)])
partes = {k: v for k, _p, v in r}
t('10.000 entre 4, um de 80%: ele fica com 2.000 (0,8 × base 2.500)', partes['b'] == D('2000.00'), partes)
t('a sobra (500) vai para os 3 cheios: 2.666,67 / 2.666,67 / 2.666,66',
  [partes['a'], partes['c'], partes['d']] == [D('2666.67'), D('2666.67'), D('2666.66')], partes)
t('a soma fecha exatamente 10.000', sum(partes.values()) == D('10000.00'), sum(partes.values()))
t('a saída mantém a ordem e o percentual', [(k, p) for k, p, _ in r] == [('a', 100), ('b', 80), ('c', 100), ('d', 100)])

r = gm.dividir_meta_loja(D('9000'), [('a', 100), ('b', 60), ('c', 80)])
partes = {k: v for k, _p, v in r}
t('60% e 80%: 0,6 × base e 0,8 × base (base 3.000)', partes['b'] == D('1800.00') and partes['c'] == D('2400.00'), partes)
t('o único cheio leva base + toda a sobra (3.000 + 1.800)', partes['a'] == D('4800.00'), partes)

r = gm.dividir_meta_loja(D('9000'), [('a', 100), ('b', 100), ('c', 100)])
t('todos com 100%: partes iguais', [v for _, _, v in r] == [D('3000.00')] * 3)

r = gm.dividir_meta_loja(D('1000'), [('a', 80), ('b', 60)])
partes = {k: v for k, _p, v in r}
t('ninguém com 100%: divide proporcional ao percentual (80:60)',
  partes['a'] == D('571.43') and partes['b'] == D('428.57'), partes)
t('… e fecha no total', sum(partes.values()) == D('1000.00'))

r = gm.dividir_meta_loja(20, [('a', 100), ('b', 80), ('c', 100), ('d', 100)], casas=0)
partes = {k: v for k, _p, v in r}
t('quantidade inteira: 20 entre 4 (um 80%) = 6 / 4 / 5 / 5', [partes[k] for k in 'abcd'] == [6, 4, 5, 5], partes)
t('… só inteiros', all(v == v.to_integral_value() for v in partes.values()))
r = gm.dividir_meta_loja(10, [('a', 100), ('b', 60), ('c', 100), ('d', 100)], casas=0)
partes = {k: v for k, _p, v in r}
t('10 entre 4 (um 60%): quem tem 60% nunca passa do proporcional (1,5 → 1)', partes['b'] == 1 and sum(partes.values()) == 10,
  partes)
t('sem consultor: lista vazia', gm.dividir_meta_loja(100, []) == [])
t('meta zero: todos zero', [v for _, _, v in gm.dividir_meta_loja(0, [('a', 100), ('b', 80)])] == [0, 0])
try:
    gm.dividir_meta_loja(100, [('a', 120)])
    t('percentual fora de 1–100 é recusado', False)
except ValueError:
    t('percentual fora de 1–100 é recusado', True)

aleatorio = random.Random(17)
sempre_fecha = cheio_maior = True
for _ in range(400):
    n = aleatorio.randint(1, 9)
    pcts = [aleatorio.choice([100, 100, 80, 60]) for _ in range(n)]
    casas = aleatorio.choice([0, 2])
    total = D(aleatorio.randint(0, 5_000_000)) / (100 if casas else 1)
    total = total.quantize(D(1).scaleb(-casas))
    saida = gm.dividir_meta_loja(total, list(enumerate(pcts)), casas)
    if sum(v for _, _, v in saida) != total:
        sempre_fecha = False
    if 100 in pcts:
        menor_cheio = min(v for _, p, v in saida if p == 100)
        maior_reduzido = max([v for _, p, v in saida if p != 100] or [D(0)])
        if menor_cheio < maior_reduzido:
            cheio_maior = False
t('400 casos aleatórios: a soma sempre fecha no total', sempre_fecha)
t('… e quem tem meta cheia nunca fica abaixo de quem tem meta reduzida', cheio_maior)

# ---------------------------------------------------------------------------
print('\n== QUADRO DE HC: select "Meta" ==')
A = Sector.objects.create(name='Loja ZZ Alfa', adabas='ESD0267-901')
B = Sector.objects.create(name='Loja ZZ Beta', adabas='ESD0267-902')


def pessoa(username, setor, cargo, first_name, **kw):
    u = User.objects.create_user(username=username, email=f'{username}@exemplo-teste.local', password='S3nha!teste',
                                 first_name=first_name, last_name='ZZ', sector=setor, job_title=cargo, **kw)
    if setor:
        u.sectors.add(setor)
    return u


chefe = pessoa('zzmp.chefe', None, 'DIRETOR', 'Chefe', hierarchy='SUPERADMIN')
gestor_hc = pessoa('zzmp.gestor', None, 'ANALISTA', 'Gestor')
leitor_hc = pessoa('zzmp.leitor', None, 'ANALISTA', 'Leitor')
fora = pessoa('zzmp.fora', None, 'ANALISTA', 'Fora')
AcessoQuadroHC.objects.create(user=gestor_hc, papel=AcessoQuadroHC.GERENCIAR, liberado_por=chefe)
AcessoQuadroHC.objects.create(user=leitor_hc, papel=AcessoQuadroHC.VER, liberado_por=chefe)

ana = pessoa('zzmp.ana', A, 'CONSULTOR DE VENDAS', 'Ana')
bia = pessoa('zzmp.bia', A, 'CONSULTOR DE VENDAS PL', 'Bia')            # consultor pleno também é consultor
caio = pessoa('zzmp.caio', A, 'CONSULTOR DE VENDAS', 'Caio')
davi = pessoa('zzmp.davi', B, 'CONSULTOR DE VENDAS', 'Davi')             # lotado na Beta, conta na Alfa por ajuste
gerente = pessoa('zzmp.ger', A, 'GERENTE DE VENDAS', 'Gerson')
ger_grupo = pessoa('zzmp.gergrupo', A, 'CONSULTOR DE VENDAS', 'Gina')    # no grupo GERENTES: é a gerente
excluido = pessoa('zzmp.excl', A, 'CONSULTOR DE VENDAS', 'Elias')         # tirado do quadro à mão
desligado = pessoa('zzmp.desl', A, 'CONSULTOR DE VENDAS', 'Fabio', demission_date=hoje - timedelta(days=3),
                   status=User.STATUS_INATIVO)
antigo = pessoa('zzmp.antigo', A, 'CONSULTOR DE VENDAS', 'Hugo', demission_date=date(2024, 11, 1),
                status=User.STATUS_ATIVO)                                  # demissão antiga, mas trabalha
recep = pessoa('zzmp.recep', A, 'RECEPCIONISTA', 'Iris')
grupo_ger = CommunicationGroup.objects.create(name='GERENTES', created_by=chefe)
grupo_ger.members.add(ger_grupo)
AjusteHC.objects.create(colaborador=davi, setor=A, atualizado_por=chefe)
AjusteHC.objects.create(colaborador=excluido, situacao=AjusteHC.EXCLUIR, motivo='teste', atualizado_por=chefe)

cs, cg, cl, cf = Client(), Client(), Client(), Client()
cs.force_login(chefe)
cg.force_login(gestor_hc)
cl.force_login(leitor_hc)
cf.force_login(fora)


def post_json(cliente, url, corpo):
    return cliente.post(url, json.dumps(corpo), content_type='application/json')


html = cs.get('/quadro-hc/').content.decode()
t('o quadro mostra o select "Meta" do consultor', f'data-meta-pct="{ana.id}"' in html and 'Meta de 80%' in html)
t('… também para o consultor pleno', f'data-meta-pct="{bia.id}"' in html)
t('gerente, gerente pelo grupo e recepção não têm o select',
  all(f'data-meta-pct="{u.id}"' not in html for u in (gerente, ger_grupo, recep)))
t('padrão é a meta de 100%', 'value="100" selected' in html.split(f'data-meta-pct="{ana.id}"')[1][:600])

r = post_json(cs, '/quadro-hc/meta/', {'colaborador': caio.id, 'percentual': '80'})
mp = MetaProporcional.objects.filter(colaborador=caio).first()
t('SUPERADMIN grava 80% ao mudar o select', r.status_code == 200 and mp and mp.percentual == 80, r.content[:200])
t('guarda quem alterou e quando', mp and mp.atualizado_por_id == chefe.id and mp.atualizado_em is not None)
t('a resposta confirma', r.json().get('percentual') == 80 and 'Meta de 80%' in r.json().get('msg', ''))
html = cs.get('/quadro-hc/').content.decode()
t('ao recarregar, o select vem em 80%', 'value="80" selected' in html.split(f'data-meta-pct="{caio.id}"')[1][:600])

r = post_json(cg, '/quadro-hc/meta/', {'colaborador': bia.id, 'percentual': 60})
t('quem gerencia o quadro (liberado pelo SUPERADMIN) também grava',
  r.status_code == 200 and MetaProporcional.objects.get(colaborador=bia).atualizado_por_id == gestor_hc.id)
r = post_json(cl, '/quadro-hc/meta/', {'colaborador': ana.id, 'percentual': 60})
t('quem só vê o quadro não grava (403)', r.status_code == 403 and not MetaProporcional.objects.filter(colaborador=ana).exists())
t('… e vê o select desabilitado',
  'disabled' in cl.get('/quadro-hc/').content.decode().split(f'data-meta-pct="{ana.id}"')[1][:400])
r = post_json(cf, '/quadro-hc/meta/', {'colaborador': ana.id, 'percentual': 60})
t('quem não tem acesso ao quadro não grava (403)', r.status_code == 403)
r = post_json(cs, '/quadro-hc/meta/', {'colaborador': ana.id, 'percentual': 50})
t('percentual fora de 100/80/60 é recusado (400)', r.status_code == 400 and 'inválida' in r.json().get('erro', ''))
r = post_json(cs, '/quadro-hc/meta/', {'colaborador': gerente.id, 'percentual': 80})
t('não grava meta proporcional de quem não é consultor (400)', r.status_code == 400)
t('GET não grava (só POST)', cs.get('/quadro-hc/meta/').status_code == 405)
r = post_json(cs, '/quadro-hc/meta/', {'colaborador': bia.id, 'percentual': 100})
t('voltar para 100% grava (fica o registro de quem voltou)',
  MetaProporcional.objects.get(colaborador=bia).percentual == 100 and r.status_code == 200)

print('\n== CONSULTORES DA LOJA (mesma regra do quadro) ==')
lista = hc.consultores_da_loja(A)
nomes = [c['nome'] for c in lista]
t('Ana, Bia, Caio, Davi (ajuste de loja) e Hugo (demissão antiga, trabalha)',
  nomes == ['Ana ZZ', 'Bia ZZ', 'Caio ZZ', 'Davi ZZ', 'Hugo ZZ'], nomes)
t('ficam de fora: gerente, gerente pelo grupo, excluído à mão, desligado e recepção',
  not {'Gerson ZZ', 'Gina ZZ', 'Elias ZZ', 'Fabio ZZ', 'Iris ZZ'} & set(nomes))
t('com o percentual do quadro', {c['nome']: c['percentual'] for c in lista}['Caio ZZ'] == 80)
t('Davi não conta mais na Beta', hc.consultores_da_loja(B) == [])
q = hc.montar_quadro()
bloco_a = [b for c in q['carteiras'] for b in c['lojas'] if b['setor'].id == A.id][0]
consultores_no_quadro = sorted(p['nome'] for p in bloco_a['pessoas'] if p['e_consultor'])
t('a lista bate com os consultores que o quadro mostra na loja', consultores_no_quadro == sorted(nomes),
  consultores_no_quadro)

# ---------------------------------------------------------------------------
print('\n== GESTÃO DE METAS: botão Salvar divide a meta ==')
loja_a = LojaMeta.objects.create(nome='ZZ ALFA', chave='ZZ ALFA', setor=A, regiao='ES', ordem=10)
loja_sem = LojaMeta.objects.create(nome='ZZ SEM SETOR', chave='ZZ SEM SETOR', regiao='ES', ordem=20)
editor_gm = pessoa('zzmp.editor', None, 'ANALISTA', 'Edi')
leitor_gm = pessoa('zzmp.leitorgm', None, 'ANALISTA', 'Lia')
AcessoGestaoMetas.objects.create(user=editor_gm, papel=AcessoGestaoMetas.EDITOR, criado_por=chefe)
AcessoGestaoMetas.objects.create(user=leitor_gm, papel=AcessoGestaoMetas.VISUALIZADOR, criado_por=chefe)
ce, clg = Client(), Client()
ce.force_login(editor_gm)
clg.force_login(leitor_gm)

mes = date(hoje.year, hoje.month, 1)
comp, _ = gm.abrir_competencia(mes, chefe)
url = f'/gestao-metas/{comp.codigo}/dividir/'
# Ticket médio da Fixa = 100 (R$ 1.000 / 10 unidades no mês anterior).
Realizado.objects.create(loja=loja_a, mes=gm.somar_meses(mes, -1), pilar='fixa', valor=D('1000'), qtd_tv=0, qtd_fixa=10)

# Consultores da Alfa: Ana 100, Bia 100, Caio 80, Davi 100, Hugo 100 → passa Hugo para 60%.
post_json(cs, '/quadro-hc/meta/', {'colaborador': antigo.id, 'percentual': 60})

html = cs.get(f'/gestao-metas/{comp.codigo}/').content.decode()
t('a linha da loja tem o botão Salvar', 'data-dividir' in html and 'data-url-dividir' in html)
t('quem só vê a gestão de metas não tem o botão', 'data-dividir' not in clg.get(f'/gestao-metas/{comp.codigo}/').content.decode())

r = post_json(ce, url, {'loja': loja_a.id, 'pilar': 'movel', 'campos': {'meta': '10.000'}})
corpo = r.json()
t('Salvar grava a meta da loja', r.status_code == 200 and MetaLoja.objects.get(competencia=comp, loja=loja_a, pilar='movel').meta == D('10000.00'),
  r.content[:300])
mcs = {m.colaborador_id: m for m in MetaConsultor.objects.filter(competencia=comp, loja=loja_a, pilar='movel')}
t('grava uma meta por consultor do quadro (5)', len(mcs) == 5, len(mcs))
# base 2.000; Caio 80% = 1.600; Hugo 60% = 1.200; sobra 1.200 para 3 cheios = +400 → 2.400 cada.
esperado = {ana.id: D('2400.00'), bia.id: D('2400.00'), caio.id: D('1600.00'), davi.id: D('2400.00'), antigo.id: D('1200.00')}
t('valores: cheios 2.400, 80% = 1.600, 60% = 1.200', {k: m.meta for k, m in mcs.items()} == esperado,
  {k: m.meta for k, m in mcs.items()})
t('soma fecha a meta da loja', sum(m.meta for m in mcs.values()) == D('10000.00'))
t('guarda percentual, total da loja, quantos e quem gerou',
  mcs[caio.id].percentual == 80 and mcs[caio.id].meta_loja == D('10000.00') and mcs[caio.id].qtd_consultores == 5
  and mcs[caio.id].gerado_por_id == editor_gm.id)
d = corpo.get('divisao') or {}
t('a resposta traz quantos e quanto cada um (para a tela)',
  d.get('n') == 5 and d.get('base') == 2000.0 and len(d.get('itens', [])) == 5
  and {i['nome']: i['meta'] for i in d['itens']}.get('Caio ZZ') == 1600.0, d)
t('fica no histórico', HistoricoMeta.objects.filter(competencia=comp, loja=loja_a, pilar='movel',
                                                    campo='divisao_consultores').exists())
html = cs.get(f'/gestao-metas/{comp.codigo}/').content.decode()
t('a tela mostra a divisão na linha (÷ 5 consultores)', '÷ 5 consultores' in html and 'Caio ZZ (80%): 1.600,00' in html)

# Muda o quadro e salva de novo: refaz a divisão (sem duplicar; quem saiu some).
post_json(cs, '/quadro-hc/meta/', {'colaborador': caio.id, 'percentual': 100})
AjusteHC.objects.filter(colaborador=davi).delete()  # Davi volta a contar só na Beta
r = post_json(ce, url, {'loja': loja_a.id, 'pilar': 'movel', 'campos': {'meta': '10000'}})
mcs = {m.colaborador_id: m.meta for m in MetaConsultor.objects.filter(competencia=comp, loja=loja_a, pilar='movel')}
t('salvar de novo refaz a divisão (Davi saiu da loja e some)', r.status_code == 200 and set(mcs) == {ana.id, bia.id, caio.id, antigo.id},
  mcs)
# base 2.500; Hugo 60% = 1.500; sobra 1.000 / 3 → 2.833,33… → 2.833,34 / 2.833,33 / 2.833,33
t('novos valores fecham 10.000 (resto do centavo no primeiro cheio)',
  [mcs[ana.id], mcs[bia.id], mcs[caio.id], mcs[antigo.id]] == [D('2833.34'), D('2833.33'), D('2833.33'), D('1500.00')]
  and sum(mcs.values()) == D('10000.00'), mcs)

print('\n== FIXA: divide a nova meta (quantidade inteira) ==')
r = post_json(ce, url, {'loja': loja_a.id, 'pilar': 'fixa', 'campos': {'meta': '2.000', 'qtd_cns': '', 'decisao': 'CIMA'}})
m_fixa = MetaLoja.objects.get(competencia=comp, loja=loja_a, pilar='fixa')
t('Fixa: meta R$ 2.000 ÷ ticket 100 = nova meta 20', m_fixa.nova_meta == 20, (m_fixa.meta, m_fixa.nova_meta, r.content[:300]))
mcs = {m.colaborador_id: m.meta for m in MetaConsultor.objects.filter(competencia=comp, loja=loja_a, pilar='fixa')}
# 4 consultores (Ana, Bia, Caio 100; Hugo 60): base 5; Hugo 3; sobra 17 / 3 → 6, 6, 5
t('divide 20 em inteiros: 6 / 6 / 5 / 3', [mcs.get(ana.id), mcs.get(bia.id), mcs.get(caio.id), mcs.get(antigo.id)] == [6, 6, 5, 3], mcs)
t('… e fecha 20', sum(mcs.values()) == 20)
r = post_json(ce, url, {'loja': loja_a.id, 'pilar': 'fixa', 'campos': {'qtd_cns': '3'}})
t('Fixa com CNs diferentes do quadro: divide e avisa (qtd_cns na resposta)',
  r.status_code == 200 and r.json()['divisao']['qtd_cns'] == 3 and r.json()['divisao']['n'] == 4, r.content[:300])

print('\n== PERMISSÃO E ERROS ==')
r = post_json(clg, url, {'loja': loja_a.id, 'pilar': 'movel', 'campos': {'meta': '1'}})
t('visualizador da gestão de metas não salva (403)', r.status_code == 403)
r = post_json(cf, url, {'loja': loja_a.id, 'pilar': 'movel', 'campos': {'meta': '1'}})
t('quem não tem acesso recebe 404', r.status_code == 404)
t('nada mudou com as tentativas barradas',
  MetaLoja.objects.get(competencia=comp, loja=loja_a, pilar='movel').meta == D('10000.00'))
r = post_json(ce, url, {'loja': loja_sem.id, 'pilar': 'movel', 'campos': {'meta': '500'}})
t('loja sem setor do portal: salva a meta e explica por que não dividiu',
  r.status_code == 400 and 'Meta da loja salva' in r.json().get('erro', '')
  and MetaLoja.objects.get(competencia=comp, loja=loja_sem, pilar='movel').meta == D('500.00'), r.content[:300])
r = post_json(ce, url, {'loja': loja_a.id, 'pilar': 'sva', 'campos': {'meta': ''}})
t('sem meta definida: não divide', r.status_code == 400 and 'Defina a meta' in r.json().get('erro', ''))
loja_b = LojaMeta.objects.create(nome='ZZ BETA', chave='ZZ BETA', setor=B, regiao='ES', ordem=30)
AjusteHC.objects.create(colaborador=davi, situacao=AjusteHC.EXCLUIR, atualizado_por=chefe)
r = post_json(ce, url, {'loja': loja_b.id, 'pilar': 'movel', 'campos': {'meta': '800'}})
t('loja sem consultor no quadro: avisa', r.status_code == 400 and 'não tem consultor' in r.json().get('erro', ''))
r = post_json(ce, url, {'loja': loja_a.id, 'pilar': 'movel', 'campos': {'meta': 'abc'}})
t('meta inválida não salva nem divide', r.status_code == 400 and 'inválido' in r.json().get('erro', ''))
gm.fechar(comp, chefe)
r = post_json(ce, url, {'loja': loja_a.id, 'pilar': 'movel', 'campos': {'meta': '1'}})
t('competência fechada: 409', r.status_code == 409)

print('\n{} ok, {} falha(s)'.format(ok, fail))
sys.exit(1 if fail else 0)
