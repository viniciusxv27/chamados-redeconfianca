"""Navegador do assistente (BIs do /power-bi/ e links): regras de endereço, acesso e sessão.

Banco descartável; a Anthropic é um dublê. O navegador de verdade só roda no
último bloco (opcional: pulado sem Playwright/Chromium) e só fala com um
servidor HTTP local deste próprio teste.

Rodar: venv/bin/python teste_assistente_navegador.py
"""
import os
import pathlib
import sys
import tempfile
import types
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-assist-nav-'))
(PASTA / 'zz_assist_nav_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-assist-nav'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-assist-nav-2'}},
}}
BASE_URL = 'https://portal.exemplo-teste.com.br'
AWS_S3_ENDPOINT_URL = 'https://minio.exemplo-teste.com.br'
NAVEGADOR_HOSTS_BLOQUEADOS = 'evolution.exemplo-teste.com.br'
NAVEGADOR_CDP_URL = ''
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_assist_nav_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
os.environ.pop('NAVEGADOR_CDP_URL', None)


class _Anthropic:                       # o pacote não está no venv local; nada aqui fala com o Claude
    def __init__(self, *a, **k):
        raise AssertionError('teste tentou falar com o Claude')


sys.modules.setdefault('anthropic', types.SimpleNamespace(Anthropic=_Anthropic))

django.setup()

from django.core.management import call_command
from django.test.utils import setup_test_environment

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model

import base64
import http.server
import threading
import time

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


from assistente import claude_client, ferramentas  # noqa: E402
from assistente import ferramentas_navegador as N  # noqa: E402
from communications.models import CommunicationGroup  # noqa: E402
from power_bi.models import PowerBIReport  # noqa: E402
from users.models import Sector  # noqa: E402

# DNS de mentira: nada sai para a rede nas regras de endereço.
DNS = {
    'publico.exemplo.com': {'93.184.216.34'},
    'app.powerbi.com': {'20.42.1.1'},
    'interno.exemplo.com': {'10.1.2.3'},
    'metadados.exemplo.com': {'169.254.169.254'},
    'misto.exemplo.com': {'93.184.216.34', '192.168.0.10'},
    'v6local.exemplo.com': {'::1'},
    'mapeado.exemplo.com': {'::ffff:127.0.0.1'},
    'cgnat.exemplo.com': {'100.64.0.9'},
    'portal.exemplo-teste.com.br': {'93.184.216.35'},
    'parciais.exemplo.com': {'93.184.216.36'},
}


def dns_falso(host, porta):
    if host in DNS:
        return DNS[host]
    raise N.socket.gaierror('sem DNS no teste')


mock.patch.object(N, '_resolver', dns_falso).start()


def roda(nome, args, user):
    return ferramentas.executar(nome, args, user, turno='turno-1')


print('regras de endereço (SSRF)')
bloqueados = {
    'http://127.0.0.1/': 'loopback',
    'http://localhost/admin': 'localhost',
    'http://10.0.0.5/': '10/8',
    'http://172.16.3.4/': '172.16/12',
    'http://192.168.1.1/': '192.168/16',
    'http://169.254.169.254/latest/meta-data/': 'link-local (metadados)',
    'http://0.0.0.0/': '0.0.0.0',
    'http://[::1]/': '::1',
    'http://[fc00::1]/': 'fc00::/7',
    'http://[fe80::1]/': 'fe80 link-local v6',
    'http://100.64.0.1/': 'CGNAT',
    'http://240.0.0.1/': 'reservado',
    'http://interno.exemplo.com/': 'DNS → 10.x',
    'http://metadados.exemplo.com/': 'DNS → 169.254',
    'http://misto.exemplo.com/': 'DNS com um IP privado',
    'http://v6local.exemplo.com/': 'DNS → ::1',
    'http://mapeado.exemplo.com/': 'DNS → ::ffff:127.0.0.1',
    'http://cgnat.exemplo.com/': 'DNS → 100.64',
    'http://redis:6379/': 'nome de serviço sem ponto / porta',
    'http://db/': 'nome de serviço sem ponto',
    'http://algo.internal/': '.internal',
    'http://nas.local/': '.local',
    'https://publico.exemplo.com:8443/': 'porta fora de 80/443',
    'ftp://publico.exemplo.com/': 'ftp',
    'file:///etc/passwd': 'file',
    'javascript:alert(1)': 'javascript',
    'http://user:senha@publico.exemplo.com/': 'credencial na URL',
    'https://minio.exemplo-teste.com.br/bucket/x': 'MinIO do portal',
    'https://evolution.exemplo-teste.com.br/': 'host bloqueado por configuração',
    'http://naoexiste.exemplo.com/': 'sem DNS',
    'http://2130706433/': 'IP decimal',
}
for url, desc in bloqueados.items():
    t(f'bloqueia {desc} ({url})', N.checar_endereco(url) is not None, N.checar_endereco(url))
