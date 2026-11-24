"""Roda os testes de uma correção numa cópia isolada do portal.

Nada do que o teste faz chega ao portal de verdade:
- cópia do código numa pasta temporária, com a correção aplicada (o servidor não muda);
- banco SQLite novo, criado direto dos models (sem migrações) e apagado no fim;
- cache em memória, arquivos em disco local, sem chaves de WhatsApp, e-mail, OpenAI, S3...;
- o processo não herda as variáveis de ambiente do servidor (nenhum segredo) e sai para a
  internet por um proxy que não existe;
- o script se chama ``teste_*.py``, então a trava central de envio (core.utils.processo_de_teste)
  também vale;
- um audit hook no processo barra escrita fora da pasta temporária, leitura da pasta real do
  portal (onde ficam .env e chaves), /proc, subprocessos e conexão que não seja local.

Cada rodada executa: compilação dos .py alterados, ``manage.py check``, leitura dos
templates alterados e o teste escrito pelo Claude — duas vezes: com a correção (precisa
passar) e sem ela (o esperado é falhar, provando que o teste pega o bug).
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .codigo import ARQUIVOS_SECRETOS, PASTAS_FORA, RAIZ

TEMPO_LIMITE = 240           # segundos por rodada
MAX_SAIDA = 14_000

SETTINGS = '''
from redeconfianca.settings import *              # noqa: F401,F403

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': r'%(pasta)s/banco.sqlite3'}}


class _SemMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


MIGRATION_MODULES = _SemMigrations()
DEBUG = False
ALLOWED_HOSTS = ['*']
USE_S3 = False
MEDIA_ROOT = r'%(pasta)s/media'
MEDIA_URL = '/media/'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
CACHES = {n: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'bug-' + n}
          for n in ('default', 'local')}
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
OPENAI_API_KEY = ''
RESEND_API_KEY = ''
ZAPI_INSTANCE_ID = ZAPI_TOKEN = ZAPI_CLIENT_TOKEN = ''
EVOLUTION_API_URL = EVOLUTION_API_KEY = EVOLUTION_INSTANCE = ''
ONESIGNAL_APP_ID = ONESIGNAL_REST_API_KEY = TRUEPUSH_API_KEY = TRUEPUSH_PROJECT_ID = ''
TANGERINO_ENABLED = False
TANGERINO_TOKEN = ''
VIVOGO_MYSQL_URL = ''
GOOGLE_DRIVE_SA_FILE = GOOGLE_DRIVE_SA_JSON = ''
CARTOES_API_TOKEN = ''
'''

HARNESS = r'''
"""Verificação da correção do bug (gerado por bugs/sandbox.py)."""
import os, py_compile, runpy, sys, traceback

# Vigia do processo (audit hook — não dá para desligar depois de ligado): o teste é código
# escrito pelo Claude a partir de um relato, então aqui ele só mexe na própria pasta.
_PASTA = os.path.realpath(os.path.dirname(os.getcwd()))
_RAIZ = os.path.realpath(%(raiz)r)
_LIBERADAS = tuple(os.path.realpath(p) for p in {sys.prefix, sys.base_prefix, sys.exec_prefix} if p)
_ESCRITA = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _dentro(caminho, pasta):
    return caminho == pasta or caminho.startswith(pasta + os.sep)


def _vigia(evento, args):
    if evento == 'open':
        caminho, modo, flags = (list(args) + [None, None, None])[:3]
        if caminho is None or isinstance(caminho, int):
            return
        caminho = os.path.realpath(os.fsdecode(caminho))
        escreve = (isinstance(modo, str) and any(c in modo for c in 'wax+')) or (isinstance(flags, int) and flags & _ESCRITA)
        if escreve and not _dentro(caminho, _PASTA) and caminho != os.devnull:
            raise PermissionError('sandbox do bug: escrita fora da pasta do teste: ' + caminho)
        if caminho.startswith('/proc/'):
            raise PermissionError('sandbox do bug: /proc bloqueado')
        if _dentro(caminho, _RAIZ) and not _dentro(caminho, _PASTA) and not any(_dentro(caminho, p) for p in _LIBERADAS):
            raise PermissionError('sandbox do bug: a pasta real do portal não é lida pelo teste: ' + caminho)
    elif evento in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn', 'os.spawn', 'os.fork', 'pty.spawn'):
        raise PermissionError('sandbox do bug: o teste não pode abrir outros programas')
    elif evento == 'socket.connect':
        endereco = args[1] if len(args) > 1 else None
        host = endereco[0] if isinstance(endereco, tuple) and endereco else endereco
        if host not in ('127.0.0.1', 'localhost', '::1') and not (isinstance(host, str) and host.startswith('/')):
            raise PermissionError('sandbox do bug: sem internet no teste')


sys.addaudithook(_vigia)
sys.path.insert(0, os.getcwd())
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')
ALTERADOS = %(alterados)r
etapas_ok = True


def secao(titulo):
    print('\n### ' + titulo, flush=True)


secao('Compilação dos .py alterados')
for rel in ALTERADOS:
    if rel.endswith('.py'):
        try:
            py_compile.compile(rel, doraise=True)
            print('ok  ' + rel)
        except py_compile.PyCompileError as exc:
            etapas_ok = False
            print('ERRO ' + rel + ': ' + str(exc))
if not etapas_ok:
    print('\nRESULTADO: FALHOU (sintaxe)')
    sys.exit(1)

import django
django.setup()
from django.core.management import call_command
from django.test.utils import setup_test_environment

secao('manage.py check')
try:
    call_command('check')
except Exception as exc:
    etapas_ok = False
    print('ERRO: ' + str(exc))

secao('Templates alterados')
from django.template import TemplateSyntaxError
from django.template.loader import get_template
for rel in ALTERADOS:
    if rel.endswith('.html'):
        partes = rel.split('/')
        nome = '/'.join(partes[partes.index('templates') + 1:]) if 'templates' in partes else rel
        try:
            get_template(nome)
            print('ok  ' + nome)
        except TemplateSyntaxError as exc:
            etapas_ok = False
            print('ERRO ' + nome + ': ' + str(exc))
        except Exception as exc:
            print('aviso ' + nome + ': ' + type(exc).__name__ + ': ' + str(exc))

if not etapas_ok:
    print('\nRESULTADO: FALHOU (check/templates)')
    sys.exit(1)

secao('Banco de teste (SQLite, criado dos models)')
setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)
print('ok')

secao('Teste do bug')
try:
    runpy.run_path('caso_do_bug.py', run_name='__main__')
except SystemExit as exc:
    if exc.code not in (None, 0):
        traceback.print_exc()
        print('\nRESULTADO: FALHOU')
        sys.exit(1)
except BaseException:
    traceback.print_exc()
    print('\nRESULTADO: FALHOU')
    sys.exit(1)
print('\nRESULTADO: OK')
'''

PREAMBULO_CASO = '''# Preparado pelo portal: Django já configurado, banco SQLite vazio com todas as tabelas.
from django.test import Client                     # noqa: F401
from django.contrib.auth import get_user_model     # noqa: F401
User = get_user_model()
'''


def _ignorar(pasta, nomes):
    fora = []
    for nome in nomes:
        if nome in PASTAS_FORA or nome.startswith('.') or nome.endswith(('.pyc', '.xlsx', '.zip', '.mp4', '.exe')):
            fora.append(nome)
            continue
        import fnmatch
        if any(fnmatch.fnmatch(nome.lower(), p) for p in ARQUIVOS_SECRETOS):
            fora.append(nome)
    return fora


def _copiar_projeto(destino):
    shutil.copytree(RAIZ, destino, ignore=_ignorar, symlinks=False, dirs_exist_ok=False)


def _ambiente(pasta):
    env = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': str(pasta),
        'TMPDIR': str(pasta),
        'LANG': os.environ.get('LANG', 'C.UTF-8'),
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONIOENCODING': 'utf-8',
        'DJANGO_SETTINGS_MODULE': 'bug_sandbox_settings',
        'RC_VARREDURA_ROTINA': '0',
        'SECRET_KEY': 'sandbox-do-bug-nao-e-segredo',
        'DEBUG': 'False',
        # Saída para a internet por um proxy inexistente: nenhum pedido HTTP sai daqui.
        'HTTP_PROXY': 'http://127.0.0.1:9', 'HTTPS_PROXY': 'http://127.0.0.1:9',
        'http_proxy': 'http://127.0.0.1:9', 'https_proxy': 'http://127.0.0.1:9', 'NO_PROXY': '',
    }
    if os.environ.get('VIRTUAL_ENV'):
        env['VIRTUAL_ENV'] = os.environ['VIRTUAL_ENV']
    return env


def _rodar_uma(alteracoes, script, com_correcao):
    pasta = Path(tempfile.mkdtemp(prefix='bug-teste-'))
    src = pasta / 'src'
    try:
        _copiar_projeto(src)
        alterados = []
        if com_correcao:
            for a in alteracoes:
                alvo = src / a['caminho']
                alvo.parent.mkdir(parents=True, exist_ok=True)
                alvo.write_text(a['novo'], encoding='utf-8')
                alterados.append(a['caminho'])
        (src / 'bug_sandbox_settings.py').write_text(SETTINGS % {'pasta': pasta}, encoding='utf-8')
        (src / 'caso_do_bug.py').write_text(PREAMBULO_CASO + '\n' + (script or 'pass') + '\n', encoding='utf-8')
        (src / 'teste_bug_verificacao.py').write_text(HARNESS % {'alterados': alterados, 'raiz': str(RAIZ)},
                                                   encoding='utf-8')
        try:
            proc = subprocess.run(
                [sys.executable, 'teste_bug_verificacao.py'], cwd=src, env=_ambiente(pasta),
                capture_output=True, text=True, timeout=TEMPO_LIMITE)
            saida = (proc.stdout or '') + ('\n[stderr]\n' + proc.stderr if proc.stderr.strip() else '')
            ok = proc.returncode == 0 and 'RESULTADO: OK' in proc.stdout
        except subprocess.TimeoutExpired as exc:
            saida = ((exc.stdout or b'').decode('utf-8', 'replace') if isinstance(exc.stdout, bytes) else (exc.stdout or ''))
            saida += f'\n\nRESULTADO: FALHOU (passou de {TEMPO_LIMITE} s)'
            ok = False
        saida = saida.replace(str(src), '<projeto>').replace(str(pasta), '<teste>')
        if len(saida) > MAX_SAIDA:
            saida = saida[:3000] + '\n[… saída cortada …]\n' + saida[-(MAX_SAIDA - 3000):]
        return ok, saida
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def rodar(alteracoes, script):
    """{'ok': passou com a correção, 'reproduz': falhou sem ela, 'saida': texto para o Claude e a tela}."""
    if not alteracoes:
        ok, saida = _rodar_uma([], script, com_correcao=False)
        resumo = f'Ainda sem correção — código atual: {"PASSOU (o teste não pega o bug)" if ok else "FALHOU (reproduz o bug)"}'
        return {'ok': False, 'reproduz': not ok, 'resumo': resumo, 'saida': f'{resumo}\n\n{saida.strip()}'}
    ok, saida_com = _rodar_uma(alteracoes, script, com_correcao=True)
    sem_ok, saida_sem = _rodar_uma(alteracoes, script, com_correcao=False)
    resumo = (f'COM a correção: {"PASSOU" if ok else "FALHOU"} · '
              f'SEM a correção: {"passou (o teste não pega o bug)" if sem_ok else "falhou (o teste reproduz o bug)"}')
    texto = (f'{resumo}\n\n======== COM a correção ========\n{saida_com.strip()}\n\n'
             f'======== SEM a correção (código atual) ========\n{saida_sem.strip()[-4000:]}')
    return {'ok': ok, 'reproduz': not sem_ok, 'resumo': resumo, 'saida': texto}
