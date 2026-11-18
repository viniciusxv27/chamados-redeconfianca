"""/sap/automacao/ (17/11/2026): o SUPERADMIN publica versões da Automação SAP e o app da loja se atualiza sozinho. Banco descartável."""
import io
import json
import os
import pathlib
import sys
import tempfile
import zipfile
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-automacao-sap-'))
(PASTA / 'zz_automacao_sap_settings.py').write_text(f"""
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {{'default': {{'ENGINE': 'django.db.backends.sqlite3', 'NAME': r'{PASTA}/banco.sqlite3'}}}}


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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-asap'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-asap-2'}},
}}
""")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_automacao_sap_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import requests
from django.contrib.auth import get_user_model

from auditoria_sap import automacao
from auditoria_sap.models import ChaveAutomacaoSAP, VersaoAutomacaoSAP
from automacao_sap_cliente import atualizador

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


def novo(u, **k):
    return User.objects.create_user(username=f'zza.{u}', email=f'zza.{u}@exemplo-teste.local', password='S3nha!teste',
                                    first_name=u.title(), last_name='ZZ', **k)


def pacote(nome='AutomacaoSAP_v1.2.1.zip', exe=True, internal=True, extra=None, barra='\\', conteudo=b'MZ-exe'):
    """Zip no formato do real: pasta AutomacaoSAP com barra invertida nos nomes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        b = barra
        if exe:
            zf.writestr(f'AutomacaoSAP{b}AutomacaoSAP.exe', conteudo)
        if internal:
            zf.writestr(f'AutomacaoSAP{b}_internal{b}python315.dll', b'dll')
        zf.writestr(f'AutomacaoSAP{b}config{b}config.json', b'{"pasta_estoque": ""}')
        zf.writestr(f'AutomacaoSAP{b}config{b}centros.json', b'{}')
        zf.writestr(f'AutomacaoSAP{b}logs{b}', b'')
        for n, c in (extra or {}).items():
            zf.writestr(n, c)
    return SimpleUploadedFile(nome, buf.getvalue(), content_type='application/zip')


sa = novo('super', hierarchy='SUPERADMIN')
adm = novo('adm', hierarchy='ADMIN')
comum = novo('comum', hierarchy='PADRAO')
csa, cadm, ccomum, anon = Client(), Client(), Client(), Client()
csa.force_login(sa); cadm.force_login(adm); ccomum.force_login(comum)
U = '/sap/automacao/'

print('Tela')
r = csa.get(U)
h = r.content.decode()
t('SUPERADMIN abre a aba', r.status_code == 200 and 'Enviar versão nova' in h and 'Nenhuma versão publicada' in h)
t('a aba aparece no menu da Visão SAP', 'Automação SAP' in h and 'sap-tab-active' in h)
t('SUPERADMIN vê a chave e o endereço da API', 'data-chave-app' in h and ChaveAutomacaoSAP.get().chave in h
  and 'http://testserver/sap/automacao/api/versao/' in h)
r = cadm.get(U)
h = r.content.decode()
t('ADMIN (vê a Visão SAP) abre, sem formulário nem chave', r.status_code == 200 and 'Enviar versão nova' not in h
  and ChaveAutomacaoSAP.get().chave not in h)
r = ccomum.get(U)
t('quem não vê a Visão SAP não entra', r.status_code == 302)
t('anônimo vai para o login', anon.get(U).status_code == 302)

print('Envio')
r = cadm.post(U + 'enviar/', {'arquivo': pacote()})
t('ADMIN não envia', VersaoAutomacaoSAP.objects.count() == 0)
r = csa.post(U + 'enviar/', {'arquivo': pacote(), 'notas': 'Primeira no portal', 'publicar': 'on'}, follow=True)
v121 = VersaoAutomacaoSAP.objects.filter(versao='1.2.1').first()
t('envia e pega a versão do nome do arquivo', v121 is not None and v121.publicada and v121.notas == 'Primeira no portal',
  [m.message for m in r.context['messages']] if r.context else r.status_code)
t('guarda sha256 e tamanho', v121 and len(v121.sha256) == 64 and v121.tamanho > 0)
t('arquivo salvo com nome da versão', v121 and v121.arquivo.name.endswith('AutomacaoSAP_v1.2.1.zip'))
r = csa.post(U + 'enviar/', {'arquivo': pacote()}, follow=True)
t('mesma versão de novo é recusada', VersaoAutomacaoSAP.objects.count() == 1 and 'já foi enviada' in r.content.decode())


def recusa(nome, arq, trecho, **post):
    antes = VersaoAutomacaoSAP.objects.count()
    r = csa.post(U + 'enviar/', {'arquivo': arq, **post}, follow=True)
    t(nome, VersaoAutomacaoSAP.objects.count() == antes and trecho in r.content.decode(), r.content.decode()[:0])


recusa('sem .exe é recusado', pacote('AutomacaoSAP_v1.3.0.zip', exe=False), 'Não achei o AutomacaoSAP.exe')
recusa('sem _internal é recusado', pacote('AutomacaoSAP_v1.3.0.zip', internal=False), 'pasta _internal')
recusa('caminho para fora (zip slip) é recusado', pacote('AutomacaoSAP_v1.3.0.zip', extra={'..\\..\\Windows\\x.dll': b'x'}),
       'Caminho perigoso')
recusa('arquivo que não é zip é recusado', SimpleUploadedFile('AutomacaoSAP_v1.3.0.zip', b'nada'), 'não é um .zip')
recusa('sem versão no nome nem no campo é recusado', pacote('AutomacaoSAP.zip'), 'formato 1.2.3')

r = csa.post(U + 'enviar/', {'arquivo': pacote('qualquer.zip', barra='/', conteudo=b'MZ-1.10'), 'versao': 'v1.10.0',
                             'publicar': 'on'}, follow=True)
t('versão digitada vale (e aceita zip com barra normal)', VersaoAutomacaoSAP.objects.filter(versao='1.10.0').exists())
r = csa.post(U + 'enviar/', {'arquivo': pacote('AutomacaoSAP_v1.9.0.zip', conteudo=b'MZ-1.9'), 'publicar': 'on'}, follow=True)
t('versão menor enviada depois avisa que a maior continua',
  'continua sendo a oferecida' in r.content.decode() and VersaoAutomacaoSAP.atual().versao == '1.10.0',
  VersaoAutomacaoSAP.atual())
r = csa.post(U + 'enviar/', {'arquivo': pacote('AutomacaoSAP_v2.0.0.zip', conteudo=b'MZ-2'), 'publicar': 'off'}, follow=True)
t('enviar sem publicar não muda a atual', VersaoAutomacaoSAP.atual().versao == '1.10.0'
  and not VersaoAutomacaoSAP.objects.get(versao='2.0.0').publicada)

print('Publicar / despublicar')
v110 = VersaoAutomacaoSAP.objects.get(versao='1.10.0')
csa.post(f'{U}{v110.id}/publicar/')
t('despublicar a atual devolve a maior publicada que sobra (1.9.0)', VersaoAutomacaoSAP.atual().versao == '1.9.0')
cadm.post(f'{U}{v110.id}/publicar/')
t('ADMIN não publica', not VersaoAutomacaoSAP.objects.get(pk=v110.pk).publicada)
csa.post(f'{U}{v110.id}/publicar/')
t('publicar de novo volta a ser a atual', VersaoAutomacaoSAP.atual().versao == '1.10.0')

print('Download pela tela')
v200 = VersaoAutomacaoSAP.objects.get(versao='2.0.0')
r = cadm.get(f'{U}{v110.id}/baixar/')
t('ADMIN baixa a versão publicada', r.status_code == 200 and b''.join(r.streaming_content)[:2] == b'PK'
  and 'AutomacaoSAP_v1.10.0.zip' in r['Content-Disposition'])
t('conta o download', VersaoAutomacaoSAP.objects.get(pk=v110.pk).downloads == 1)
t('não publicada: ADMIN não baixa', cadm.get(f'{U}{v200.id}/baixar/').status_code == 404)
t('não publicada: SUPERADMIN baixa', csa.get(f'{U}{v200.id}/baixar/').status_code == 200)
h = cadm.get(U).content.decode()
t('ADMIN não vê a versão não publicada na lista', 'data-versao="2.0.0"' not in h and 'data-versao="1.10.0"' in h)
t('tela mostra a atual com botão de baixar', 'Baixar a versão 1.10.0' in h)

print('API do app')
chave = ChaveAutomacaoSAP.get().chave
API = '/sap/automacao/api/versao/'
t('sem chave: 403', anon.get(API + '?atual=1.2.1').status_code == 403)
t('chave errada: 403', anon.get(API + '?atual=1.2.1', HTTP_X_CHAVE_AUTOMACAO='errada').status_code == 403)
r = anon.get(API + '?atual=1.2.1', HTTP_X_CHAVE_AUTOMACAO=chave)
d = r.json()
t('com chave: diz a versão atual e manda atualizar', r.status_code == 200 and d['versao'] == '1.10.0' and d['atualizar'] is True
  and d['sha256'] == v110.sha256 and d['tamanho'] == v110.tamanho and d['preservar'] == ['config', 'logs'], d)
t('compara por número, não por texto (1.10.0 > 1.9.0)',
  anon.get(API + '?atual=1.9.0', HTTP_X_CHAVE_AUTOMACAO=chave).json()['atualizar'] is True)
t('já na atual: não atualiza', anon.get(API + '?atual=1.10.0', HTTP_X_CHAVE_AUTOMACAO=chave).json()['atualizar'] is False)
t('mais nova que a publicada (teste local): não atualiza',
  anon.get(API + '?atual=2.0.0', HTTP_X_CHAVE_AUTOMACAO=chave).json()['atualizar'] is False)
t('versão ilegível: não atualiza', anon.get(API + '?atual=abc', HTTP_X_CHAVE_AUTOMACAO=chave).json()['atualizar'] is False)
r = anon.get(d['url'].replace('http://testserver', ''), HTTP_X_CHAVE_AUTOMACAO=chave)
corpo = b''.join(r.streaming_content)
import hashlib
t('o app baixa pela chave e o sha256 confere', r.status_code == 200 and hashlib.sha256(corpo).hexdigest() == v110.sha256)
t('conta a atualização automática', VersaoAutomacaoSAP.objects.get(pk=v110.pk).atualizacoes == 1)
t('download da API sem chave: 403', anon.get(d['url'].replace('http://testserver', '')).status_code == 403)
t('versão não publicada não sai pela API',
  anon.get('/sap/automacao/api/baixar/2.0.0/', HTTP_X_CHAVE_AUTOMACAO=chave).status_code == 404)
csa.post(U + 'chave/')
t('trocar a chave invalida a antiga', anon.get(API + '?atual=1.2.1', HTTP_X_CHAVE_AUTOMACAO=chave).status_code == 403
  and anon.get(API, HTTP_X_CHAVE_AUTOMACAO=ChaveAutomacaoSAP.get().chave).status_code == 200)
cadm.post(U + 'chave/')
t('ADMIN não troca a chave', anon.get(API, HTTP_X_CHAVE_AUTOMACAO=ChaveAutomacaoSAP.get().chave).status_code == 200)

print('App dentro do portal (Ponte)')
from unittest import mock as _mock  # noqa: E402

from django.core import signing  # noqa: E402

from auditoria_sap.models import InstaladorPonteSAP  # noqa: E402
from core.models import SystemLog  # noqa: E402

h = cadm.get(U).content.decode()
t('a tela traz a janela do app no portal', 'id="pnApp"' in h and 'auditoria_sap/ponte.js' in h
  and 'data-sessao="/sap/automacao/ponte/sessao/"' in h and 'automacao_logo_claro.png' in h)
t('sem instalador da ponte publicado, não oferece download', 'data-baixar-ponte=""' in h)
t('o texto não diz mais que o app não roda pelo navegador', 'não roda pelo navegador' not in h and 'Aqui no portal' in h)
SESSAO = '/sap/automacao/ponte/sessao/'
r = cadm.get(SESSAO)
j = r.json()
t('a página pega o token da sessão e a chave para parear a ponte', r.status_code == 200 and j['token']
  and j['chave'] == ChaveAutomacaoSAP.get().chave and r['Cache-Control'] == 'no-store' and j['portal'] == 'http://testserver')
t('quem não vê a Visão SAP não pega sessão', ccomum.get(SESSAO).status_code == 302 and anon.get(SESSAO).status_code == 302)
CONF = '/sap/automacao/api/ponte/conferir/'


def conferir(token, chave_=None):
    return anon.get(CONF, {'token': token, 'pc': 'LOJA-01', 'ponte': '1.0.0'},
                    HTTP_X_CHAVE_AUTOMACAO=ChaveAutomacaoSAP.get().chave if chave_ is None else chave_)


r = conferir(j['token'])
t('a ponte confere o token: quem é', r.status_code == 200 and r.json() == {'ok': True, 'usuario': adm.get_full_name()}, r.content)
t('fica registrado quem abriu e em qual PC', SystemLog.objects.filter(user=adm, description__contains='PC LOJA-01').exists())
t('chave errada: recusa', conferir(j['token'], 'errada').status_code == 403)
t('token adulterado: recusa', conferir(j['token'] + 'x').status_code == 403)
with _mock.patch('time.time', return_value=__import__('time').time() - 600):
    velho = signing.dumps({'u': adm.pk}, salt='auditoria_sap.ponte', compress=True)
r = conferir(velho)
t('token velho (mais de 2 min): pede para recarregar', r.status_code == 403 and 'recarregue' in r.json()['erro'])
t('quem não pode ver a Visão SAP não usa, nem com token', conferir(signing.dumps({'u': comum.pk}, salt='auditoria_sap.ponte')).status_code == 403)
inativo = novo('inativo', hierarchy='ADMIN', is_active=False)
t('nem quem foi desativado', conferir(signing.dumps({'u': inativo.pk}, salt='auditoria_sap.ponte')).status_code == 403)
ENVIAR = '/sap/automacao/ponte/enviar/'
exe = SimpleUploadedFile('PonteAutomacaoSAP_v1.0.0.exe', b'MZ' + b'\0' * 2000, content_type='application/octet-stream')
cadm.post(ENVIAR, {'arquivo': exe})
t('só o SUPERADMIN publica a ponte', not InstaladorPonteSAP.objects.exists())
csa.post(ENVIAR, {'arquivo': SimpleUploadedFile('ponte.zip', b'PK', content_type='application/zip')})
csa.post(ENVIAR, {'arquivo': SimpleUploadedFile('ponte.exe', b'XX nao e exe', content_type='application/octet-stream')})
t('só .exe de verdade (cabeçalho MZ)', not InstaladorPonteSAP.objects.exists())
csa.post(ENVIAR, {'arquivo': SimpleUploadedFile('PonteAutomacaoSAP_v1.0.0.exe', b'MZ' + b'\0' * 2000)})
inst = InstaladorPonteSAP.atual()
t('a ponte é publicada com versão e sha256', inst and inst.versao == '1.0.0' and len(inst.sha256) == 64 and inst.tamanho == 2002)
h = cadm.get(U).content.decode()
t('a janela passa a oferecer o download da ponte', 'data-baixar-ponte="/sap/automacao/ponte/baixar/"' in h)
r = cadm.get('/sap/automacao/ponte/baixar/')
t('baixar a ponte entrega o .exe e conta', r.status_code == 200 and b''.join(r.streaming_content).startswith(b'MZ')
  and 'PonteAutomacaoSAP_v1.0.0.exe' in r['Content-Disposition'] and InstaladorPonteSAP.atual().downloads == 1)
t('quem não vê a Visão SAP não baixa a ponte', ccomum.get('/sap/automacao/ponte/baixar/').status_code == 302)
embutido = PASTA / 'PonteAutomacaoSAP.exe'
embutido.write_bytes(b'MZ da imagem')
with _mock.patch.object(automacao, 'ponte_embutida', return_value=('1.0.0', embutido)):
    r = cadm.get('/sap/automacao/ponte/baixar/')
    t('a ponte gerada na construção da imagem vale sobre a enviada de mesma versão', r.status_code == 200
      and b''.join(r.streaming_content) == b'MZ da imagem' and InstaladorPonteSAP.atual().downloads == 1)
    t('a tela diz de onde veio', 'gerada na construção do portal' in csa.get(U).content.decode())
    csa.post(ENVIAR, {'arquivo': SimpleUploadedFile('PonteAutomacaoSAP_v1.1.0.exe', b'MZ enviada nova')})
    r = cadm.get('/sap/automacao/ponte/baixar/')
    t('enviada de versão maior passa na frente da imagem', b''.join(r.streaming_content) == b'MZ enviada nova'
      and 'v1.1.0' in r['Content-Disposition'])
InstaladorPonteSAP.objects.all().delete()
with _mock.patch.object(automacao, 'ponte_embutida', return_value=('1.0.0', embutido)):
    h = cadm.get(U).content.decode()
    t('sem nada enviado, a da imagem já é oferecida às lojas', 'data-baixar-ponte="/sap/automacao/ponte/baixar/"' in h)
with _mock.patch.object(automacao, 'ponte_embutida', return_value=None):
    t('sem imagem e sem envio, não oferece', 'data-baixar-ponte=""' in cadm.get(U).content.decode()
      and cadm.get('/sap/automacao/ponte/baixar/').status_code == 404)
t('lê a versão da ponte do próprio código', automacao.ponte_embutida() is None or automacao.ponte_embutida()[0] == '1.0.0')

print('Atualizador do app (lado da loja)')
chave = ChaveAutomacaoSAP.get().chave


class Resposta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def abrir_no_portal(pedido, timeout=None):
    """Faz o urllib do app falar com o portal de teste."""
    url = pedido.full_url.replace('http://testserver', '')
    r = anon.get(url, HTTP_X_CHAVE_AUTOMACAO=pedido.get_header('X-chave-automacao') or '')
    corpo = b''.join(r.streaming_content) if r.streaming else r.content
    if r.status_code != 200:
        raise OSError(f'HTTP {r.status_code}')
    return Resposta(corpo)


info = atualizador.consultar('1.2.1', 'http://testserver', chave, abrir=abrir_no_portal)
t('consultar devolve a versão nova', info['versao'] == '1.10.0' and info['atualizar'])
pasta = pathlib.Path(tempfile.mkdtemp(prefix='zz-asap-cliente-'))
info_local = dict(info, url=info['url'].replace('http://testserver', 'http://localhost'))


def abrir_local(pedido, timeout=None):
    pedido.full_url = pedido.full_url.replace('http://localhost', 'http://testserver')
    return abrir_no_portal(pedido, timeout)


zipado = atualizador.baixar(info_local, str(pasta / 'p.zip'), chave, abrir=abrir_local)
t('baixar confere o sha256 e grava', os.path.getsize(zipado) == info['tamanho'])
try:
    atualizador.baixar(dict(info_local, sha256='0' * 64), str(pasta / 'x.zip'), chave, abrir=abrir_local)
    t('sha256 diferente é recusado', False)
except ValueError as e:
    t('sha256 diferente é recusado', 'não confere' in str(e))
try:
    atualizador.baixar(dict(info, url='http://outro.com/x.zip'), str(pasta / 'y.zip'), chave, abrir=abrir_local)
    t('endereço sem https é recusado', False)
except ValueError:
    t('endereço sem https é recusado', True)
raiz = atualizador.extrair(zipado, str(pasta / 'novo'))
t('extrai o zip com barra invertida e acha a pasta do .exe',
  raiz.endswith('AutomacaoSAP') and os.path.exists(os.path.join(raiz, '_internal', 'python315.dll')))
ruim = pasta / 'ruim.zip'
with zipfile.ZipFile(ruim, 'w') as zf:
    zf.writestr('AutomacaoSAP\\AutomacaoSAP.exe', b'x')
    zf.writestr('..\\..\\fora.txt', b'x')
atualizador.extrair(str(ruim), str(pasta / 'ruim'))
t('extrair ignora caminho para fora da pasta', not (pasta.parent / 'fora.txt').exists() and not (pasta / 'fora.txt').exists())
bat = atualizador.script_de_troca(r'C:\T\novo\AutomacaoSAP', r'C:\AutomacaoSAP', 1234, r'C:\AutomacaoSAP\_versao_anterior')
t('o .bat espera o app fechar', 'PID eq 1234' in bat and 'goto espera' in bat and 'ping -n 2' in bat)
t('o .bat troca _internal e o .exe sem tocar em config e logs',
  r'robocopy "%NOVO%\_internal" "%APP%\_internal" /MIR' in bat and r'/XD "%NOVO%\_internal" "%NOVO%\config" "%NOVO%\logs"' in bat
  and r'"%APP%\logs" /MIR' not in bat and r'"%APP%\config" /MIR' not in bat)
t('o .bat só acrescenta config nova (não sobrescreve)', '/XC /XN /XO' in bat)
t('o .bat volta atrás se a cópia falhar e reabre o app', ':volta' in bat and 'errorlevel 8' in bat and 'start ""' in bat)
t('fora do .exe empacotado não faz nada', atualizador.atualizar_se_preciso('1.0.0') is False)
t('versões comparadas por número', atualizador.versao_em_tupla('1.10.0') > atualizador.versao_em_tupla('1.9.9'))

print('Pacote real (Downloads), só conferido')
real = pathlib.Path('/Users/teste/Downloads/AutomacaoSAP_v1.2.1.zip')
if real.exists():
    with open(real, 'rb') as f:
        sha, tam = automacao.conferir_zip(f)
    t('o zip 1.2.1 de verdade passa na conferência', len(sha) == 64 and tam == real.stat().st_size)
    t('a versão sai do nome do arquivo real', automacao.versao_do_nome(real.name) == '1.2.1')
else:
    print('  (pulado: zip real não está em Downloads)')

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