t('libera site público', N.checar_endereco('https://publico.exemplo.com/pagina?x=1') is None)
t('libera Power BI publicado na web', N.checar_endereco('https://app.powerbi.com/view?r=abc&pageName=x') is None)
t('libera iframe interno do Power BI (sandbox de visual)',
  N.checar_endereco('https://app.powerbi.com/13.0.1/visualSandboxMinimal.cshtml?locale=pt-BR') is None)
motivo = N.checar_endereco('https://app.powerbi.com/groups/me/reports/abc/def?ctid=1') or ''
t('recusa Power BI /groups/me (login Microsoft)', 'login Microsoft' in motivo, motivo)
t('recusa página de login', N.checar_endereco('https://login.microsoftonline.com/common/oauth2') is not None)
t('recusa login do Google', N.checar_endereco('https://accounts.google.com/signin') is not None)
t('envio de formulário (POST de navegação) bloqueado',
  'formulário' in (N.checar_endereco('https://publico.exemplo.com/enviar', metodo='POST') or ''))
t('POST que não é navegação (consulta do Power BI) passa',
  N.checar_endereco('https://app.powerbi.com/public/reports/querydata', navegacao=False, metodo='POST') is None)
motivo = N.checar_endereco('https://portal.exemplo-teste.com.br/tickets/?x=1') or ''
t('página logada do portal → orienta ler_pagina', 'ler_pagina(caminho="/tickets/")' in motivo, motivo)
t('página pública do portal (/facilitador/p/) abre',
  N.checar_endereco('https://portal.exemplo-teste.com.br/facilitador/p/abc123/') is None)
t('estático do portal (para a página pública) passa como recurso',
  N.checar_endereco('https://portal.exemplo-teste.com.br/static/x.css', navegacao=False) is None)
t('API logada do portal bloqueada também como recurso',
  N.checar_endereco('https://portal.exemplo-teste.com.br/api/qualquer', navegacao=False) is not None)
cache = {}
N.checar_endereco('http://interno.exemplo.com/a', cache_hosts=cache)
t('decisão por host fica em cache na sessão', cache.get(('interno.exemplo.com', None)) is not None)


print('rota do navegador (toda requisição, inclusive redirecionamento)')


class FakeResp:
    def __init__(self, status, location=''):
        self.status = status
        self.headers = {'location': location} if location else {}


class FakeRoute:
    def __init__(self, url, navegacao=True, metodo='GET', resp=None):
        self.request = types.SimpleNamespace(
            url=url, method=metodo, resource_type='document' if navegacao else 'image',
            is_navigation_request=lambda: navegacao)
        self.resp = resp or FakeResp(200)
        self.feito = None
        self.corpo = ''

    def abort(self, motivo=''):
        self.feito = 'abort'

    def continue_(self):
        self.feito = 'continue'

    def fetch(self, **kw):
        self.fetch_kw = kw
        return self.resp

    def fulfill(self, response=None, status=None, body='', **kw):
        self.feito = 'fulfill'
        self.corpo = body or ''


s = N.Sessao()
r = FakeRoute('http://10.0.0.1/', navegacao=False)
s._rota(r, r.request)
t('recurso para IP privado é abortado', r.feito == 'abort')
r = FakeRoute('https://publico.exemplo.com/img.png', navegacao=False)
s._rota(r, r.request)
t('recurso público segue', r.feito == 'continue')
r = FakeRoute('https://publico.exemplo.com/', resp=FakeResp(302, 'http://169.254.169.254/latest/'))
s._rota(r, r.request)
t('redirecionamento para metadados (169.254) é abortado', r.feito == 'abort' and r.fetch_kw.get('max_redirects') == 0)
t('o bloqueio fica registrado para explicar ao Claude', any('rede interna' in b for b in s.bloqueios), s.bloqueios)
r = FakeRoute('https://publico.exemplo.com/', resp=FakeResp(301, '/outra'))
s._rota(r, r.request)
t('redirecionamento permitido vira navegação nova (passa de novo pela rota)',
  r.feito == 'fulfill' and 'location.replace("https://publico.exemplo.com/outra")' in r.corpo, r.corpo)
