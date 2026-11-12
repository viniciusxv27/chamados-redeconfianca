"""Acess (12/11/2026): XML do GEFAC mandado pelo RH no pré-cadastro e na edição do usuário + tema escuro do Acess.
Sem ficha ainda, o XML espera e entra na Etapa 1 quando a ficha nasce (aprovação do pré-cadastro).
Banco e arquivos descartáveis (SQLite + pasta temporária); nada sai para a rede.
"""
import os
import pathlib
import sys
import tempfile
from unittest import mock

import django

# Banco e arquivos descartáveis: o schema sai direto dos models (`--run-syncdb`),
# nada toca o Postgres compartilhado nem o MinIO.
PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-acess-xml-cad-'))
(PASTA / 'zz_acess_xml_cad_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-axc'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-axc-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_acess_xml_cad_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import pathlib as _pl
import re

import requests
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.messages import get_messages

from acess import servicos as sv
from acess.models import Colaborador, LogAcess, XmlGefac, XmlGefacPendente
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

XML = b'<?xml version="1.0" encoding="UTF-8"?><pedido><perfil>CONSULTOR</perfil><nome>ZZ Fulana</nome></pedido>'


def xml(nome='pedido.xml', conteudo=XML):
    return SimpleUploadedFile(nome, conteudo, content_type='application/xml')


def msgs(r):
    return ' | '.join(str(m) for m in get_messages(r.wsgi_request))


loja = Sector.objects.create(name='Loja ZZ Xml Cad', adabas='ESZZX02-001')
rh = User.objects.create_user(username='zzx.rh', email='zzx.rh@exemplo-teste.local', password='S3nha!teste',
                              first_name='Rita', last_name='RH', hierarchy='SUPERADMIN', is_superuser=True)
crh = Client()
crh.force_login(rh)

print('pré-cadastro: tela')
r = crh.get('/users/manage/users/pre-register/')
corpo = r.content.decode()
t('tela abre', r.status_code == 200, r.status_code)
t('formulário aceita arquivo', 'enctype="multipart/form-data"' in corpo)
t('campo XML do GEFAC opcional', 'name="xml_gefac"' in corpo and 'XML do GEFAC' in corpo and '(opcional)' in corpo)
t('campo não é obrigatório', not re.search(r'name="xml_gefac"[^>]*required', corpo))


def pre(email, arquivo=None):
    dados = {'full_name': 'ZZ Fulana ' + email.split('@')[0], 'email': email, 'admission_date': '2026-11-20',
             'hierarchy': 'PADRAO', 'sector': loja.pk}
    if arquivo is not None:
        dados['xml_gefac'] = arquivo
    return crh.post('/users/manage/users/pre-register/', dados)


print('pré-cadastro: XML recusado não cria ninguém')
casos = [
    ('quebrado', xml(conteudo=b'<pedido><perfil>sem fechar</pedido>'), 'não é um XML válido'),
    ('doctype', xml(conteudo=b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'), 'DOCTYPE'),
    ('grande', xml(conteudo=b'<a>' + b'x' * (5 * 1024 * 1024) + b'</a>'), '5 MB'),
    ('extensão', xml(nome='pedido.txt'), '.xml'),
    ('vazio', xml(conteudo=b''), None),
]
for rotulo, arq, trecho in casos:
    email = f'zzx.ruim.{rotulo.replace("ã", "a")}@exemplo-teste.local'
    r = pre(email, arq)
    m = msgs(r)
    criado = User.objects.filter(email=email).exists()
    if rotulo == 'vazio':
        t('XML vazio: recusado com mensagem', 'O arquivo está vazio' in m and not criado, m[:200])
        continue
    t(f'XML {rotulo}: recusado com mensagem', 'XML do GEFAC' in m and trecho in m and r.status_code == 200, m[:200])
    t(f'XML {rotulo}: pré-cadastro não criado', not criado)
t('nada ficou guardado das recusas', not XmlGefacPendente.objects.exists() and not XmlGefac.objects.exists())

print('pré-cadastro: sem arquivo segue normal')
r = pre('zzx.sem@exemplo-teste.local')
sem = User.objects.get(email='zzx.sem@exemplo-teste.local')
t('cria e vai para o link', r.status_code == 302 and 'pre-register-link' in r['Location'], r.status_code)
t('sem XML guardado', not XmlGefacPendente.objects.filter(user=sem).exists())

print('pré-cadastro: com XML fica guardado até a ficha nascer')
r = pre('zzx.com@exemplo-teste.local', xml('Pedido GEFAC Fulana.xml'))
fulana = User.objects.get(email='zzx.com@exemplo-teste.local')
p = XmlGefacPendente.objects.filter(user=fulana).first()
t('cria o pré-cadastro', r.status_code == 302 and fulana.pre_registration_status == User.PRE_REG_PENDING)
t('XML guardado esperando a ficha', p is not None and p.origem == 'PRE_CADASTRO' and p.enviado_por_id == rh.pk
  and p.nome_original == 'Pedido GEFAC Fulana.xml' and p.tamanho == len(XML))
t('arquivo no armazenamento com nome aleatório', p and p.arquivo.name.startswith('acess/gefac/')
  and 'Fulana' not in p.arquivo.name and p.arquivo.open('rb').read() == XML)
t('ainda sem ficha no Acess', not Colaborador.objects.filter(user=fulana).exists())
t('mensagem diz que entra na aprovação', 'quando o pré-cadastro for aprovado' in msgs(r), msgs(r))

print('edição de quem ainda está no pré-cadastro')
r = crh.get(f'/users/manage/users/{fulana.pk}/edit/')
corpo = r.content.decode()
t('edição abre com o campo', r.status_code == 200 and 'name="xml_gefac"' in corpo, r.status_code)
t('mostra o XML aguardando a ficha', 'data-xml-situacao="pendente"' in corpo and 'Pedido GEFAC Fulana.xml' in corpo)


def editar(u, arquivo=None, **extra):
    dados = {'email': u.email, 'username': u.username, 'full_name': u.get_full_name(), 'hierarchy': u.hierarchy,
             'sector': u.sector_id or '', 'status': 'ATIVO' if u.is_active else 'INATIVO', 'job_title': u.job_title,
             'cpf': u.cpf or ''}
    dados.update(extra)
    if arquivo is not None:
        dados['xml_gefac'] = arquivo
    return crh.post(f'/users/manage/users/{u.pk}/edit/', dados)


fulana.refresh_from_db()
r = editar(fulana, xml('versao2.xml', XML.replace(b'CONSULTOR', b'GERENTE')), status='INATIVO')
t('edição com XML guarda mais um pendente', XmlGefacPendente.objects.filter(user=fulana).count() == 2, msgs(r))
t('o segundo veio da edição', XmlGefacPendente.objects.filter(user=fulana, origem='EDICAO').exists())

print('aprovação leva os XMLs para a Etapa 1')
fulana.refresh_from_db()
fulana.pre_registration_status = User.PRE_REG_COMPLETED
fulana.cpf = '52998224725'
fulana.save()
nomes_antes = set(XmlGefacPendente.objects.filter(user=fulana).values_list('arquivo', flat=True))
r = crh.post(f'/users/manage/users/{fulana.pk}/pre-register/approve/')
c = Colaborador.objects.filter(user=fulana).first()
t('aprovado vira ficha na Etapa 1', c is not None and c.origem == 'PRE_CADASTRO' and c.etapa_calculada == 1, r.status_code)
anexos = list(c.xmls_gefac.all()) if c else []
t('os dois XMLs estão na ficha', len(anexos) == 2 and not XmlGefacPendente.objects.filter(user=fulana).exists())
t('mesmo arquivo, sem cópia', {a.arquivo.name for a in anexos} == nomes_antes)
t('o mais novo é o da edição', anexos and anexos[0].nome_original == 'versao2.xml' and anexos[0].origem == 'EDICAO')
t('quem mandou continua registrado', all(a.enviado_por_id == rh.pk for a in anexos))
logs = LogAcess.objects.filter(colaborador=c, acao='XML_GEFAC_ANEXADO')
t('histórico do Acess diz de onde veio e quem mandou',
  logs.count() == 2 and logs.filter(descricao__contains='veio pelo pré-cadastro', usuario=rh).exists()
  and logs.filter(descricao__contains='veio pela edição do usuário').exists()
  and all('Rita RH' in l.descricao for l in logs), list(logs.values_list('descricao', flat=True)))

print('Etapa 1 do Acess mostra e baixa')
r = crh.get('/acess/pendentes/?etapa=1')
corpo = r.content.decode()
t('Etapa 1 lista o XML', r.status_code == 200 and 'versao2.xml' in corpo and 'pela edição do usuário' in corpo, r.status_code)
url = f'/acess/colaborador/{c.pk}/xml-gefac/{anexos[0].pk}/'
t('link de baixar na Etapa 1', url in corpo)
r = crh.get(url)
t('baixa o arquivo certo', r.status_code == 200 and b''.join(r.streaming_content) == XML.replace(b'CONSULTOR', b'GERENTE')
  and 'attachment' in r['Content-Disposition'], r.status_code)
r = crh.get(f'/acess/colaborador/{c.pk}/')
corpo = r.content.decode()
t('ficha mostra a origem', r.status_code == 200 and 'veio do pré-cadastro' in corpo and 'veio da edição do usuário' in corpo)

print('edição de quem já tem ficha: entra direto')
fulana.refresh_from_db()
fulana.is_active = True
fulana.status = 'ATIVO'
fulana.save()
r = editar(fulana, xml('terceiro.xml'))
t('anexado direto na ficha', c.xmls_gefac.count() == 3 and c.xmls_gefac.first().origem == 'EDICAO'
  and c.xmls_gefac.first().nome_original == 'terceiro.xml', msgs(r))
t('mensagem fala da Etapa 1', 'já aparece na Etapa 1' in msgs(r), msgs(r))
t('histórico: anexou pela edição do usuário', LogAcess.objects.filter(
    colaborador=c, descricao__contains='Anexou o XML do perfil GEFAC', usuario=rh).filter(
    descricao__contains='pela edição do usuário').exists())
r = crh.get(f'/users/manage/users/{fulana.pk}/edit/')
t('edição mostra o XML que está na ficha', 'data-xml-situacao="ficha"' in r.content.decode() and 'terceiro.xml' in r.content.decode())

print('edição com XML recusado não grava nada')
r = editar(fulana, xml(conteudo=b'<a><b></a>'), job_title='CARGO NOVO ZZ')
fulana.refresh_from_db()
t('volta para a edição com mensagem', r.status_code == 302 and f'/users/manage/users/{fulana.pk}/edit/' in r['Location']
  and 'XML do GEFAC' in msgs(r), msgs(r))
t('nenhum campo foi salvo', fulana.job_title != 'CARGO NOVO ZZ')
t('nenhum XML a mais', c.xmls_gefac.count() == 3)

print('edição sem arquivo segue normal')
r = editar(fulana, job_title='CARGO NOVO ZZ')
fulana.refresh_from_db()
t('salva e volta para a lista', r.status_code == 302 and fulana.job_title == 'CARGO NOVO ZZ', r.status_code)
t('sem XML novo', c.xmls_gefac.count() == 3)

print('usuário ativo sem ficha e fora do pré-cadastro')
beto = User.objects.create_user(username='zzx.beto', email='zzx.beto@exemplo-teste.local', password='S3nha!teste',
                                first_name='Beto', last_name='Portal', hierarchy='PADRAO', sector=loja)
r = editar(beto, xml('beto.xml'))
cb = Colaborador.objects.filter(user=beto).first()
t('entra na esteira (Etapa 1) com o XML', cb is not None and cb.origem == 'PORTAL' and cb.etapa_calculada == 1
  and cb.xmls_gefac.count() == 1, msgs(r))

print('ficha antiga (sem usuário) com o mesmo CPF')
antiga = sv.criar_colaborador({'nome': 'ZZ Carla Antiga', 'cpf': '11144477735'}, loja, usuario=rh)
carla = User.objects.create_user(username='zzx.carla', email='zzx.carla@exemplo-teste.local', password='S3nha!teste',
                                 first_name='Carla', last_name='Antiga', hierarchy='PADRAO', sector=loja, cpf='111.444.777-35')
r = editar(carla, xml('carla.xml'))
antiga.refresh_from_db()
t('liga a ficha antiga em vez de criar outra', antiga.user_id == carla.pk and antiga.xmls_gefac.count() == 1
  and Colaborador.objects.filter(cpf='11144477735').count() == 1, msgs(r))

print('usuário inativo sem ficha')
dora = User.objects.create_user(username='zzx.dora', email='zzx.dora@exemplo-teste.local', password='S3nha!teste',
                                first_name='Dora', last_name='Inativa', hierarchy='PADRAO', sector=loja, is_active=False)
dora.status = 'INATIVO'
dora.save()
r = editar(dora, xml('dora.xml'))
t('fica aguardando (não cria ficha de quem está inativo)', not Colaborador.objects.filter(user=dora).exists()
  and XmlGefacPendente.objects.filter(user=dora, origem='EDICAO').exists(), msgs(r))
c_dora = sv.criar_colaborador({'nome': 'ZZ Dora Inativa', 'cpf': '39053344705'}, loja, usuario=rh, user=dora, origem='PORTAL')
t('cadastro pelo Acess traz o XML pendente', c_dora.xmls_gefac.count() == 1 and not XmlGefacPendente.objects.filter(user=dora).exists())

print('usuário inativo fora do pré-cadastro: espera a reativação')
dani = User.objects.create_user(username='zzx.dani', email='zzx.dani@exemplo-teste.local', password='S3nha!teste',
                                first_name='Dani', last_name='Volta', hierarchy='PADRAO', sector=loja, is_active=False)
dani.status = 'INATIVO'
dani.save()
r = editar(dani, xml('dani.xml'))
m = msgs(r)
t('mensagem fala da reativação, não do pré-cadastro', 'quando o usuário for reativado' in m
  and 'pré-cadastro' not in m, m)
r = crh.get(f'/users/manage/users/{dani.pk}/edit/')
corpo = r.content.decode()
t('edição mostra que espera a reativação', 'data-xml-situacao="pendente"' in corpo
  and 'entra na Etapa 1 quando o usuário for reativado' in corpo)
dani.refresh_from_db()
r = editar(dani, status='ATIVO')
dani.refresh_from_db()
cd = Colaborador.objects.filter(user=dani).first()
t('reativado pela edição: entra na Etapa 1 com o XML', dani.is_active and cd is not None
  and cd.etapa_calculada == 1 and cd.xmls_gefac.count() == 1 and cd.xmls_gefac.first().nome_original == 'dani.xml'
  and not XmlGefacPendente.objects.filter(user=dani).exists(), msgs(r))
t('histórico registra quem mandou o XML', LogAcess.objects.filter(
    colaborador=cd, acao='XML_GEFAC_ANEXADO', usuario=rh, descricao__contains='pela edição do usuário').exists())
r = crh.get('/acess/pendentes/?etapa=1')
t('aparece na Etapa 1 para baixar', 'dani.xml' in r.content.decode())
gil = User.objects.create_user(username='zzx.gil', email='zzx.gil@exemplo-teste.local', password='S3nha!teste',
                               first_name='Gil', last_name='Sem', hierarchy='PADRAO', sector=loja, is_active=False)
gil.is_active = True
gil.save()
t('reativar sem XML esperando não cria ficha', not Colaborador.objects.filter(user=gil).exists())

print('readmissão: ficha ligada já desligada')
eva = User.objects.create_user(username='zzx.eva', email='zzx.eva@exemplo-teste.local', password='S3nha!teste',
                               first_name='Eva', last_name='Volta', hierarchy='PADRAO', sector=loja)
velha = sv.criar_colaborador({'nome': 'EVA VOLTA', 'cpf': '86288366757'}, loja, usuario=rh, user=eva, origem='PORTAL')
Colaborador.objects.filter(pk=velha.pk).update(status=Colaborador.DESLIGADO)
r = editar(eva, xml('eva.xml'))
velha.refresh_from_db()
nova = Colaborador.objects.filter(user=eva).first()
t('ficha nova na Etapa 1 com o XML', nova is not None and nova.pk != velha.pk and nova.etapa_calculada == 1
  and nova.xmls_gefac.count() == 1 and 'já aparece na Etapa 1' in msgs(r), msgs(r))
t('ficha desligada fica no arquivo, sem o XML', velha.status == Colaborador.DESLIGADO and velha.user_id is None
  and velha.xmls_gefac.count() == 0)
t('histórico da ficha antiga explica a readmissão', LogAcess.objects.filter(
    colaborador=velha, acao='VINCULO', descricao__contains='readmissão', usuario=rh).exists())
r = crh.get('/acess/pendentes/?etapa=1')
t('XML da readmissão aparece na Etapa 1', 'eva.xml' in r.content.decode())

print('readmissão de inativo: espera e entra ao reativar')
ivo = User.objects.create_user(username='zzx.ivo', email='zzx.ivo@exemplo-teste.local', password='S3nha!teste',
                               first_name='Ivo', last_name='Volta', hierarchy='PADRAO', sector=loja)
velha_ivo = sv.criar_colaborador({'nome': 'IVO VOLTA', 'cpf': '71428793860'}, loja, usuario=rh, user=ivo, origem='PORTAL')
Colaborador.objects.filter(pk=velha_ivo.pk).update(status=Colaborador.DESLIGADO)
ivo.is_active = False
ivo.status = 'INATIVO'
ivo.save()
r = editar(ivo, xml('ivo.xml'))
velha_ivo.refresh_from_db()
t('inativo: XML espera, ficha antiga intocada', XmlGefacPendente.objects.filter(user=ivo).count() == 1
  and velha_ivo.user_id == ivo.pk and velha_ivo.xmls_gefac.count() == 0
  and 'quando o usuário for reativado' in msgs(r), msgs(r))
r = crh.get(f'/users/manage/users/{ivo.pk}/edit/')
t('edição não mostra o XML antigo da ficha desligada como atual', 'data-xml-situacao="ficha"' not in r.content.decode())
ivo.refresh_from_db()
editar(ivo, status='ATIVO')
velha_ivo.refresh_from_db()
nova_ivo = Colaborador.objects.filter(user=ivo).first()
t('reativado: ficha nova na Etapa 1 com o XML', nova_ivo is not None and nova_ivo.pk != velha_ivo.pk
  and nova_ivo.etapa_calculada == 1 and nova_ivo.xmls_gefac.count() == 1 and velha_ivo.user_id is None
  and not XmlGefacPendente.objects.filter(user=ivo).exists())

print('pré-cadastro aprovado de quem tem ficha desligada')
lia = User.objects.create_user(username='zzx.lia', email='zzx.lia@exemplo-teste.local', password='S3nha!teste',
                               first_name='Lia', last_name='Volta', hierarchy='PADRAO', sector=loja)
velha_lia = sv.criar_colaborador({'nome': 'LIA VOLTA', 'cpf': '29537995593'}, loja, usuario=rh, user=lia, origem='PORTAL')
Colaborador.objects.filter(pk=velha_lia.pk).update(status=Colaborador.DESLIGADO)
lia.pre_registration_status = User.PRE_REG_COMPLETED
lia.save()
r = editar(lia, xml('lia.xml'))
t('pré-cadastro em aberto: XML espera a aprovação', XmlGefacPendente.objects.filter(user=lia).count() == 1
  and 'quando o pré-cadastro for aprovado' in msgs(r), msgs(r))
crh.post(f'/users/manage/users/{lia.pk}/pre-register/approve/')
velha_lia.refresh_from_db()
nova_lia = Colaborador.objects.filter(user=lia).first()
t('aprovado: ficha nova na Etapa 1 com o XML, a desligada fica no arquivo', nova_lia is not None
  and nova_lia.pk != velha_lia.pk and nova_lia.etapa_calculada == 1 and nova_lia.xmls_gefac.count() == 1
  and velha_lia.user_id is None and velha_lia.status == Colaborador.DESLIGADO)

print('o anexar do próprio Acess continua igual')
r = crh.post(f'/acess/colaborador/{c.pk}/xml-gefac/', {'xml': xml('pelo_acess.xml')})
t('anexa pelo Acess com origem Acess', c.xmls_gefac.first().nome_original == 'pelo_acess.xml'
  and c.xmls_gefac.first().origem == 'ACESS', msgs(r))
r = crh.post(f'/acess/colaborador/{c.pk}/xml-gefac/', {'xml': xml(conteudo=b'nada')})
t('XML inválido pelo Acess continua recusado', c.xmls_gefac.count() == 4 and 'não é um XML válido' in msgs(r), msgs(r))

print('tema escuro do Acess')
base = _pl.Path('templates/acess/base.html').read_text()
t('tokens claros e escuros', ':root {' in base and 'html.dark {' in base and '--ac-sup:var(--tema-superficie' in base)
estilo = base[base.index('<style>'):base.index('</style>')]
regras = re.sub(r':root \{.*?\}\s*html\.dark \{.*?\}', '', estilo, flags=re.S)
fixos = re.findall(r'background(?:-color)?\s*:\s*(#fff\b|#FFF\b|#F9FAFB|#FAFAFA|#FFFBEB|#FEF3C7|#DCFCE7)', regras)
fixos += re.findall(r'[^-]color\s*:\s*(#111827|#374151|#4B5563|#6B7280)', regras)
t('nenhuma cor clara fixa fora dos tokens', not fixos, fixos)
t('conteúdo dentro de .ac-raiz', '<div class="ac-raiz">' in base)
hex_nos_templates = []
for arq in _pl.Path('templates/acess').glob('*.html'):
    if arq.name == 'base.html':
        continue
    if re.search(r'#[0-9A-Fa-f]{6}\b|bg-\[#|text-\[#', arq.read_text()):
        hex_nos_templates.append(arq.name)
t('telas do Acess sem cor fixa no HTML', not hex_nos_templates, hex_nos_templates)
paginas = ['/acess/', '/acess/pendentes/', '/acess/colaboradores/', f'/acess/colaborador/{c.pk}/', '/acess/chamados/',
           '/acess/solicitacoes/', '/acess/desligamentos/', '/acess/historico/', '/acess/acessos/', '/acess/popups/',
           '/acess/lojas/', '/acess/liberados/', '/acess/mensagens/']
for u in paginas:
    r = crh.get(u)
    t(f'{u} abre com o tema do módulo', r.status_code == 200 and 'class="ac-raiz"' in r.content.decode()
      and '--ac-sup' in r.content.decode(), r.status_code)

print('higiene')
ruins = []
for arq in [_pl.Path('templates/acess/_xml_gefac_cadastro.html'), _pl.Path('templates/acess/base.html'),
            _pl.Path('templates/admin/pre_register_user.html'), _pl.Path('templates/admin/edit_user.html')]:
    for n, linha in enumerate(arq.read_text().splitlines(), 1):
        if '{#' in linha and '#}' not in linha:
            ruins.append(f'{arq}:{n}')
t('comentário {# #} sempre em uma linha', not ruins, ruins)
vazou = [u for u in ['/users/manage/users/pre-register/', f'/users/manage/users/{fulana.pk}/edit/', '/acess/pendentes/']
         if any(x in crh.get(u).content.decode() for x in ('{#', '#}', '{% comment'))]
t('nenhum comentário de template vaza na tela', not vazou, vazou)

print(f'\n{ok} OK, {fail} falha(s)')
