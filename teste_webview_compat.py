"""App Android (WebView): tela cinza e popups — camada de compatibilidade no <head> (11/11/2026).

Cobre: armazenamento desligado no WebView (reserva no window.name), alert/confirm viram o popup do portal
no app, janela nova abre na mesma tela, a camada é o 1º script de toda página. Banco SQLite descartável.
"""
import os
import pathlib
import re
import sys
import tempfile

import django
import json

PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-wv-compat-'))
(PASTA / 'zz_wv_compat_settings.py').write_text(f"""
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
    'default': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-wv-cp'}},
    'local': {{'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-wv-cp-2'}},
}}
""")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(PASTA))
os.environ['DJANGO_SETTINGS_MODULE'] = 'zz_wv_compat_settings'
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402
from django.test.utils import setup_test_environment  # noqa: E402

setup_test_environment()
call_command('migrate', run_syncdb=True, verbosity=0)

import glob  # noqa: E402
import re as _re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.template.loader import render_to_string  # noqa: E402

from portal_popups.models import PortalPopup  # noqa: E402
from users.models import Sector  # noqa: E402

User = get_user_model()
ok = fail = 0
RAIZ = pathlib.Path(__file__).resolve().parent
UA_APP = ('Mozilla/5.0 (Linux; Android 16; SM-S938B Build/CP2A; wv) AppleWebKit/537.36 (KHTML, like Gecko) '
          'Version/4.0 Chrome/153.0.0.0 Mobile Safari/537.36')


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


def primeiro_script(html):
    """O <script> da camada tem que ser o primeiro da página (antes do Tailwind e de tudo que usa storage)."""
    m = _re.search(r'<script\b[^>]*>(.*?)</script>', html, _re.S)
    return m.group(1) if m else ''


print('== PÁGINAS DO PORTAL ==')
loja = Sector.objects.create(name='Loja ZZ WV')
u = User.objects.create_user(username='zz.wvc', email='zz.wvc@exemplo-teste.local', password='S3nha!teste',
                             first_name='Zz', last_name='Compat', hierarchy='PADRAO', sector=loja)
PortalPopup.objects.create(title='Aviso de teste', message='x', completion_mode='ACK', target_all=True, blocking_mode='NEVER')
c = Client(HTTP_USER_AGENT=UA_APP)
c.force_login(u)
h = c.get('/', follow=True).content.decode()
t('home: a camada é o primeiro script', 'Armazenamento de reserva' in primeiro_script(h), primeiro_script(h)[:120])
t('home: a camada aparece uma vez só', h.count('Armazenamento de reserva') == 1)
t('home: o popup do portal está na página (e é ele que precisa fechar)', 'portal-popup-modal' in h)
t('home: o script do iPhone vem com versão nova', 'ios-notifications.js?v=20261111' in h)
t('login (sem sessão) também tem a camada', 'Armazenamento de reserva' in primeiro_script(Client().get('/login/', follow=True).content.decode()))

print('\n== MENU DO CELULAR (a "tela cinza" do print de 11/11) ==')
t('o fundo do menu só aparece com o menu aberto', 'html:not(.rc-menu-aberto) #mobileOverlay { display: none !important; }' in h)
t('no celular o menu entra sem animação (não depende de quadro)', _re.search(
    r'@media \(max-width: 1023px\) \{\s*#sidebar \{ transition: none !important; \}\s*html\.rc-menu-aberto #sidebar \{ transform: none !important; \}', h) is not None)
abrir = h[h.find('function openMobileMenu()'):h.find('function closeMobileMenu()')]
fechar = h[h.find('function closeMobileMenu()'):h.find('window.rcFecharMenuCelular')]
t('abrir marca rc-menu-aberto e fechar desmarca', "classList.add('rc-menu-aberto')" in abrir and "classList.remove('rc-menu-aberto')" in fechar)
t('fechar não depende da largura (sem "if (!isMobile()) return")', 'if (!isMobile()) return;' not in fechar, fechar[:200])
t('volta do cache (botão voltar) fecha o menu', "addEventListener('pageshow', function (e) { if (e.persisted) closeMobileMenu(); })" in h)
t('o fundo do menu continua sendo a única película abaixo do carrinho e do robô (z < 39)',
  len(_re.findall(r'class="[^"]*fixed inset-0[^"]*\bz-(?:10|20|30)\b', h)) == 1)