r = FakeRoute('https://publico.exemplo.com/', resp=FakeResp(302, 'https://x.exemplo.com/"</script><script>alert(1)//'))
DNS['x.exemplo.com'] = {'93.184.216.40'}
s._rota(r, r.request)
t('destino do redirecionamento não injeta HTML', '</script><script>' not in r.corpo, r.corpo)
r = FakeRoute('https://publico.exemplo.com/')
s._rota(r, r.request)
t('página pública sem redirecionamento é entregue', r.feito == 'fulfill')
r = FakeRoute('https://publico.exemplo.com/form', metodo='POST')
s._rota(r, r.request)
t('POST de navegação abortado na rota', r.feito == 'abort')
r = FakeRoute('chrome-extension://abc/x.js', navegacao=False)
s._rota(r, r.request)
t('esquema estranho abortado', r.feito == 'abort')
ws = types.SimpleNamespace(url='ws://10.0.0.2/socket', fechou=False, ligou=False)
ws.close = lambda: setattr(ws, 'fechou', True)
ws.connect_to_server = lambda: setattr(ws, 'ligou', True)
s._rota_ws(ws)
t('websocket para rede interna é fechado', ws.fechou and not ws.ligou)
ws.url, ws.fechou = 'wss://publico.exemplo.com/socket', False
s._rota_ws(ws)
t('websocket público conecta', ws.ligou and not ws.fechou)


print('relatórios do /power-bi/ por usuário')
loja = Sector.objects.create(name='LOJA ZZ NAV')
outra = Sector.objects.create(name='LOJA ZZ OUTRA')
admin = User.objects.create_user(username='zznav.admin', email='zznav.admin@exemplo-teste.local',
                                 password='S3nha!teste', first_name='Adm', hierarchy='SUPERADMIN')
ana = User.objects.create_user(username='zznav.ana', email='zznav.ana@exemplo-teste.local',
                               password='S3nha!teste', first_name='Ana', hierarchy='PADRAO', sector=loja)
beto = User.objects.create_user(username='zznav.beto', email='zznav.beto@exemplo-teste.local',
                                password='S3nha!teste', first_name='Beto', hierarchy='PADRAO', sector=outra)
grupo = CommunicationGroup.objects.create(name='ZZ GERENTES NAV', created_by=admin)
grupo.members.add(beto)

geral = PowerBIReport.objects.create(name='BI Geral', embed_url='https://app.powerbi.com/view?r=GERAL')
da_loja = PowerBIReport.objects.create(name='BI da Loja', embed_url='https://app.powerbi.com/view?r=LOJA&pageName=p1')
da_loja.allowed_sectors.add(loja)
gerentes = PowerBIReport.objects.create(name='BI Gerentes', embed_url='https://parciais.exemplo.com/gerentes')
gerentes.allowed_groups.add(grupo)
login_ms = PowerBIReport.objects.create(name='BI com login',
                                        embed_url='https://app.powerbi.com/groups/me/reports/abc/def?ctid=1')
inativo = PowerBIReport.objects.create(name='BI Inativo', embed_url='https://app.powerbi.com/view?r=INATIVO',
                                       is_active=False)
pagina_portal = PowerBIReport.objects.create(name='Análise pública',
                                             embed_url='https://portal.exemplo-teste.com.br/facilitador/p/tok/')

import json  # noqa: E402

lista_ana = json.loads(roda('powerbi_relatorios', {}, ana))['relatorios']
nomes_ana = {r['nome'] for r in lista_ana}
t('Ana vê o geral e o da loja dela', {'BI Geral', 'BI da Loja'} <= nomes_ana, nomes_ana)
t('Ana não vê o dos gerentes nem o inativo', not ({'BI Gerentes', 'BI Inativo'} & nomes_ana), nomes_ana)
nomes_beto = {r['nome'] for r in json.loads(roda('powerbi_relatorios', {}, beto))['relatorios']}
t('Beto (grupo) vê o dos gerentes e não o da loja da Ana',
  'BI Gerentes' in nomes_beto and 'BI da Loja' not in nomes_beto, nomes_beto)
