"""Módulo de bugs (/bugs/): relato → análise do Claude → testes isolados → aprovação → GitHub (24/11/2026).

Banco SQLite descartável; o Claude e o GitHub são dublês — nada sai daqui. Os testes da
correção rodam de verdade no sandbox (cópia do portal + SQLite próprio).
"""
import os
import pathlib
import sys
import tempfile
import types
from unittest import mock

import django

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-bugs-'))
(PASTA / 'zz_bugs_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-bugs'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-bugs-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_bugs_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
for var in ('BUGS_GITHUB_TOKEN', 'GITHUB_TOKEN', 'BUGS_GITHUB_REPO'):
    os.environ.pop(var, None)

django.setup()

from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

from django.contrib.auth import get_user_model  # noqa: E402

from bugs import analise, codigo, github, sandbox, servicos  # noqa: E402
from bugs.models import ConfigBugs, RelatoBug  # noqa: E402
from core.models import Notification  # noqa: E402

User = get_user_model()
S = RelatoBug.Status
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def pessoa(login, hierarquia):
    u = User.objects.create_user(username=login, email=f'{login}@exemplo-teste.local', password='S3nha!teste',
                                 first_name='ZZ', last_name=login, hierarchy=hierarquia)
    c = Client()
    c.force_login(u)
    return u, c


chefe, c_chefe = pessoa('zz.bug.super', 'SUPERADMIN')
colab, c_colab = pessoa('zz.bug.colab', 'PADRAO')
outro, c_outro = pessoa('zz.bug.outro', 'PADRAO')

print('\n# Acesso ao código')
for ruim in ('../etc/passwd', '.env', 'venv/lib/x.py', '.git/config', 'media/x.txt'):
    try:
        rel, _ = codigo.Rascunho().ler(ruim)
        t(f'recusa ler {ruim}', False, rel)
    except codigo.ErroCodigo:
        t(f'recusa ler {ruim}', True)
t('chave .pem conta como segredo', codigo.secreto('vapid_private.pem') and codigo.secreto('auto.key'))
for proibido in ('core/migrations/0001_initial.py', 'redeconfianca/settings.py', 'bugs/servicos.py',
                 'templates/bugs/lista.html', 'requirements.txt', 'Dockerfile', 'deploy_new.sh'):
    t(f'não deixa editar {proibido}', not codigo.pode_editar(proibido)[0])
t('deixa editar view e template comuns', codigo.pode_editar('tickets/views.py')[0]
  and codigo.pode_editar('templates/tickets/list.html')[0])

r = codigo.Rascunho()
original = (codigo.RAIZ / 'core/utils.py').read_text(encoding='utf-8')
try:
    r.editar('core/utils.py', 'trecho que não existe em lugar nenhum', 'x')
    t('trecho inexistente é recusado', False)
except codigo.ErroCodigo:
    t('trecho inexistente é recusado', True)
try:
    r.editar('core/utils.py', 'import', 'importe')
    t('trecho repetido é recusado', False)
except codigo.ErroCodigo as exc:
    t('trecho repetido é recusado', 'vezes' in str(exc))
r.editar('core/utils.py', 'def processo_de_teste():', 'def zz_funcao_do_bug():\n    return 42\n\n\ndef processo_de_teste():')
t('edição fica só no rascunho (disco intacto)', (codigo.RAIZ / 'core/utils.py').read_text(encoding='utf-8') == original)
t('leitura devolve a versão editada', 'zz_funcao_do_bug' in r.ler('core/utils.py')[1])
t('diff mostra a linha nova', '+def zz_funcao_do_bug():' in r.diff())
t('busca enxerga o rascunho', any('zz_funcao_do_bug' in x for x in codigo.buscar('zz_funcao_do_bug', 'core', '*.py', r)['resultados']))
rota = codigo.rota_da_url('https://portal.exemplo/bugs/?f=todos')
t('rota da URL acha a view e o template', rota.get('view') == 'bugs.views.lista' and rota.get('arquivo') == 'bugs/views.py'
  and 'bugs/lista.html' in rota.get('templates_citados', []), rota)

print('\n# Sandbox (cópia isolada, rodando de verdade)')
caso = """
from core.utils import zz_funcao_do_bug
assert zz_funcao_do_bug() == 42
from django.db import connection
assert connection.vendor == 'sqlite', connection.vendor
for segredo in ('DATABASE_URL', 'AWS_SECRET_ACCESS_KEY', 'ANTHROPIC_API_KEY'):
    assert segredo not in os.environ, segredo
from django.conf import settings
assert not settings.ZAPI_TOKEN and not settings.RESEND_API_KEY
import requests
try:
    requests.get('https://example.com', timeout=5)
    raise AssertionError('saiu para a internet')
except requests.RequestException:
    pass
u = User.objects.create_user(username='a', email='a@a.local', password='x', first_name='A', last_name='B', hierarchy='SUPERADMIN')
c = Client(); c.force_login(u)
assert c.get('/bugs/').status_code == 200
""".replace('\nfrom core.utils', '\nimport os\nfrom core.utils')
res = sandbox.rodar(r.alteracoes(), caso)
t('com a correção passa (sqlite, sem segredo, sem internet, tela abre)', res['ok'], res['saida'][-2500:])
t('sem a correção falha (o teste reproduz o bug)', res['reproduz'], res['resumo'])
vigia = f"""
import socket, subprocess
def barrado(acao):
    try:
        acao()
    except PermissionError as exc:
        assert 'sandbox do bug' in str(exc), exc
        return True
    return False
assert barrado(lambda: open({str(codigo.RAIZ / 'manage.py')!r}).read()), 'leu a pasta real do portal'
assert barrado(lambda: open({str(codigo.RAIZ / 'zz_nao_deve_existir.txt')!r}, 'w')), 'escreveu fora'
assert barrado(lambda: open('/tmp/zz_bug_fora.txt', 'w')), 'escreveu no /tmp'
assert barrado(lambda: subprocess.run(['echo', 'x'])), 'abriu programa'
assert barrado(lambda: socket.create_connection(('93.184.216.34', 80), timeout=3)), 'conectou fora'
with open('dentro.txt', 'w') as f:
    f.write('ok')
import tempfile
with tempfile.NamedTemporaryFile('w') as f:
    f.write('ok')
"""
res3 = sandbox.rodar(r.alteracoes(), vigia)
t('vigia barra ler o portal real, escrever fora, programas e internet (e deixa a pasta do teste)', res3['ok'], res3['saida'][-2500:])
t('nada vazou para fora da pasta do teste', not (codigo.RAIZ / 'zz_nao_deve_existir.txt').exists()
  and not os.path.exists('/tmp/zz_bug_fora.txt'))
quebrado = codigo.Rascunho()
quebrado.editar('core/utils.py', 'def processo_de_teste():', 'def processo_de_teste(:')
res2 = sandbox.rodar(quebrado.alteracoes(), 'pass')
t('erro de sintaxe na correção reprova', not res2['ok'] and 'sintaxe' in res2['saida'], res2['resumo'])

print('\n# Análise com um Claude dublê')


class Bloco(types.SimpleNamespace):
    pass


def uso(id_, nome, entrada):
    return Bloco(type='tool_use', id=id_, name=nome, input=entrada)


class Resp(types.SimpleNamespace):
    pass


ROTEIRO = [
    [uso('1', 'rota_da_url', {'url': '/bugs/'})],
    [uso('2', 'ler_arquivo', {'caminho': 'core/utils.py', 'de': 1, 'ate': 20}),
     uso('3', 'ler_arquivo', {'caminho': '.env'})],
    [uso('4', 'editar_arquivo', {'caminho': 'core/utils.py', 'trecho_atual': 'def processo_de_teste():',
                                 'trecho_novo': 'def zz_funcao_do_bug():\n    return 42\n\n\ndef processo_de_teste():'})],
    [uso('5', 'concluir', {'titulo': 'cedo demais', 'diagnostico': 'x', 'solucao': 'x', 'explicacao_usuario': 'x'})],
    [uso('6', 'rodar_testes', {'teste': 'from core.utils import zz_funcao_do_bug\nassert zz_funcao_do_bug() == 42'})],
    [uso('7', 'concluir', {'titulo': 'Utils: função do bug faltando', 'diagnostico': 'Faltava `zz_funcao_do_bug` em core/utils.py.',
                           'solucao': 'Criada a função.', 'explicacao_usuario': 'Achei o problema e corrigi.'})],
]


class ClaudeDuble:
    def __init__(self):
        self.chamadas = []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.chamadas.append(kw)
        passo = ROTEIRO[len(self.chamadas) - 1]
        return Resp(stop_reason='tool_use', content=passo,
                    usage=types.SimpleNamespace(input_tokens=100, output_tokens=10, cache_read_input_tokens=0,
                                                cache_creation_input_tokens=0))


duble = ClaudeDuble()
disparos = []
with mock.patch('bugs.servicos._thread', lambda alvo, pk, nome: disparos.append((alvo.__name__, pk))):
    resp = c_colab.post('/bugs/relatar/', {'url': '/bugs/', 'descricao': 'A página quebra quando eu abro.'})
relato = RelatoBug.objects.get(autor=colab)
t('relatar redireciona para o detalhe e já entra em análise', resp.status_code == 302 and relato.status == S.ANALISANDO
  and disparos == [('_analisar_em_thread', relato.pk)], (resp.status_code, relato.status, disparos))

with mock.patch('assistente.claude_client._cliente', lambda user: (duble, 'claude-opus-5-5')):
    servicos.executar_analise(relato.pk)
relato.refresh_from_db()
resultados = {b['tool_use_id']: b for m in duble.chamadas[-1]['messages'] if m['role'] == 'user' and isinstance(m['content'], list)
              for b in m['content'] if isinstance(b, dict) and b.get('type') == 'tool_result'}
t('o .env não é lido pelo Claude', resultados['3'].get('is_error') and 'segredo' in resultados['3']['content'], resultados['3'])
t('concluir antes de testar é recusado', 'rode rodar_testes' in resultados['5']['content'], resultados['5'])
t('termina aguardando aprovação, com diff e teste', relato.status == S.AGUARDANDO and '+def zz_funcao_do_bug' in relato.diff
  and relato.testes_ok and relato.arquivos == ['core/utils.py'] and 'COM a correção: PASSOU' in relato.teste_saida,
  (relato.status, relato.erro))
t('linha do tempo registrada', any('Testes:' in p['texto'] for p in relato.passos), relato.passos)
t('SYSTEM e fim da conversa vão com cache', duble.chamadas[0]['system'][0].get('cache_control')
  and duble.chamadas[1]['messages'][-1]['content'][-1].get('cache_control'))
t('o histórico guardado não acumula marca de cache', not any(
  isinstance(b, dict) and b.get('cache_control') for m in duble.chamadas[-1]['messages'][:-1]
  if isinstance(m['content'], list) for b in m['content']))
t('SUPERADMIN avisado no sino', Notification.objects.filter(user=chefe, related_object_id=relato.pk).exists())
t('arquivo do servidor não mudou', (codigo.RAIZ / 'core/utils.py').read_text(encoding='utf-8') == original)

print('\n# Telas e permissões')
t('quem relatou vê o detalhe, sem o diff', c_colab.get(f'/bugs/{relato.pk}/').status_code == 200
  and 'zz_funcao_do_bug' not in c_colab.get(f'/bugs/{relato.pk}/').content.decode())
t('outro colaborador não vê', c_outro.get(f'/bugs/{relato.pk}/').status_code == 404)
h = c_chefe.get(f'/bugs/{relato.pk}/').content.decode()
t('SUPERADMIN vê diff, testes e o botão de aprovar', 'zz_funcao_do_bug' in h and 'Aprovar e publicar' in h and 'Configure o' in h)
t('estado (polling) responde JSON', c_colab.get(f'/bugs/{relato.pk}/estado/').json()['status'] == S.AGUARDANDO)
t('colaborador não aprova', c_colab.post(f'/bugs/{relato.pk}/aprovar/').status_code == 404)
t('colaborador não abre a configuração', c_colab.get('/bugs/configurar/').status_code == 404)
lista_chefe = c_chefe.get('/bugs/').content.decode()
t('lista do SUPERADMIN abre em "Para aprovar"', 'Para aprovar (1)' in lista_chefe and f'#{relato.pk}' in lista_chefe)
t('lista do colaborador abre', c_outro.get('/bugs/?url=/tickets/').status_code == 200
  and 'value="/tickets/"' in c_outro.get('/bugs/?url=/tickets/').content.decode())
t('menu tem "Relatar bug" levando a página atual', '/bugs/?url=/bugs/' in c_outro.get('/bugs/').content.decode())
resp = c_chefe.post(f'/bugs/{relato.pk}/aprovar/')
t('aprovar sem GitHub configurado manda configurar', resp.status_code == 302 and resp['Location'] == '/bugs/configurar/')

print('\n# Publicação no GitHub (dublê)')
cfg = ConfigBugs.get()
cfg.set_token('github_pat_zzzz1234')
cfg.repositorio = 'dono/repo'
cfg.save()
t('token guardado cifrado', cfg.github_token_cifrado and 'zzzz1234' not in cfg.github_token_cifrado
  and github.configuracao() == ('github_pat_zzzz1234', 'dono/repo', 'main'))
t('configuração abre para o SUPERADMIN sem mostrar o token', c_chefe.get('/bugs/configurar/').status_code == 200
  and 'github_pat_zzzz1234' not in c_chefe.get('/bugs/configurar/').content.decode())

alt = {'caminho': 'a.py', 'tipo': 'editar', 'original': 'x = 1\ny = 2\n', 'novo': 'x = 10\ny = 2\n',
       'edicoes': [{'de': 'x = 1\n', 'para': 'x = 10\n'}]}
t('aplica direto quando o GitHub está igual', github._aplicar(alt, 'x = 1\ny = 2\n') == 'x = 10\ny = 2\n')
t('reaplica a edição quando o arquivo mudou em outro lugar', github._aplicar(alt, 'x = 1\ny = 3\nz = 4\n') == 'x = 10\ny = 3\nz = 4\n')
try:
    github._aplicar(alt, 'x = 2\n')
    t('conflito quando o trecho sumiu', False)
except github.ErroPublicacao as exc:
    t('conflito quando o trecho sumiu', 'Conflito' in str(exc))

publicados = {}


class GitHubDuble:
    def __init__(self, token, repo):
        self.repo = repo

    def ponta(self, branch):
        return 'sha-ponta', 'arvore-base'

    def conteudo(self, caminho, ref):
        return (codigo.RAIZ / caminho).read_text(encoding='utf-8')

    def pedir(self, metodo, caminho, **kw):
        publicados.setdefault(caminho, []).append(kw.get('json'))
        respostas = {'git/trees': {'sha': 'arvore-nova'}, 'git/commits': {'sha': 'abc1234def'}}
        return types.SimpleNamespace(json=lambda: respostas.get(caminho, {}))


with mock.patch('bugs.github.Cliente', GitHubDuble), \
        mock.patch('bugs.servicos._thread', lambda alvo, pk, nome: servicos.publicar(pk)):
    resp = c_chefe.post(f'/bugs/{relato.pk}/aprovar/')
relato.refresh_from_db()
t('aprovar publica: commit na ponta da branch', relato.status == S.PUBLICADO and relato.commit_sha == 'abc1234def'
  and relato.commit_url == 'https://github.com/dono/repo/commit/abc1234def' and relato.decidido_por == chefe, (relato.status, relato.erro))
arvore = publicados['git/trees'][0]
t('árvore leva o arquivo corrigido', arvore['base_tree'] == 'arvore-base'
  and arvore['tree'][0]['path'] == 'core/utils.py' and 'def zz_funcao_do_bug' in arvore['tree'][0]['content'])
commit = publicados['git/commits'][0]
t('commit em cima da ponta, com causa e quem aprovou', commit['parents'] == ['sha-ponta'] and commit['message'].startswith('fix: Utils')
  and 'aprovado por ZZ zz.bug.super' in commit['message'], commit['message'])
t('push sem forçar', publicados['git/refs/heads/main'][0] == {'sha': 'abc1234def', 'force': False})
t('quem relatou é avisado da correção', Notification.objects.filter(user=colab, title__contains='corrigido').exists())
t('publicado não aceita aprovar de novo', not servicos.aprovar(relato, chefe))

print('\n# Recusa e nova análise')
r2 = RelatoBug.objects.create(autor=colab, url='/x/', descricao='outra coisa quebrada', status=S.AGUARDANDO,
                              alteracoes=relato.alteracoes, diff=relato.diff)
c_chefe.post(f'/bugs/{r2.pk}/recusar/', {'motivo': 'não é bem isso'})
r2.refresh_from_db()
t('recusar guarda o motivo e avisa', r2.status == S.REJEITADO and r2.motivo_recusa == 'não é bem isso'
  and Notification.objects.filter(user=colab, related_object_id=r2.pk).exists())
with mock.patch('bugs.servicos._thread', lambda *a: None):
    c_chefe.post(f'/bugs/{r2.pk}/reanalisar/', {'orientacao': 'use outro nome'})
r2.refresh_from_db()
t('nova análise volta para a fila com a orientação', r2.status == S.ANALISANDO and r2.orientacao == 'use outro nome', r2.status)
sessao = analise.Sessao(r2)
t('a nova análise parte da proposta anterior', sessao.previo and 'core/utils.py' in sessao.rascunho.arquivos)
t('mensagem inicial traz a orientação do SUPERADMIN', 'use outro nome' in analise._mensagem_inicial(r2, True)['content'][-1]['text'])

print('\n# Sem chave do Claude')
r3 = RelatoBug.objects.create(autor=outro, url='/y/', descricao='mais um problema aqui', status=S.ANALISANDO)
servicos.executar_analise(r3.pk)
r3.refresh_from_db()
t('sem chave vira ERRO explicando onde configurar', r3.status == S.ERRO and '/users/settings/' in r3.erro, r3.erro)

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
