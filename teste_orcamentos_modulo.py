"""Módulo /orcamentos/ (06/11/2026): acesso por loja, gestores, mailing por loja e o painel.

Cobre quem usa e quem gere, a importação (CSV e Excel) com a escolha da coluna do número,
os repetidos e inválidos que ficam de fora, a lista compartilhada da loja, o contato que
vira "orçado" quando sai o orçamento, o descarte, o encerramento da base, o gestor limitado
às lojas dele e os números do painel (quem usa e quem não usa).

Roda num sqlite descartável (schema direto dos models); nenhuma mensagem sai.
"""
import io
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-orc-modulo-'))
(PASTA / 'zz_orc_modulo_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-orcm'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-orcm-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_orc_modulo_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
os.environ.setdefault('RC_SEM_TAREFAS_DIARIAS', '1')

django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import openpyxl
import requests
from django.contrib.auth import get_user_model

from core import evolution
from orcamentos import mailing
from orcamentos.models import BaseMailing, ContatoMailing, GestorOrcamentos, LiberacaoOrcamentos
from orcamentos.permissoes import lojas_geridas, pode_usar
from users.models import Sector
from vendas.models import Orcamento

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


for _n in ('get', 'post', 'request'):
    mock.patch.object(requests, _n, sem_rede).start()
mock.patch.object(evolution, 'enviar_texto', sem_rede).start()

centro = Sector.objects.create(name='Loja Centro')
gloria = Sector.objects.create(name='Loja Glória')
quiosque = Sector.objects.create(name='Quiosque Shopping', adabas='12345')   # loja pelo ADABAS
financeiro = Sector.objects.create(name='Financeiro')


def usuario(nome, setor, hierarquia='PADRAO', cargo='Consultor'):
    u = User.objects.create_user(username=f'zzm.{nome}', email=f'zzm.{nome}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZM', last_name=nome.capitalize(), sector=setor, hierarchy=hierarquia,
                                 job_title=cargo)
    c = Client()
    c.force_login(u)
    return u, c


ana, ca = usuario('ana', centro)
bia, cb = usuario('bia', centro)
caio, cc = usuario('caio', gloria)
davi, cd = usuario('davi', quiosque)
fin, cf = usuario('fin', financeiro, cargo='Analista')
lib, cl = usuario('lib', financeiro, cargo='Analista')
gestor, cg = usuario('gestor', financeiro, cargo='Coordenador')
chefe, cs = usuario('chefe', financeiro, hierarquia='SUPERADMIN', cargo='Diretor')


def html(c, url, **params):
    return c.get(url, params).content.decode()


print('Acesso')
h = html(ca, '/orcamentos/')
t('lotado em loja usa (e vê no menu)', 'name="cliente_nome"' in h and 'href="/orcamentos/"' in h)
t('loja pelo ADABAS também', pode_usar(davi) and cd.get('/orcamentos/').status_code == 200)
t('vendedor não vê importar, painel nem acessos', 'orcamentos/gestao/' not in h and 'orcamentos/painel/' not in h and 'orcamentos/acessos/' not in h)
t('vendedor não abre a gestão nem o painel', ca.get('/orcamentos/gestao/').status_code == 302 and ca.get('/orcamentos/painel/').status_code == 302)
t('fora de loja não usa', cf.get('/orcamentos/').status_code == 302 and cf.get('/orcamentos/mailing/').status_code == 302)
h = html(cs, '/orcamentos/')
t('SUPERADMIN vê tudo', all(u in h for u in ('/orcamentos/gestao/', '/orcamentos/painel/', '/orcamentos/acessos/')))

print('Gestores e liberações (SUPERADMIN)')
r = cs.post('/orcamentos/acessos/', {'acao': 'gestor', 'user': gestor.pk, 'lojas': [centro.pk]})
g = GestorOrcamentos.objects.get(user=gestor)
t('vira gestor das lojas marcadas', r.status_code == 302 and list(g.lojas.all()) == [centro])
gestor = User.objects.get(pk=gestor.pk)
t('o gestor gere só a loja dele', [l.id for l in lojas_geridas(gestor)] == [centro.id])
h = html(cg, '/orcamentos/')
t('o gestor usa e vê importar e painel, sem acessos', '/orcamentos/gestao/' in h and '/orcamentos/painel/' in h and '/orcamentos/acessos/' not in h)
t('gestor não abre acessos', cg.get('/orcamentos/acessos/').status_code == 302)
cs.post('/orcamentos/acessos/', {'acao': 'liberar', 'user': lib.pk})
t('liberado fora da loja usa', cl.get('/orcamentos/').status_code == 200)
h = html(cs, '/orcamentos/acessos/')
t('a tela lista gestor (com a loja) e liberado', 'ZZM Gestor' in h and 'Loja Centro' in h and 'ZZM Lib' in h)
cs.post('/orcamentos/acessos/', {'acao': 'tirar_liberacao', 'id': LiberacaoOrcamentos.objects.get(user=lib).pk})
t('tirar a liberação corta o acesso', cl.get('/orcamentos/').status_code == 302)
r = cs.post('/orcamentos/acessos/', {'acao': 'gestor', 'user': ''})
t('sem pessoa não cria gestor', GestorOrcamentos.objects.count() == 1)
t('quem não é SUPERADMIN não muda acesso', cg.post('/orcamentos/acessos/', {'acao': 'liberar', 'user': fin.pk}).status_code == 302
  and not LiberacaoOrcamentos.objects.filter(user=fin).exists())

print('Importar CSV (latin-1, ";") e conferir as colunas')
csv_bytes = ('ID Contato;Nome;Celular;Plano\n'
             '1001;MARIA DA SILVA;(27) 99876-5432;Controle 25GB\n'
             '1002;João Souza;27 98765-4321;Pós 50GB\n'
             '1003;Repetida;27998765432;Controle\n'
             '1004;Sem número;abc;Pré\n'
             '1005;<script>alert(1)</script>;27 3222-1100;Fixa\n'
             ';;;\n').encode('latin-1')
r = cs.post('/orcamentos/gestao/importar/', {'nome': 'Clientes outubro', 'loja': str(centro.pk),
                                              'arquivo': SimpleUploadedFile('base.csv', csv_bytes, 'text/csv')})
base = BaseMailing.objects.get(nome='Clientes outubro')
t('lê e guarda em rascunho, sem contato na loja ainda', base.status == 'RASCUNHO' and base.total_linhas == 5
  and base.colunas == ['ID Contato', 'Nome', 'Celular', 'Plano'] and not ContatoMailing.objects.exists(), base.colunas)
t('vai para a conferência com a loja escolhida', r.status_code == 302 and r['Location'] == f'/orcamentos/gestao/{base.pk}/conferir/?loja={centro.pk}')
t('acento do latin-1 lido certo', any('João Souza' in l for l in base.linhas))
t('sugere "Celular" (não "ID Contato") e "Nome"', mailing.sugerir_colunas(base) == (2, 1), mailing.sugerir_colunas(base))
h = html(cs, r['Location'])
t('a conferência mostra prévia e já marca as sugestões', 'Controle 25GB' in h and '<option value="2" selected>Celular' in h
  and '<option value="1" selected>Nome' in h and f'<option value="{centro.pk}" selected>' in h)
t('vendedor da loja ainda não vê o rascunho', 'Maria' not in html(ca, '/orcamentos/mailing/'))
r = cs.post(f'/orcamentos/gestao/{base.pk}/conferir/', {'loja': centro.pk, 'coluna_numero': '', 'coluna_nome': '1'})
base.refresh_from_db()
t('sem a coluna do número não importa', base.status == 'RASCUNHO')
r = cs.post(f'/orcamentos/gestao/{base.pk}/conferir/', {'loja': centro.pk, 'coluna_numero': '2', 'coluna_nome': '1'}, follow=True)
base.refresh_from_db()
t('importa: 3 contatos, 1 repetido, 1 sem número', (base.status, base.importados, base.duplicados, base.invalidos) == ('ATIVA', 3, 1, 1),
  (base.status, base.importados, base.duplicados, base.invalidos))
t('a mensagem diz o que ficou de fora', '3 contato(s) enviado(s) para o mailing da Loja Centro (1 repetido(s) e 1 sem número válido' in r.content.decode())
t('as linhas cruas saem do banco', base.linhas == [])
maria = ContatoMailing.objects.get(numero='5527998765432')
t('número no formato do WhatsApp, nome arrumado, outras colunas guardadas', maria.nome == 'Maria Da Silva' and maria.loja == centro
  and maria.dados == {'ID Contato': '1001', 'Plano': 'Controle 25GB'}, (maria.nome, maria.dados))
t('fixo também entra', ContatoMailing.objects.filter(numero='552732221100').exists())
r = cs.post(f'/orcamentos/gestao/{base.pk}/conferir/', {'loja': centro.pk, 'coluna_numero': '2'})
t('conferir de novo não duplica', ContatoMailing.objects.filter(base=base).count() == 3)

print('Importar Excel para outra loja')
livro = openpyxl.Workbook()
folha = livro.active
folha.append(['Cliente', 'WhatsApp', 'Cidade'])
folha.append(['Pedro Lima', 27999990001.0, 'Vitória'])      # número que o Excel guarda como float
folha.append(['Maria da Silva', 27998765432, 'Serra'])       # também está na Centro: em outra loja, entra
folha.append(['Sem fone', None, 'Vila Velha'])
buf = io.BytesIO()
livro.save(buf)
cs.post('/orcamentos/gestao/importar/', {'nome': 'Glória nov', 'arquivo': SimpleUploadedFile(
    'gloria.xlsx', buf.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
bg = BaseMailing.objects.get(nome='Glória nov')
t('lê o Excel e sugere as colunas', bg.total_linhas == 3 and mailing.sugerir_colunas(bg) == (1, 0), (bg.linhas, mailing.sugerir_colunas(bg)))
cs.post(f'/orcamentos/gestao/{bg.pk}/conferir/', {'loja': gloria.pk, 'coluna_numero': '1', 'coluna_nome': '0'})
bg.refresh_from_db()
t('2 contatos na Glória (o número da Centro conta só lá)', (bg.importados, bg.duplicados, bg.invalidos) == (2, 0, 1)
  and ContatoMailing.objects.filter(loja=gloria, numero='5527999990001').exists())
mailing_bytes = 'Fone;Nome\n27998765432;Maria de novo\n27991112222;Nova\n'.encode()
cs.post('/orcamentos/gestao/importar/', {'nome': 'Centro 2', 'arquivo': SimpleUploadedFile('c2.csv', mailing_bytes, 'text/csv')})
b2 = BaseMailing.objects.get(nome='Centro 2')
cs.post(f'/orcamentos/gestao/{b2.pk}/conferir/', {'loja': centro.pk, 'coluna_numero': '0', 'coluna_nome': '1'})
b2.refresh_from_db()
t('quem já está "a trabalhar" na mesma loja não entra de novo', (b2.importados, b2.duplicados) == (1, 1))

print('Arquivo ruim')
for nome_caso, arq, msg in (('PDF', SimpleUploadedFile('x.pdf', b'%PDF', 'application/pdf'), '.xlsx ou .csv'),
                            ('só cabeçalho', SimpleUploadedFile('x.csv', b'Nome;Fone\n', 'text/csv'), 'pelo menos uma linha'),
                            ('sem arquivo', None, 'Escolha o arquivo')):
    dados = {'nome': 'ruim'}
    if arq:
        dados['arquivo'] = arq
    r = cs.post('/orcamentos/gestao/importar/', dados, follow=True)
    t(f'{nome_caso}: recusa com aviso', msg in r.content.decode() and not BaseMailing.objects.filter(nome='ruim').exists())

print('Mailing da loja (vendedor)')
h = html(ca, '/orcamentos/mailing/')
t('vê os contatos da loja dele', 'Maria Da Silva' in h and 'João Souza' in h and 'Nova' in h)
t('não vê os de outra loja', 'Pedro Lima' not in h)
t('nome da planilha escapado', '<script>alert(1)</script>' not in h and '&lt;script&gt;' in h)
t('contagem de a trabalhar', 'A trabalhar <span class="opacity-75">4</span>' in h)
t('a outra loja vê só os dela', 'Pedro Lima' in html(cc, '/orcamentos/mailing/') and 'João Souza' not in html(cc, '/orcamentos/mailing/'))
t('busca por número e nome', 'João Souza' in html(ca, '/orcamentos/mailing/', q='98765-4321')
  and 'Maria Da Silva' not in html(ca, '/orcamentos/mailing/', q='joão'))
h = html(ca, '/orcamentos/')
t('o início mostra quanto falta trabalhar', 'Mailing a trabalhar' in h and '>4</p>' in h)

print('Orçamento a partir do mailing')
h = html(ca, '/orcamentos/', contato=maria.pk)
t('o formulário já vem com nome e número', 'value="Maria Da Silva"' in h and 'value="(27) 99876-5432"' in h and f'name="contato" value="{maria.pk}"' in h)
pedro = ContatoMailing.objects.get(nome='Pedro Lima')
h = html(ca, '/orcamentos/', contato=pedro.pk)
t('contato de outra loja não é aceito', 'name="contato"' not in h and 'Pedro Lima' not in h)
XHR = {'HTTP_X_REQUESTED_WITH': 'XMLHttpRequest'}
j = ca.post('/orcamentos/', {'contato': maria.pk, 'cliente_nome': 'Maria Da Silva', 'pilar': 'MOVEL',
                             'numero': '(27) 99876-5432', 'texto': 'Controle 30GB por R$ 59,99'}, **XHR).json()
maria.refresh_from_db()
o = Orcamento.objects.get(pk=j['id'])
t('o orçamento sai e o contato vira "orçado" por quem fez', j['ok'] and maria.status == 'TRABALHADO'
  and maria.trabalhado_por == ana and maria.orcamento == o)
t('a lista é da loja: a colega vê que já foi orçado', 'Maria Da Silva' not in html(cb, '/orcamentos/mailing/')
  and 'Orçado por ZZM Ana' in html(cb, '/orcamentos/mailing/', status='TRABALHADO'))
ca.post('/orcamentos/', {'contato': pedro.pk, 'cliente_nome': 'Pedro Lima', 'pilar': 'MOVEL',
                         'numero': '27999990001', 'texto': 'Oferta'}, **XHR)
pedro.refresh_from_db()
t('contato de outra loja não é marcado', pedro.status == 'NOVO')

print('Descartar e reabrir')
joao = ContatoMailing.objects.get(nome='João Souza')
r = cb.post(f'/orcamentos/mailing/contato/{joao.pk}/', {'acao': 'descartar', 'observacao': 'Já é cliente', 'voltar': '/orcamentos/mailing/?status=NOVO'})
joao.refresh_from_db()
t('descarta com o motivo e volta para a lista', joao.status == 'DESCARTADO' and joao.observacao == 'Já é cliente'
  and joao.trabalhado_por == bia and r['Location'] == '/orcamentos/mailing/?status=NOVO')
r = cb.post(f'/orcamentos/mailing/contato/{joao.pk}/', {'acao': 'reabrir', 'voltar': 'https://site-de-fora.example/'})
joao.refresh_from_db()
t('reabre, e não redireciona para fora do portal', joao.status == 'NOVO' and joao.trabalhado_por is None and r['Location'] == '/orcamentos/mailing/')
r = cc.post(f'/orcamentos/mailing/contato/{joao.pk}/', {'acao': 'descartar'})
joao.refresh_from_db()
t('outra loja não mexe no contato', joao.status == 'NOVO')

print('Gestor limitado à loja dele')
h = html(cg, '/orcamentos/gestao/')
t('vê só as bases da loja dele', 'Clientes outubro' in h and 'Glória nov' not in h)
t('não confere nem encerra base de outra loja', cg.get(f'/orcamentos/gestao/{bg.pk}/conferir/').status_code == 404
  and cg.post(f'/orcamentos/gestao/{bg.pk}/', {'acao': 'encerrar'}).status_code == 404)
cg.post('/orcamentos/gestao/importar/', {'nome': 'Do gestor', 'arquivo': SimpleUploadedFile('g.csv', b'Fone\n27993334444\n', 'text/csv')})
bgs = BaseMailing.objects.get(nome='Do gestor')
h = html(cg, f'/orcamentos/gestao/{bgs.pk}/conferir/')
t('na conferência só aparecem as lojas dele', 'Loja Centro' in h and 'Loja Glória' not in h)
cg.post(f'/orcamentos/gestao/{bgs.pk}/conferir/', {'loja': gloria.pk, 'coluna_numero': '0'})
bgs.refresh_from_db()
t('e não manda para loja que não gere', bgs.status == 'RASCUNHO' and not bgs.contatos.exists())
t('vendedor com mailing de gestor: o gestor trabalha o mailing das lojas dele', 'Nova' in html(cg, '/orcamentos/mailing/'))
cg.post(f'/orcamentos/gestao/{bgs.pk}/', {'acao': 'descartar'})
t('descartar o rascunho apaga a importação', not BaseMailing.objects.filter(pk=bgs.pk).exists())

print('Painel')
Orcamento.objects.create(vendedor=caio, loja=gloria, cliente_nome='X', pilar='FIXA', numero='5527999990001', texto='y', status='ABERTO')
r = cs.get('/orcamentos/painel/')
d = r.context['d']
k = d['kpis']
t('abre para o SUPERADMIN', r.status_code == 200 and 'Quem mais usa' in r.content.decode())
t('equipe = quem está lotado nas lojas', k['equipe'] == 4, k)
t('usam 2 (Ana e Caio), não usam 2 (Bia e Davi)', k['usam'] == 2 and k['nao_usam'] == 2
  and {p['nome'] for p in d['nao_usam']} == {'ZZM Bia', 'ZZM Davi'}, (k, d['nao_usam']))
t('adoção geral 50%', k['adocao'] == 50)
t('1 orçamento veio do mailing', k['do_mailing'] == 1)
por_loja = {l['nome']: l for l in d['por_loja']}
t('por loja: Centro 1 de 2, Glória 1 de 1, Quiosque 0 de 1', (por_loja['Loja Centro']['usaram'], por_loja['Loja Centro']['equipe'],
  por_loja['Loja Glória']['adocao'], por_loja['Quiosque Shopping']['adocao']) == (1, 2, 100, 0), por_loja)
t('mailing por loja', (por_loja['Loja Centro']['mailing_novos'], por_loja['Loja Centro']['mailing_trabalhados']) == (3, 1), por_loja['Loja Centro'])
t('quem nunca usou aparece como "nunca usou"', 'nunca usou' in r.content.decode())
r = cg.get('/orcamentos/painel/')
t('o gestor vê só a loja dele', [l['nome'] for l in r.context['d']['por_loja']] == ['Loja Centro'] and r.context['d']['kpis']['equipe'] == 2)
r = cs.get('/orcamentos/painel/', {'loja': gloria.pk})
t('filtro por loja', [l['nome'] for l in r.context['d']['por_loja']] == ['Loja Glória'])
r = cs.get('/orcamentos/painel/', {'de': '2020-01-01', 'ate': '2020-01-31'})
t('período sem orçamento: todo mundo em "não usam"', r.context['d']['kpis']['orcamentos'] == 0 and r.context['d']['kpis']['nao_usam'] == 4
  and len(r.context['d']['serie']) == 31)
r = cs.get('/orcamentos/painel/', {'de': 'lixo', 'ate': '2026-13-40'})
t('data inválida cai no padrão de 30 dias', r.status_code == 200 and len(r.context['d']['serie']) == 30)
t('vendedor não abre o painel', ca.get('/orcamentos/painel/').status_code == 302)

print('Encerrar a base')
cs.post(f'/orcamentos/gestao/{base.pk}/', {'acao': 'encerrar'})
base.refresh_from_db()
h = html(ca, '/orcamentos/mailing/')
t('base encerrada sai da lista da loja', base.status == 'ENCERRADA' and 'João Souza' not in h and 'Nova' in h)
t('e do contador do início', '>1</p>' in html(ca, '/orcamentos/'))

print(f'\n{ok} OK, {fail} falha(s)')