lista_admin = json.loads(roda('powerbi_relatorios', {}, admin))['relatorios']
t('SUPERADMIN vê todos os ativos', len(lista_admin) == 5, [r['nome'] for r in lista_admin])
tipo = {r['nome']: r for r in lista_admin}
t('tipo de link: publicado na web', tipo['BI Geral']['tipo_link'] == 'powerbi_publico')
t('tipo de link: exige login (não pode abrir)',
  tipo['BI com login']['tipo_link'] == 'powerbi_exige_login' and not tipo['BI com login']['pode_abrir'])
t('tipo de link: página pública do portal', tipo['Análise pública']['tipo_link'] == 'portal_publico')
t('a lista não expõe o link inteiro (só o site)', 'r=GERAL' not in roda('powerbi_relatorios', {}, admin))

# Nenhuma destas recusas pode chegar a abrir o navegador.
with mock.patch.object(N.Sessao, 'abrir', side_effect=AssertionError('abriu o navegador')):
    r = roda('navegador_abrir', {'relatorio_id': da_loja.pk}, beto)
    t('relatório de outra loja por id → não encontrado', 'não encontrado' in r.lower(), r)
    r = roda('navegador_abrir', {'url': 'https://app.powerbi.com/view?r=LOJA'}, beto)
    t('relatório de outra loja pelo link → recusado', 'não está liberado' in r, r)
    r = roda('navegador_abrir', {'relatorio_id': inativo.pk}, admin)
    t('relatório inativo → não encontrado', 'não encontrado' in r.lower(), r)
    r = roda('navegador_abrir', {'relatorio_id': login_ms.pk}, admin)
    t('Power BI /groups/me → explica o login Microsoft', 'login Microsoft' in r and f'/power-bi/{login_ms.pk}/' in r, r)
    r = roda('navegador_abrir', {'url': 'https://portal.exemplo-teste.com.br/vendas/'}, ana)
    t('página logada do portal → ler_pagina', 'ler_pagina' in r, r)
    r = roda('navegador_abrir', {'url': '/vendas/'}, ana)
    t('caminho relativo → ler_pagina', 'ler_pagina' in r, r)
    r = roda('navegador_abrir', {'url': 'http://169.254.169.254/latest/meta-data/'}, ana)
    t('metadados da nuvem recusados antes de abrir', r.startswith('Não abro'), r)
    r = roda('navegador_abrir', {}, ana)
    t('sem id nem url → pede um dos dois', 'Informe' in r, r)


print('sessão por pergunta (navegador falso)')


class FakePage:
    def __init__(self):
        self.url = 'about:blank'
        self.fotos = 0

    def goto(self, url, **kw):
        self.url = url

    def screenshot(self, **kw):
        self.fotos += 1
        return b'\xff\xd8\xff\xe0JPEG-FALSO'

    def evaluate(self, *a, **k):
        return {'pbi': False}

    def wait_for_load_state(self, *a, **k):
        pass

    def wait_for_timeout(self, ms):
        pass


contagem = {'abriu': 0, 'fechou': 0, 'threads': set()}


def abrir_falso(self):
    contagem['abriu'] += 1
    contagem['threads'].add(threading.current_thread().name)
    self.page = FakePage()


def fechar_falso(self):
    contagem['fechou'] += 1


def ler_falso(s, parte=1, busca=''):
    return f'URL: {s.page.url}\nleitura falsa'


p_abrir = mock.patch.object(N.Sessao, '_abrir_na_thread', abrir_falso)
p_fechar = mock.patch.object(N.Sessao, '_fechar_na_thread', fechar_falso)
p_ler = mock.patch.object(N, '_ler', ler_falso)
p_esperar = mock.patch.object(N, '_esperar', lambda page, maximo=0: None)
for p in (p_abrir, p_fechar, p_ler, p_esperar):
    p.start()

vagas_antes = N._VAGAS._value
with N.pergunta() as perg:
    r1 = roda('navegador_abrir', {'relatorio_id': geral.pk}, ana)
    r2 = roda('navegador_print', {}, ana)
    sessao = perg.sessao
    r3 = roda('navegador_abrir', {'url': 'https://publico.exemplo.com/x'}, ana)
    t('ORM continua funcionando na thread da pergunta', User.objects.filter(pk=ana.pk).exists())