print('\n== PÁGINAS AVULSAS (sem base.html) ==')
avulsas = []
for caminho in sorted(glob.glob(str(RAIZ / 'templates' / '**' / '*.html'), recursive=True)):
    texto = open(caminho, encoding='utf-8').read()
    if '<html' in texto and '{% extends' not in texto and '/emails/' not in caminho:
        avulsas.append((caminho, texto))
sem = [p for p, x in avulsas if "_webview_compat.html" not in x]
t(f'todas as {len(avulsas)} páginas avulsas incluem a camada', not sem, sem)
fora_de_ordem = []
for p, x in avulsas:
    i_camada = x.find('_webview_compat.html')
    i_script = x.find('<script')
    if i_camada < 0 or (0 <= i_script < i_camada):
        fora_de_ordem.append(p)
t('nas avulsas, a camada vem antes de qualquer <script>', not fora_de_ordem, fora_de_ordem)
for nome in ('404.html', '500.html'):
    html = render_to_string(nome, {})
    t(f'{nome} renderiza sem contexto e com a camada', 'Armazenamento de reserva' in primeiro_script(html))

print('\n== O SCRIPT DA CAMADA ==')
camada = render_to_string('_webview_compat.html', {})
js = _re.search(r'<script>(.*?)</script>', camada, _re.S).group(1)
node = shutil.which('node')
if node:
    arq = PASTA / 'camada.js'
    arq.write_text(js)
    r = subprocess.run([node, '--check', str(arq)], capture_output=True, text=True)
    t('JS da camada com sintaxe válida', r.returncode == 0, r.stderr[:300])
    # Simula o WebView sem armazenamento (padrão do Android) e roda a camada no Node.
    simulacao = PASTA / 'simula.js'
    simulacao.write_text("""
var classes = [];
global.window = { name: '', localStorage: null, sessionStorage: null, confirm: function () { return false; },
                  alert: function () {}, open: function () { return null; }, event: undefined, __rcWebview: true };
global.navigator = { userAgent: 'x' };
global.document = { documentElement: { classList: { add: function (c) { classes.push(c); } } },
                    addEventListener: function () {}, body: null };
global.location = { href: 'https://portal.exemplo/', origin: 'https://portal.exemplo' };
global.URL = URL;
""" + js + """
var w = window;
w.sessionStorage.setItem('pulou', '1');
w.localStorage.setItem('tema', 'light');
var nome = w.name;
// "troca de página": o objeto window é novo, o window.name continua
global.window = { name: nome, localStorage: null, sessionStorage: null, confirm: function () { return false; },
                  alert: function () {}, open: function () { return null; }, __rcWebview: false };
""" + js + """
console.log(JSON.stringify({
  sessao: window.sessionStorage.getItem('pulou'), local: window.localStorage.getItem('tema'),
  nada: window.localStorage.getItem('nao-existe'), tamanho: window.localStorage.length,
  classeApp: classes.indexOf('rc-webview') >= 0
}));
""")
    r = subprocess.run([node, str(simulacao)], capture_output=True, text=True)
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        res = {}
    t('sem armazenamento: o que foi guardado sobrevive à troca de página (window.name)',
      res.get('sessao') == '1' and res.get('local') == 'light' and res.get('nada') is None and res.get('tamanho') == 1,
      (res, r.stderr[:300]))
    t('no app: marca a página com rc-webview', res.get('classeApp') is True, res)
else:
    print('  (node ausente: sintaxe e simulação não conferidas)')
t('o confirm só é trocado dentro do app', "if (!noApp) return;" in js and js.index('if (!noApp) return;') < js.index('window.confirm ='))
t('janela nova do próprio portal abre na mesma tela no app', 'location.href = destino.href' in js)
t('o botão Multitarefa some no app', '#multiJanelaBtn { display: none' in camada)

print(f'\n{ok} ok, {fail} falha(s)')
sys.exit(1 if fail else 0)