t('abre o relatório pelo id (link resolvido no servidor)', 'Relatório: BI Geral' in r1 and 'r=GERAL' in r1, r1)
t('um só navegador para a pergunta inteira (reuso)', contagem['abriu'] == 1, contagem)
t('o navegador roda numa thread própria',
  all(n.startswith('assistente-navegador') for n in contagem['threads']), contagem['threads'])
t('fechou no fim da pergunta', contagem['fechou'] == 1 and sessao.fechada, contagem)
t('vaga do navegador devolvida', N._VAGAS._value == vagas_antes, (N._VAGAS._value, vagas_antes))
t('print volta como blocos texto + imagem',
  isinstance(r2, list) and r2[0]['type'] == 'text' and r2[1]['type'] == 'image'
  and r2[1]['source']['media_type'] == 'image/jpeg', r2)
t('imagem em base64 válido', base64.b64decode(r2[1]['source']['data']).startswith(b'\xff\xd8'))

contagem.update(abriu=0, fechou=0)
r = roda('navegador_print', {}, ana)
t('fora de uma pergunta: abre e fecha na mesma chamada', contagem['abriu'] == 1 and contagem['fechou'] == 1, contagem)
t('…e sem página aberta avisa', 'Nenhuma página aberta' in r, r)

contagem.update(abriu=0, fechou=0)
try:
    with N.pergunta():
        roda('navegador_abrir', {'relatorio_id': geral.pk}, ana)
        raise RuntimeError('o laço do Claude quebrou')
except RuntimeError:
    pass
t('fecha mesmo quando a pergunta quebra no meio', contagem['fechou'] == 1, contagem)

contagem.update(abriu=0, fechou=0)
with N.pergunta() as perg:
    roda('navegador_abrir', {'relatorio_id': geral.pk}, ana)
    perg.sessao.inicio -= N.ORCAMENTO_PERGUNTA_S + 1
    r = roda('navegador_print', {}, ana)
    r_depois = roda('navegador_abrir', {'relatorio_id': geral.pk}, ana)
t('orçamento de tempo da pergunta estourado → avisa e fecha', 'tempo de navegador' in r and contagem['fechou'] == 1, r)
t('…e não reabre na mesma pergunta', contagem['abriu'] == 1 and 'tempo de navegador' in r_depois, (contagem, r_depois))

print('responder: tool_result com imagem e navegador fechado no fim')


class FakeMessages:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    def create(self, **kw):
        self.chamadas.append(kw)
        return self.respostas.pop(0)


def bloco_tool(nome, entrada, ident):
    return types.SimpleNamespace(type='tool_use', name=nome, input=entrada, id=ident)


respostas = [
    types.SimpleNamespace(stop_reason='tool_use', content=[bloco_tool('navegador_abrir', {'relatorio_id': geral.pk}, 'tu1')]),
    types.SimpleNamespace(stop_reason='tool_use', content=[bloco_tool('navegador_print', {}, 'tu2'),
                                                           bloco_tool('powerbi_relatorios', {}, 'tu3')]),
    types.SimpleNamespace(stop_reason='end_turn', content=[types.SimpleNamespace(type='text', text='Pronto.')]),
]
msgs = FakeMessages(respostas)
cliente = types.SimpleNamespace(beta=types.SimpleNamespace(messages=msgs))
contagem.update(abriu=0, fechou=0)
with mock.patch.object(claude_client, '_cliente', return_value=(cliente, 'claude-haiku-4-5')):
    resposta = claude_client.responder(ana, 'quanto vendeu a loja no BI geral?')
t('responder devolve o texto final', resposta == 'Pronto.', resposta)
ultima = msgs.chamadas[-1]['messages'][-1]['content']
img = next((b for b in ultima if b['tool_use_id'] == 'tu2'), None)
t('tool_result do print leva o bloco de imagem',
  img and isinstance(img['content'], list) and img['content'][1]['type'] == 'image', img)
txt = next((b for b in ultima if b['tool_use_id'] == 'tu3'), None)
t('ferramentas de texto continuam devolvendo string (ORM depois do navegador)',
  txt and isinstance(txt['content'], str) and 'BI Geral' in txt['content'], txt)
t('um navegador na pergunta e fechado ao terminar', contagem['abriu'] == 1 and contagem['fechou'] == 1, contagem)
nomes_tools = {x['name'] for x in msgs.chamadas[0]['tools']}
t('ferramentas do navegador registradas', {'powerbi_relatorios', 'navegador_abrir', 'navegador_ler', 'navegador_clicar',
                                           'navegador_digitar', 'navegador_tecla', 'navegador_rolar',
                                           'navegador_esperar', 'navegador_voltar', 'powerbi_pagina',
                                           'navegador_print'} <= nomes_tools)
t('SYSTEM orienta sobre BIs e links', 'powerbi_relatorios' in claude_client.SYSTEM and 'navegador_abrir' in claude_client.SYSTEM)

contagem.update(abriu=0, fechou=0)
msgs = types.SimpleNamespace(create=mock.Mock(side_effect=[
    types.SimpleNamespace(stop_reason='tool_use', content=[bloco_tool('navegador_abrir', {'relatorio_id': geral.pk}, 'tu1')]),
    RuntimeError('API caiu')]))
cliente = types.SimpleNamespace(beta=types.SimpleNamespace(messages=msgs))
with mock.patch.object(claude_client, '_cliente', return_value=(cliente, 'claude-haiku-4-5')):
    try:
        claude_client.responder(ana, 'abre o BI')
    except RuntimeError:
        pass
t('API falhando no meio: navegador fechado mesmo assim', contagem['abriu'] == 1 and contagem['fechou'] == 1, contagem)

for p in (p_abrir, p_fechar, p_ler, p_esperar):
    p.stop()

print('navegador indisponível')
with mock.patch.dict(sys.modules, {'playwright.sync_api': None}):
    r = roda('navegador_abrir', {'relatorio_id': geral.pk}, ana)
t('sem Playwright: mensagem clara, sem quebrar', 'não está instalado' in r, r)
t('…e a vaga não fica presa', N._VAGAS._value == vagas_antes)

with mock.patch.object(N.Sessao, '_abrir_na_thread', side_effect=RuntimeError('chromium sumiu')):
    with N.pergunta():
        r = roda('navegador_abrir', {'relatorio_id': geral.pk}, ana)
        r2 = roda('navegador_ler', {}, ana)
t('Chromium não abre: mensagem clara', 'Não consegui abrir o navegador' in r, r)
t('…e não tenta de novo na mesma pergunta', r2 == r, r2)
t('…e a vaga volta', N._VAGAS._value == vagas_antes)

presas = [N._VAGAS.acquire(timeout=0) for _ in range(vagas_antes)]
with mock.patch.object(N, 'ESPERA_VAGA_S', 0.05):
    r = roda('navegador_abrir', {'relatorio_id': geral.pk}, ana)
for _ in presas:
    N._VAGAS.release()
t('todas as vagas ocupadas: avisa que está ocupado', 'ocupado' in r, r)

print('guardas de digitar, clicar e teclar')


class FakeLoc:
    def __init__(self, info):
        self.info = info
        self.preenchido = None
        self.clicado = False

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def evaluate(self, js):
        return self.info

    def fill(self, texto):
        self.preenchido = texto

    def click(self, **kw):
        self.clicado = True


def sessao_com(info):
    loc = FakeLoc(info)
    frame = types.SimpleNamespace(locator=lambda sel: loc)
    s = types.SimpleNamespace(elementos={1: frame}, page=FakePage())
    return s, loc


base = {'tag': 'input', 'type': 'text', 'attrs': 'q busca', 'texto': '', 'form_com_senha': False,
        'pagina_com_senha': False}
digitar = N._digitar.__wrapped__
clicar = N._clicar.__wrapped__
s, loc = sessao_com(dict(base, type='password', attrs='senha'))
try:
    digitar(s, {'numero': 1, 'texto': 'x'})
    t('não digita em campo de senha', False)
except N.Recusado:
    t('não digita em campo de senha', loc.preenchido is None)
for attrs in ('cvv', 'cpf', 'token', 'one-time-code otp'):
    s, loc = sessao_com(dict(base, attrs=attrs))
    try:
        digitar(s, {'numero': 1, 'texto': 'x'})
        t(f'não digita em campo sensível ({attrs})', False)
    except N.Recusado:
        t(f'não digita em campo sensível ({attrs})', loc.preenchido is None)
s, loc = sessao_com(dict(base, form_com_senha=True, attrs='usuario'))
try:
    digitar(s, {'numero': 1, 'texto': 'fulano'})
    t('não preenche usuário de formulário de login', False)
except N.Recusado:
    t('não preenche usuário de formulário de login', loc.preenchido is None)
for texto in ('Excluir', 'Enviar pedido', 'Salvar', 'Entrar', 'Sign in', 'Comprar agora'):
    s, loc = sessao_com(dict(base, tag='button', type='', texto=texto))
    try:
        clicar(s, {'numero': 1})
        t(f'não clica em "{texto}"', False)
    except N.Recusado:
        t(f'não clica em "{texto}"', not loc.clicado)
s, loc = sessao_com(dict(base, tag='button', type='submit', texto='Ok', form_com_senha=True))
try:
    clicar(s, {'numero': 1})
    t('não envia formulário de login', False)
except N.Recusado:
    t('não envia formulário de login', not loc.clicado)
t('filtros e páginas não são barrados', not any(N.BOTAO_PROIBIDO.search(x) for x in
                                                ('Aplicar', 'Limpar filtros', 'Próxima Página', 'GLÓRIA', 'Ranking')))
r = N._tecla.__wrapped__(types.SimpleNamespace(page=FakePage()), {'tecla': 'Control+A'})
t('tecla fora da lista é recusada', 'não permitida' in r, r)
try:
    N._elemento(types.SimpleNamespace(elementos={}), 7)
    t('número desconhecido pede nova leitura', False)
except N.Recusado as exc:
    t('número desconhecido pede nova leitura', 'navegador_ler' in str(exc))

print('formatação da leitura')
dados = [('', {'url': 'https://app.powerbi.com/view?r=x', 'titulo': 'Microsoft Power BI', 'powerbi': True,
               'pagina_pbi': '2 de 5', 'filtros': ['PDV = GLÓRIA'],
               'texto': 'lixo do power bi ' * 50,
               'els': [{'n': 1, 'tipo': 'combobox', 'nome': 'PDV', 'estado': 'valor=GLÓRIA', 'x': 10, 'y': 20}],
               'omitidos': 3,
               'visuais': [{'titulo': '', 'rotulos': ['Soma de META R$16.500,00.'], 'linhas': [], 'texto': '', 'fora': False},
                           {'titulo': 'Ranking', 'rotulos': [], 'texto': '', 'fora': True,
                            'linhas': ['CONSULTOR | MÓVEL'] + [f'PESSOA {i} | {i},00%' for i in range(60)]}]}),
         (' (quadro 1: x)', {'texto': 'FALTA PARA A META R$ 189.243,41', 'els': [], 'visuais': []})]
sess = types.SimpleNamespace(bloqueios=['porta 8000 não permitida (x)'])
out = N._fmt_leitura(sess, dados)
t('cabeçalho com página e filtros do Power BI', 'página 2 de 5' in out and 'PDV = GLÓRIA' in out, out[:300])
t('números dos visuais aparecem', 'R$16.500,00' in out)
t('texto de visual personalizado (iframe) aparece', 'R$ 189.243,41' in out)
t('texto solto do Power BI omitido (repete os visuais)', 'lixo do power bi' not in out)
t('tabela longa é cortada com aviso', '+21 linhas' in out, out[-600:])
t('bloqueios explicados', 'porta 8000' in out)
t('elementos omitidos sinalizados', 'mais 3 elementos' in out)
out = N._fmt_leitura(sess, dados, busca='pessoa 59')
t('busca filtra linhas da tabela', 'PESSOA 59' in out and 'PESSOA 1 |' not in out and 'CONSULTOR | MÓVEL' in out, out)
grande = [('', dict(dados[0][1], powerbi=False, visuais=[], texto='x' * (N.LIMITE_TEXTO * 2 + 10)))]
out1, out3 = N._fmt_leitura(sess, grande), N._fmt_leitura(sess, grande, parte=3)
t('página longa vem em partes limitadas', 'Parte 1 de 3' in out1 and len(out1) < N.LIMITE_TEXTO + 2000
  and 'Parte 3 de 3' in out3, len(out1))


print('navegador de verdade (opcional, só servidor local)')
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    with sync_playwright() as _p:
        _b = _p.chromium.launch()
        _b.close()
    TEM_NAVEGADOR = True
except Exception as exc:  # noqa: BLE001
    TEM_NAVEGADOR = False
    print(f'  PULADO (sem Playwright/Chromium: {type(exc).__name__})')

if TEM_NAVEGADOR:
    pedidos = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _html(self, corpo):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(corpo.encode())

        def do_GET(self):
            pedidos.append(('GET', self.path))
            if self.path == '/redir':
                self.send_response(302)
                self.send_header('Location', '/segredo')
                self.end_headers()
            elif self.path == '/redir2':
                self.send_response(302)
                self.send_header('Location', '/redir3')
                self.end_headers()
            elif self.path == '/redir3':
                self.send_response(302)
                self.send_header('Location', '/segredo')
                self.end_headers()
            elif self.path == '/vai':
                self.send_response(302)
                self.send_header('Location', '/ok')
                self.end_headers()
            elif self.path == '/frame':
                self._html('<h1>Com quadro</h1><iframe src="/redir"></iframe>')
            elif self.path == '/form':
                self._html('<form method="post" action="/post"><input name="q" aria-label="Busca">'
                           '<button>Buscar</button></form>'
                           '<form><input type="password" name="pw" aria-label="Senha"></form>'
                           '<select aria-label="Loja"><option>A</option><option>GLÓRIA</option></select>')
            else:
                self._html(f'<h1>PAGINA {self.path}</h1><a href="/ok">ir</a>')

        def do_POST(self):
            pedidos.append(('POST', self.path))
            self._html('POSTOU')

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), H)
    porta = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base_url = f'http://127.0.0.1:{porta}'
    original = N.checar_endereco

    def checar_teste(url, **kw):
        # o servidor local faz papel de "site público"; /segredo faz papel de rede interna
        if '/segredo' in url:
            return 'o endereço aponta para uma rede interna/privada'
        return original(url, **kw)

    with mock.patch.object(N, 'checar_endereco', checar_teste), \
            mock.patch.object(N, '_ip_publico', lambda ip: True), \
            mock.patch.object(N, 'PORTAS_PERMITIDAS', {80, 443, porta}):
        with N.pergunta() as perg:
            r = roda('navegador_abrir', {'url': base_url + '/redir'}, ana)
            t('real: redirecionamento para rede interna barrado', 'rede interna' in r and 'SEGREDO' not in r, r[:300])
            r = roda('navegador_abrir', {'url': base_url + '/redir2'}, ana)
            t('real: cadeia de redirecionamentos também barrada no 2º salto', 'PAGINA /segredo' not in r, r[:300])
            r = roda('navegador_abrir', {'url': base_url + '/vai'}, ana)
            t('real: redirecionamento permitido chega ao destino', 'PAGINA /ok' in r, r[:300])
            r = roda('navegador_abrir', {'url': base_url + '/frame'}, ana)
            t('real: iframe redirecionado para rede interna não carrega', 'PAGINA /segredo' not in r, r[:500])
            t('real: ORM funciona depois do navegador na mesma thread', User.objects.filter(pk=ana.pk).exists())
            r = roda('navegador_abrir', {'url': base_url + '/form'}, ana)
            import re as _re
            num = lambda nome: int(_re.search(r'\[(\d+)\] [^\n]*"' + nome + '"', r).group(1))  # noqa: E731
            r_senha = roda('navegador_digitar', {'numero': num('Senha'), 'texto': 'x'}, ana)
            t('real: recusa digitar senha', 'senha' in r_senha.lower() and 'não' in r_senha.lower(), r_senha)
            r_sel = roda('navegador_digitar', {'numero': num('Loja'), 'texto': 'GLÓRIA'}, ana)
            t('real: escolhe opção de select', 'valor=GLÓRIA' in r_sel, r_sel[:400])
            r_busca = roda('navegador_digitar', {'numero': num('Busca'), 'texto': 'abc', 'enter': True}, ana)
            r_busca = roda('navegador_clicar', {'numero': num('Buscar')}, ana) if 'Buscar' in r_busca else r_busca
            t('real: formulário POST não chega ao servidor', not any(m == 'POST' for m, _ in pedidos), pedidos)
            img = roda('navegador_print', {}, ana)
            t('real: print devolve JPEG', isinstance(img, list)
              and base64.b64decode(img[1]['source']['data'])[:2] == b'\xff\xd8')
            sessao_real = perg.sessao
        t('real: nenhum pedido a /segredo chegou ao servidor', ('GET', '/segredo') not in pedidos, pedidos)
        t('real: navegador fechado no fim da pergunta', sessao_real.fechada)
        time.sleep(0.3)
        t('real: thread do navegador encerrada',
          not [th for th in threading.enumerate() if th.name.startswith('assistente-navegador') and th.is_alive()])
    srv.shutdown()

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
