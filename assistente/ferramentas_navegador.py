"""Navegador do assistente: BIs do /power-bi/ e qualquer link público, só leitura.

O Claude abre um relatório (pelo id) ou um link http(s), lê a página (texto,
números dos visuais e uma lista numerada do que dá para clicar), clica em
segmentações, abas e páginas, digita em campos de busca e tira print. Tudo num
Chromium sem login (Playwright): local, ou remoto quando ``NAVEGADOR_CDP_URL``
aponta para um navegador (ex.: um contêiner browserless).

Vida da sessão: um contexto de navegador por PERGUNTA. ``claude_client.responder``
roda o laço dentro de ``pergunta()``; a primeira ferramenta abre o navegador, as
seguintes reaproveitam a mesma aba (os filtros clicados continuam valendo) e,
no fim da pergunta, tudo fecha — dê certo ou não. Fora de ``pergunta()`` cada
chamada abre e fecha sozinha. No máximo ``NAVEGADOR_MAX_SIMULTANEOS`` navegadores
por processo; a pergunta tem um orçamento de tempo de navegador.

Segurança (do servidor, não do prompt):
- só http/https, portas 80/443, sem usuário/senha na URL;
- endereço que resolve para IP privado, loopback, link-local, reservado etc. é
  recusado — na abertura e em CADA requisição do navegador (rota do contexto),
  inclusive redirecionamentos, iframes e websockets; também os hosts internos
  do portal (banco, Redis, MinIO, o próprio navegador remoto);
- relatório só se ``is_visible_to(user)``; link de BI que a pessoa não vê é recusado;
- Power BI que exige login Microsoft (``/groups/...``), páginas de login e as
  páginas logadas do próprio portal não abrem (para o portal, ``ler_pagina``);
- nada de senha: não digita em campo de senha/token/cartão, não envia formulário
  (navegação POST é barrada), não clica em botão de enviar/salvar/excluir/entrar,
  não baixa arquivo.
"""
import base64
import functools
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import contextmanager
from urllib.parse import parse_qs, urljoin, urlsplit

from django.conf import settings

logger = logging.getLogger(__name__)

VIEWPORT = {'width': 1366, 'height': 900}
TIMEOUT_NAVEGACAO_MS = 45_000
TIMEOUT_ACAO_MS = 15_000
ORCAMENTO_PERGUNTA_S = 300          # tempo total de navegador numa pergunta
ESPERA_VAGA_S = 30                  # quanto espera por um navegador livre
LIMITE_TEXTO = 20_000               # caracteres por parte da leitura
LIMITE_ELEMENTOS = 150
MAX_LINHAS_TABELA = 40
QUALIDADE_PRINT = 60                # JPEG

PORTAS_PERMITIDAS = {80, 443}
HOSTS_DE_LOGIN = {
    'login.microsoftonline.com', 'login.live.com', 'login.windows.net', 'login.microsoft.com',
    'accounts.google.com',
}
# Páginas do portal que abrem sem login (o navegador do assistente é anônimo).
PORTAL_PUBLICO = ('/facilitador/p/', '/static/', '/media/')
SUFIXOS_INTERNOS = ('.localhost', '.local', '.internal', '.lan', '.home.arpa', '.intranet', '.corp')
TECLAS_PERMITIDAS = {
    'Enter', 'Escape', 'Tab', 'Shift+Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
    'PageUp', 'PageDown', 'Home', 'End', 'Space', 'Backspace', 'Delete',
}
# Campo onde não se digita, nem a pedido.
CAMPO_SENSIVEL = re.compile(
    r'senha|password|passwd|pwd|passcode|token|secret|segredo|cvv|cvc|cart[aã]o|card.?number|'
    r'cc-|cpf|otp|2fa|mfa|c[oó]digo de (verifica|seguran)|one.?time', re.I)
# Botão que muda dado ou entra em conta: o assistente só lê e filtra.
BOTAO_PROIBIDO = re.compile(
    r'^\s*(excluir|apagar|deletar|remover|enviar|salvar|gravar|comprar|pagar|assinar|publicar|'
    r'finalizar|cadastrar|registrar|inscrever|entrar|login|log in|sign in|sign up|delete|remove|'
    r'submit|send|save|buy|pay|subscribe|publish|register)\b', re.I)


class Indisponivel(Exception):
    """O navegador não pode ser usado agora (não instalado, ocupado, sem tempo)."""


class Recusado(Exception):
    """Pedido fora das regras (endereço bloqueado, campo de senha, etc.)."""


# ─── Regras de endereço ──────────────────────────────────────────────────────

def _host_de(url):
    try:
        return (urlsplit(url).hostname or '').lower().rstrip('.')
    except ValueError:
        return ''


def _portal_hosts():
    host = _host_de(getattr(settings, 'BASE_URL', '') or '')
    if not host:
        return set()
    return {host, host[4:] if host.startswith('www.') else 'www.' + host}


def _hosts_internos():
    """Serviços do próprio portal que o navegador nunca deve alcançar."""
    hosts = set()
    candidatos = [
        getattr(settings, 'REDIS_URL', ''), getattr(settings, 'CELERY_BROKER_URL', ''),
        getattr(settings, 'AWS_S3_ENDPOINT_URL', ''), _cdp_url(),
    ]
    try:
        hosts.add((settings.DATABASES['default'].get('HOST') or '').lower())
    except Exception:  # noqa: BLE001
        pass
    for c in candidatos:
        if c:
            hosts.add(_host_de(str(c)))
    extra = getattr(settings, 'NAVEGADOR_HOSTS_BLOQUEADOS', '') or os.environ.get('NAVEGADOR_HOSTS_BLOQUEADOS', '')
    hosts.update(h.strip().lower() for h in str(extra).split(',') if h.strip())
    hosts.discard('')
    return hosts


def _ip_publico(ip):
    try:
        ip = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_unspecified


def _resolver(host, porta):
    """IPs do host (via DNS). Separado para os testes trocarem."""
    return {info[4][0] for info in socket.getaddrinfo(host, porta, proto=socket.IPPROTO_TCP)}


# Áreas do app.powerbi.com que pedem conta Microsoft. O relatório público (/view)
# abre iframes do próprio app.powerbi.com (/13.0.../visualSandbox...): esses passam.
_POWERBI_COM_LOGIN = ('/groups/', '/home', '/signin', '/singlesignon', '/apps/', '/links/', '/reports/',
                      '/myorg/', '/datahub', '/redirect')


def _powerbi_com_login(caminho):
    caminho = (caminho or '/').lower()
    return caminho == '/' or caminho.startswith(_POWERBI_COM_LOGIN)


def checar_endereco(url, navegacao=True, metodo='GET', cache_hosts=None):
    """None se pode; senão o motivo (texto) da recusa.

    ``navegacao`` = a requisição troca o documento (página ou iframe); as regras
    de login e de páginas do portal valem só para ela, o resto vale para tudo.
    """
    try:
        partes = urlsplit(url)
        porta = partes.port
    except ValueError:
        return 'endereço inválido'
    esquema = (partes.scheme or '').lower()
    if esquema not in ('http', 'https'):
        return f'só endereços http/https (veio {esquema or "sem esquema"})'
    if partes.username or partes.password:
        return 'endereço com usuário/senha embutidos não é aceito'
    host = (partes.hostname or '').lower().rstrip('.')
    if not host:
        return 'endereço sem servidor'
    if porta is not None and porta not in PORTAS_PERMITIDAS:
        return f'porta {porta} não permitida (só 80 e 443)'
    if navegacao and metodo.upper() != 'GET':
        return 'envio de formulário bloqueado: o assistente só lê e filtra'

    if host in _portal_hosts():
        caminho = partes.path or '/'
        if any(caminho.startswith(p) for p in PORTAL_PUBLICO):
            return None
        return ('página logada do próprio portal: o navegador do assistente não tem a sessão do '
                f'usuário. Use ler_pagina(caminho="{caminho}") no lugar')

    if navegacao and (host in HOSTS_DE_LOGIN or host.startswith('login.')):
        return 'página de login: o assistente não entra em contas'
    if navegacao and host == 'app.powerbi.com' and _powerbi_com_login(partes.path):
        return ('este Power BI exige login Microsoft (link /groups/...): o assistente não consegue '
                'ler. Só os relatórios publicados na web (/view?r=...) abrem')

    if host == 'localhost' or host.endswith(SUFIXOS_INTERNOS) or ('.' not in host and ':' not in host):
        return 'endereço interno não permitido'
    if host in _hosts_internos():
        return 'serviço interno do portal não permitido'

    chave = (host, porta)
    if cache_hosts is not None and chave in cache_hosts:
        return cache_hosts[chave]
    motivo = None
    try:
        ipaddress.ip_address(host)
        ips = {host}
    except ValueError:
        try:
            ips = _resolver(host, porta or (443 if esquema == 'https' else 80))
        except (socket.gaierror, UnicodeError, OSError):
            ips = set()
            motivo = 'não consegui resolver o endereço'
    if not motivo and (not ips or not all(_ip_publico(ip) for ip in ips)):
        motivo = 'o endereço aponta para uma rede interna/privada'
    if cache_hosts is not None:
        cache_hosts[chave] = motivo
    return motivo


# ─── Relatórios do /power-bi/ ────────────────────────────────────────────────

def _tipo_link(url):
    host = _host_de(url)
    caminho = (urlsplit(url).path or '').lower()
    if host == 'app.powerbi.com':
        return 'powerbi_exige_login' if _powerbi_com_login(caminho) else 'powerbi_publico'
    if host in _portal_hosts():
        return 'portal_publico' if any(caminho.startswith(p) for p in PORTAL_PUBLICO) else 'portal_logado'
    if host == 'docs.google.com':
        return 'google_planilha' if '/spreadsheets/' in caminho else 'google_docs'
    return 'site'


_NOTA_TIPO = {
    'powerbi_publico': 'Power BI publicado na web: abre e dá para filtrar.',
    'powerbi_exige_login': 'Exige login Microsoft: o assistente não consegue abrir.',
    'portal_publico': 'Página pública do portal: abre.',
    'portal_logado': 'Página do portal com login: use ler_pagina.',
    'google_planilha': 'Planilha Google: abre se estiver pública (desenhada em canvas: use print).',
    'google_docs': 'Documento Google: abre se estiver público.',
    'site': 'Site externo: abre se for público.',
}


def _relatorios_visiveis(user):
    from power_bi.models import PowerBIReport
    return [r for r in PowerBIReport.objects.filter(is_active=True).order_by('sort_order', 'name')
            if r.is_visible_to(user)]


def _chave_bi(url):
    """Identidade de um link de BI: o r= do Power BI público, ou o link sem fragmento."""
    try:
        partes = urlsplit(url)
    except ValueError:
        return url
    r = parse_qs(partes.query).get('r')
    if partes.hostname == 'app.powerbi.com' and r:
        return 'pbi:' + r[0]
    return f'{partes.hostname}{partes.path}'.rstrip('/').lower()


def _bi_proibido(user, url):
    """O link é de um BI ativo que esta pessoa NÃO vê? Então não abre pelo link."""
    from power_bi.models import PowerBIReport
    chave = _chave_bi(url)
    visiveis = {_chave_bi(r.embed_url) for r in _relatorios_visiveis(user)}
    if chave in visiveis:
        return False
    return any(_chave_bi(r.embed_url) == chave for r in PowerBIReport.objects.filter(is_active=True))


def _powerbi_relatorios(user, args):
    itens = []
    for r in _relatorios_visiveis(user):
        tipo = _tipo_link(r.embed_url)
        itens.append({'id': r.pk, 'nome': r.name, 'descricao': (r.description or '')[:200],
                      'tipo_link': tipo, 'site': _host_de(r.embed_url),
                      'pode_abrir': tipo not in ('powerbi_exige_login', 'portal_logado'),
                      'nota': _NOTA_TIPO[tipo]})
    if not itens:
        return 'Você não tem nenhum relatório liberado no /power-bi/.'
    return json.dumps({'relatorios': itens, 'como_usar': (
        'Abra com navegador_abrir(relatorio_id=...). Depois navegador_ler, navegador_clicar (segmentações, '
        'abas), powerbi_pagina (páginas do relatório) e navegador_print para ver o visual.')},
        ensure_ascii=False)


# ─── Sessão por pergunta ─────────────────────────────────────────────────────

def _cdp_url():
    return (getattr(settings, 'NAVEGADOR_CDP_URL', '') or os.environ.get('NAVEGADOR_CDP_URL', '') or '').strip()


def _max_simultaneos():
    try:
        return max(1, int(getattr(settings, 'NAVEGADOR_MAX_SIMULTANEOS', 0)
                          or os.environ.get('NAVEGADOR_MAX_SIMULTANEOS', 2)))
    except (TypeError, ValueError):
        return 2


_VAGAS = threading.BoundedSemaphore(_max_simultaneos())
_local = threading.local()


class _Pergunta:
    def __init__(self):
        self.sessao = None
        self.falha = ''


class Sessao:
    """Um navegador + contexto + aba(s) para uma pergunta.

    O Playwright (API síncrona) deixa um laço asyncio "rodando" na thread em que
    começa, e aí o ORM do Django recusa trabalhar nela. Por isso todo o navegador
    vive numa thread só dele (``rodar``); a thread da requisição segue livre para
    o banco e para as outras ferramentas.
    """

    def __init__(self):
        self.pw = self.browser = self.context = self.page = None
        self.paginas = []
        self.elementos = {}          # número → frame onde ele está
        self.bloqueios = []          # últimos motivos de bloqueio, para explicar falhas
        self.hosts = {}              # cache de DNS/decisão por host
        self.inicio = time.monotonic()
        self.fechada = False
        self.travada = False
        self._vaga = False
        self._exec = None

    def rodar(self, fn, *args):
        """Roda ``fn`` na thread do navegador e devolve o resultado (ou a exceção)."""
        if self._exec is None:
            self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix='assistente-navegador')
        futuro = self._exec.submit(fn, *args)
        try:
            return futuro.result(timeout=max(self.restante(), 5) + 60)
        except FuturesTimeout:
            self.travada = True
            raise Indisponivel('O navegador do assistente travou nesta pergunta. Responda com o que já foi lido.')

    # abrir/fechar ------------------------------------------------------------
    def abrir(self):
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            raise Indisponivel('O navegador do assistente não está instalado neste servidor (Playwright).')
        if not _VAGAS.acquire(timeout=ESPERA_VAGA_S):
            raise Indisponivel('O navegador do assistente está ocupado com outras perguntas. Tente em instantes.')
        self._vaga = True
        try:
            self.rodar(self._abrir_na_thread)
        except Exception as exc:  # noqa: BLE001
            logger.warning('Navegador do assistente não abriu: %s', exc)
            self.fechar()
            if isinstance(exc, Indisponivel):
                raise
            raise Indisponivel('Não consegui abrir o navegador do assistente agora.')

    def _abrir_na_thread(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        cdp = _cdp_url()
        if cdp:
            self.browser = self.pw.chromium.connect_over_cdp(cdp, timeout=20_000)
        else:
            canal = (getattr(settings, 'NAVEGADOR_CANAL', '') or os.environ.get('NAVEGADOR_CANAL', '')).strip()
            self.browser = self.pw.chromium.launch(
                headless=True, channel=canal or None, timeout=30_000,
                args=['--disable-dev-shm-usage', '--no-first-run', '--disable-extensions'])
        self.context = self.browser.new_context(
            viewport=VIEWPORT, locale='pt-BR', timezone_id='America/Sao_Paulo',
            accept_downloads=False, service_workers='block')
        self.context.set_default_timeout(TIMEOUT_ACAO_MS)
        self.context.set_default_navigation_timeout(TIMEOUT_NAVEGACAO_MS)
        self.context.route('**/*', self._rota)
        if hasattr(self.context, 'route_web_socket'):
            self.context.route_web_socket(re.compile('.*'), self._rota_ws)
        self.context.on('page', self._nova_aba)
        self.page = self.context.new_page()

    def _fechar_na_thread(self):
        # Rotas ainda em andamento (site lento) não podem estourar erro depois do fechamento.
        for passo in (lambda: self.context and self.context.unroute_all(behavior='ignoreErrors'),
                      lambda: self.context and self.context.close(),
                      lambda: self.browser and self.browser.close(),
                      lambda: self.pw and self.pw.stop()):
            _silencioso(passo)

    def fechar(self):
        if self.fechada:
            return
        self.fechada = True
        if self._exec is not None:
            if not self.travada:
                try:
                    self._exec.submit(self._fechar_na_thread).result(timeout=30)
                except Exception:  # noqa: BLE001
                    logger.warning('Navegador do assistente não fechou direito.')
            self._exec.shutdown(wait=not self.travada)
        if self._vaga:
            self._vaga = False
            _VAGAS.release()

    def restante(self):
        return ORCAMENTO_PERGUNTA_S - (time.monotonic() - self.inicio)

    # rotas e abas ------------------------------------------------------------
    def _bloquear(self, motivo, url):
        logger.info('Navegador do assistente bloqueou %s: %s', url[:200], motivo)
        item = f'{motivo} ({_host_de(url) or url[:60]})'
        if item not in self.bloqueios:
            self.bloqueios = (self.bloqueios + [item])[-8:]

    def _rota(self, route, request=None):
        request = request or route.request
        try:
            navegacao = request.is_navigation_request()
        except Exception:  # noqa: BLE001
            navegacao = request.resource_type == 'document'
        url = request.url
        if url.startswith(('data:', 'blob:', 'about:')):
            return route.continue_()
        if not url.startswith(('http://', 'https://')):
            return route.abort('blockedbyclient')      # chrome-extension:, filesystem:, file: …
        motivo = checar_endereco(url, navegacao=navegacao, metodo=request.method, cache_hosts=self.hosts)
        if motivo:
            self._bloquear(motivo, url)
            return route.abort('blockedbyclient')
        if navegacao:
            return self._navegar_sem_redirecionar(route, url)
        return route.continue_()

    def _navegar_sem_redirecionar(self, route, url):
        """Página/iframe: o redirecionamento do servidor também passa pela regra.

        A rota do Playwright não vê os saltos de um redirecionamento (o navegador
        segue sozinho). Então o documento é buscado sem seguir; se vier 3xx, o
        destino é conferido e a página navega até ele por conta própria — o que é
        uma navegação nova, de novo pela rota.
        """
        try:
            resp = route.fetch(max_redirects=0, timeout=TIMEOUT_NAVEGACAO_MS)
        except Exception as exc:  # noqa: BLE001 — site fora do ar, DNS, tempo, sessão fechando
            logger.info('Navegador do assistente: falha ao buscar %s: %s', url[:200], str(exc).split('\n')[0])
            return _silencioso(lambda: route.abort('failed'))
        if 300 <= resp.status < 400:
            destino = urljoin(url, resp.headers.get('location') or '')
            motivo = checar_endereco(destino, navegacao=True, cache_hosts=self.hosts)
            if motivo or not destino:
                self._bloquear(motivo or 'redirecionamento sem destino', destino or url)
                return route.abort('blockedbyclient')
            alvo = json.dumps(destino).replace('<', '\\u003c')
            return route.fulfill(
                status=200, content_type='text/html; charset=utf-8',
                body=f'<!doctype html><meta name="robots" content="noindex"><script>location.replace({alvo})</script>')
        return route.fulfill(response=resp)

    def _rota_ws(self, ws):
        url = ws.url.replace('wss://', 'https://', 1).replace('ws://', 'http://', 1)
        motivo = checar_endereco(url, navegacao=False, cache_hosts=self.hosts)
        if motivo:
            self._bloquear(motivo, url)
            ws.close()
        else:
            ws.connect_to_server()

    def _preparar_aba(self, page):
        page.on('dialog', lambda d: _silencioso(d.dismiss))
        page.on('download', lambda d: _silencioso(d.cancel))

    def _nova_aba(self, page):
        # Link que abre em nova guia: a aba nova vira a atual (a anterior volta com navegador_voltar).
        self._preparar_aba(page)
        if self.page is not None and page is not self.page:
            self.paginas.append(self.page)
        self.page = page
        self.elementos = {}


def _silencioso(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


@contextmanager
def pergunta():
    """Uma pergunta do assistente: o navegador (se usado) fecha na saída."""
    anterior = getattr(_local, 'pergunta', None)
    atual = _Pergunta()
    _local.pergunta = atual
    try:
        yield atual
    finally:
        _local.pergunta = anterior
        if atual.sessao is not None:
            atual.sessao.fechar()


def _sessao():
    p = _local.pergunta
    if p.falha:
        raise Indisponivel(p.falha)
    s = p.sessao
    if s is None or s.fechada:
        s = Sessao()
        try:
            s.abrir()
        except Indisponivel as exc:
            p.falha = str(exc)
            raise
        p.sessao = s
    if s.restante() <= 0 or s.travada:
        s.fechar()
        p.falha = ('O tempo de navegador desta pergunta acabou. Responda com o que já foi lido ou peça '
                   'ao usuário para perguntar de novo com um foco menor.')
        raise Indisponivel(p.falha)
    return s


def _protegido(nome, corpo):
    """Roda ``corpo()`` dentro de uma pergunta e devolve texto em vez de levantar."""
    if getattr(_local, 'pergunta', None) is None:
        with pergunta():
            return _protegido(nome, corpo)
    try:
        return corpo()
    except (Indisponivel, Recusado) as exc:
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        tipo = type(exc).__name__
        if 'Timeout' in tipo:
            return ('A página demorou demais para responder (o site pode estar lento ou ainda carregando). '
                    'Use navegador_esperar para dar mais tempo e reler, ou siga por um caminho mais direto.')
        logger.warning('Navegador do assistente (%s): %s', nome, exc)
        msg = str(exc).split('\n')[0][:200]
        return f'Não consegui fazer isso no navegador ({tipo}: {msg}).'


def _no_navegador(fn):
    """Ferramenta que só mexe no navegador: ``fn(sessao, args)`` roda na thread dele."""
    @functools.wraps(fn)
    def embrulho(user, args):
        def corpo():
            s = _sessao()
            return s.rodar(fn, s, args or {})
        return _protegido(fn.__name__, corpo)
    return embrulho


# ─── Leitura da página ───────────────────────────────────────────────────────

_JS_LER = r"""
(arg) => {
  const inicio = arg.inicio, maxEl = arg.max;
  const W = window.innerWidth, H = window.innerHeight;
  const limpa = s => (s || '').replace(/\s+/g, ' ').trim();
  const visivel = e => {
    const r = e.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(e);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05;
  };
  document.querySelectorAll('[data-rc-nav]').forEach(e => e.removeAttribute('data-rc-nav'));
  const SEL = 'a[href],button,select,textarea,input:not([type=hidden]),[role=button],[role=link],[role=tab],' +
    '[role=option],[role=menuitem],[role=menuitemcheckbox],[role=menuitemradio],[role=checkbox],[role=radio],' +
    '[role=combobox],[role=switch],[role=treeitem],[role=slider],[role=searchbox],[contenteditable=true],' +
    'li.animated,.slicerItemContainer,[onclick]';
  const SENS = /senha|password|passwd|pwd|token|secret|cvv|cvc|card|cpf|otp/i;
  const nomeDe = e => {
    let n = e.getAttribute('aria-label');
    const lb = e.getAttribute('aria-labelledby');
    if (!n && lb) n = lb.split(/\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
    if (!n && e.labels && e.labels.length) n = e.labels[0].innerText;
    if (!n && e.tagName === 'SELECT') n = e.name || e.id || 'lista';
    if (!n) n = e.innerText;
    if (!n) n = e.getAttribute('title') || e.getAttribute('placeholder') || e.getAttribute('alt') || '';
    if (!n && e.tagName === 'INPUT' && e.type !== 'password') n = e.value;
    return limpa(n).slice(0, 90);
  };
  // Controles do próprio visualizador do Power BI que não servem para ler/filtrar.
  const RUIDO = /^(Mostrar os atalhos|Mostrar dicas do leitor|Pular para o conte|Compartilhar|Reduzir$|Ampliar$|N[ií]vel de zoom|Ajustar [àa] p[áa]gina|Microsoft Power BI|Abrir no modo de tela inteira|Selecionar Linha$|Sele[çc][ãa]o de Linha$)/i;
  const els = [];
  let n = inicio, omitidos = 0;
  for (const e of document.querySelectorAll(SEL)) {
    if (!visivel(e)) continue;
    const nome = nomeDe(e);
    if (RUIDO.test(nome)) continue;
    const tag = e.tagName.toLowerCase();
    let tipo = e.getAttribute('role') || (tag === 'input' ? 'input:' + (e.type || 'text') : tag);
    if (tag === 'li' && e.classList.contains('animated')) tipo = 'pagina_do_relatorio';
    if (!nome && !['input', 'select', 'textarea'].includes(tag) && !tipo.startsWith('input')) {
      // sem nome: só vale se for um botão grande (ex.: botão-imagem do Power BI)
      const r0 = e.getBoundingClientRect();
      if (r0.width * r0.height < 900) continue;
    }
    if (els.length >= maxEl) { omitidos++; continue; }
    const r = e.getBoundingClientRect();
    const est = [];
    for (const a of ['aria-selected', 'aria-checked', 'aria-expanded', 'aria-pressed']) {
      const v = e.getAttribute(a);
      if (v === 'true') est.push(a.slice(5) === 'expanded' ? 'aberto' : 'marcado');
    }
    if (e.disabled || e.getAttribute('aria-disabled') === 'true') est.push('desativado');
    if (tag === 'select') {
      est.push('valor=' + limpa((e.options[e.selectedIndex] || {}).text).slice(0, 40));
      const ops = Array.from(e.options).map(o => limpa(o.text).slice(0, 30));
      est.push('opções: ' + ops.slice(0, 25).join(' / ') + (ops.length > 25 ? ' / … +' + (ops.length - 25) : ''));
    }
    else if ((tag === 'input' || tag === 'textarea') && e.type !== 'password' && !SENS.test(e.name + ' ' + e.id) && e.value)
      est.push('valor=' + limpa(e.value).slice(0, 40));
    if (tipo === 'combobox' && e.innerText) est.push('valor=' + limpa(e.innerText).slice(0, 40));
    const cx = Math.round(r.x + r.width / 2), cy = Math.round(r.y + r.height / 2);
    if (cx < 0 || cy < 0 || cx > W || cy > H) est.push('fora da tela');
    if (tag === 'a' && e.href && !e.href.startsWith('javascript:')) est.push('link=' + e.href.slice(0, 120));
    e.setAttribute('data-rc-nav', String(n));
    els.push({n: n, tipo: tipo, nome: nome, estado: est.join(', '), x: cx, y: cy});
    n++;
  }

  // Power BI: números dos visuais (aria-label dos cartões/pontos, linhas das tabelas).
  const IGN = /^(Navegando para visual|Limpar sele|Pressionar Enter|Mais op|Modo de foco|Filtros|Copiar|Expandir|Classificar|Sort|More options|Focus mode|Clear selections)/i;
  const visuais = [];
  for (const v of document.querySelectorAll('.visualContainer')) {
    if (!visivel(v)) continue;
    const r = v.getBoundingClientRect();
    const tit = limpa((v.querySelector('.visualTitle, [data-testid="visual-title"], .slicer-header-text') || {}).innerText);
    const rotulos = [];
    const vistos = new Set();
    const add = s => { s = limpa(s); if (s && !IGN.test(s) && !vistos.has(s) && rotulos.length < 40) { vistos.add(s); rotulos.push(s.slice(0, 220)); } };
    for (const e of v.querySelectorAll('[aria-label]')) {
      if (e.closest('.slicer-dropdown-popup')) continue;
      add(e.getAttribute('aria-label'));
    }
    const linhas = [];
    for (const row of v.querySelectorAll('[role=row]')) {
      const cel = Array.from(row.querySelectorAll('[role=gridcell],[role=columnheader],[role=rowheader],[role=cell]')).map(c => limpa(c.innerText)).filter(Boolean);
      if (cel.length) linhas.push(cel.join(' | ').slice(0, 300));
      if (linhas.length >= 80) break;
    }
    let txt = limpa(v.innerText).replace(/Pressionar Enter para explorar os dados/g, '').trim();
    if (!rotulos.length && !linhas.length && !txt) continue;
    visuais.push({titulo: tit, rotulos: rotulos, linhas: linhas, texto: txt.slice(0, 400),
                  pos: Math.round(r.x) + ',' + Math.round(r.y), fora: (r.y > H || r.y + r.height < 0)});
  }
  const filtros = Array.from(document.querySelectorAll('.visualContainer [role=combobox]')).filter(visivel)
    .map(e => limpa(e.getAttribute('aria-label')) + ' = ' + limpa(e.innerText)).slice(0, 30);
  const pag = document.querySelector('a.middleText, .navigation-wrapper .middleText');
  return {
    url: location.href, titulo: document.title,
    texto: (document.body ? document.body.innerText : '').replace(/[ \t]+/g, ' ').replace(/\n\s*\n+/g, '\n'),
    els: els, omitidos: omitidos, visuais: visuais, filtros: filtros,
    pagina_pbi: pag ? limpa(pag.innerText) : '',
    powerbi: !!document.querySelector('.visualContainer, visual-container, .exploration'),
  };
}
"""

_JS_CARREGANDO = r"""
() => {
  const vis = e => { const r = e.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(e); return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity || '1') > 0.05; };
  return {vc: document.querySelectorAll('.visualContainer').length,
          sp: Array.from(document.querySelectorAll('.powerbi-spinner, .circle')).filter(vis).length,
          pbi: location.hostname === 'app.powerbi.com'};
}
"""


def _esperar(page, maximo=25.0):
    """Espera a página assentar. No Power BI: visuais desenhados e sem spinner."""
    try:
        page.wait_for_load_state('domcontentloaded', timeout=TIMEOUT_NAVEGACAO_MS)
    except Exception:  # noqa: BLE001
        pass
    limite = time.monotonic() + maximo
    estavel = 0
    while time.monotonic() < limite:
        try:
            st = page.evaluate(_JS_CARREGANDO)
        except Exception:  # noqa: BLE001
            st = {'pbi': False}
        if not st.get('pbi'):
            try:
                # Site comum: espera a rede acalmar, mas sem ficar preso em página que nunca para.
                page.wait_for_load_state('networkidle', timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            return
        if st['vc'] and not st['sp']:
            estavel += 1
            if estavel >= 2:
                page.wait_for_timeout(800)
                return
        else:
            estavel = 0
        page.wait_for_timeout(700)


def _fmt_leitura(s, dados_por_frame, parte=1, busca=''):
    principal = dados_por_frame[0][1]
    linhas = [f'URL: {principal["url"][:300]}', f'Título: {principal["titulo"][:150]}']
    if principal.get('pagina_pbi'):
        linhas.append(f'Power BI — página {principal["pagina_pbi"]} (troque com powerbi_pagina; '
                      'powerbi_pagina sem "pagina" lista todas)')
    if principal.get('filtros'):
        linhas.append('Segmentações (filtros): ' + ' | '.join(principal['filtros']))
    if s.bloqueios:
        linhas.append('Bloqueado pelo portal nesta pergunta: ' + '; '.join(s.bloqueios[-3:]))

    termo = _normaliza(busca)
    corpo = []
    els = []
    for _, d in dados_por_frame:
        for e in d.get('els') or []:
            if termo and termo not in _normaliza(e['nome'] + ' ' + e['tipo']):
                continue
            est = f' [{e["estado"]}]' if e['estado'] else ''
            els.append(f'[{e["n"]}] {e["tipo"]} "{e["nome"]}"{est} @({e["x"]},{e["y"]})')
    omitidos = sum(d.get('omitidos') or 0 for _, d in dados_por_frame)
    if els:
        corpo.append('\nELEMENTOS (use o número em navegador_clicar / navegador_digitar; @x,y = posição no print):')
        corpo.extend(els)
        if omitidos:
            corpo.append(f'… mais {omitidos} elementos omitidos — use navegador_ler(busca="...") para achar um.')

    visuais = principal.get('visuais') or []
    blocos_v = []
    for v in visuais:
        txt = ' · '.join(v['rotulos'] or ([v['texto']] if v['texto'] else []))
        cab = (f'- {v["titulo"]}: ' if v['titulo'] else '- ') + txt
        if v.get('fora'):
            cab += ' (role a página para ver)'
        linhas_t = v['linhas']
        if termo and termo not in _normaliza(cab):
            # Na busca, da tabela ficam o cabeçalho e as linhas que batem.
            linhas_t = linhas_t[:1] + [l for l in linhas_t[1:] if termo in _normaliza(l)]
            if len(linhas_t) <= 1:
                continue
        bloco = [cab] + [f'    {l}' for l in linhas_t[:MAX_LINHAS_TABELA]]
        if len(linhas_t) > MAX_LINHAS_TABELA:
            bloco.append(f'    … +{len(linhas_t) - MAX_LINHAS_TABELA} linhas (use busca, ou role dentro da tabela)')
        blocos_v.append('\n'.join(bloco))
    if blocos_v:
        corpo.append('\nVISUAIS (números lidos dos gráficos, cartões e tabelas):')
        corpo.extend(blocos_v)

    for i, (rotulo, d) in enumerate(dados_por_frame):
        texto = d.get('texto') or ''
        if termo:
            texto = '\n'.join(l for l in texto.split('\n') if termo in _normaliza(l))
        # No Power BI o texto solto repete os visuais: só vale o dos quadros embutidos.
        if texto.strip() and not (d.get('powerbi') and visuais and i == 0):
            corpo.append(f'\nTEXTO{rotulo}:\n{texto.strip()}')

    texto = '\n'.join(corpo)
    total = max(1, -(-len(texto) // LIMITE_TEXTO))
    parte = min(max(1, int(parte or 1)), total)
    trecho = texto[(parte - 1) * LIMITE_TEXTO: parte * LIMITE_TEXTO]
    if total > 1:
        linhas.append(f'Parte {parte} de {total} (peça navegador_ler(parte=N) para o resto).')
    return '\n'.join(linhas) + '\n' + trecho


def _normaliza(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()
    return ' '.join(s.split())


def _ler(s, parte=1, busca=''):
    page = s.page
    if page is None or page.url in ('', 'about:blank'):
        raise Recusado('Nenhuma página aberta. Use navegador_abrir primeiro.')
    s.elementos = {}
    dados = []
    inicio = 1
    quadros = [page.main_frame] + [f for f in page.frames if f is not page.main_frame][:20]
    for i, frame in enumerate(quadros):
        arg = {'inicio': inicio, 'max': LIMITE_ELEMENTOS if i == 0 else 40}
        try:
            d = frame.evaluate(_JS_LER, arg)
        except Exception as exc:  # noqa: BLE001
            if i or 'context was destroyed' not in str(exc):
                continue
            # A página trocou no meio da leitura (redirecionamento, clique em link): lê a nova.
            _esperar(page, maximo=10.0)
            try:
                d = frame.evaluate(_JS_LER, arg)
            except Exception:  # noqa: BLE001
                continue
        if i and not (d.get('texto', '').strip() or d.get('els')):
            continue
        for e in d.get('els') or []:
            s.elementos[e['n']] = frame
        inicio += len(d.get('els') or [])
        if i:
            d['visuais'] = []
            d['texto'] = (d.get('texto') or '')[:1500]
        dados.append(('' if i == 0 else f' (quadro {i}: {(frame.url or "embutido")[:80]})', d))
    if not dados:
        raise Recusado('Não consegui ler a página.')
    return _fmt_leitura(s, dados, parte, busca)


def _elemento(s, numero):
    try:
        numero = int(numero)
    except (TypeError, ValueError):
        raise Recusado('Informe o número do elemento (da última navegador_ler).')
    frame = s.elementos.get(numero)
    if frame is None:
        raise Recusado(f'Elemento {numero} não está na última leitura. Rode navegador_ler de novo.')
    loc = frame.locator(f'[data-rc-nav="{numero}"]').first
    if not loc.count():
        raise Recusado(f'O elemento {numero} sumiu (a página mudou). Rode navegador_ler de novo.')
    return loc


_JS_INFO = r"""
(e) => {
  const form = e.closest('form');
  const txt = (e.getAttribute('aria-label') || e.innerText || e.value || e.getAttribute('title') || '').trim();
  return {tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(),
          attrs: [e.name, e.id, e.getAttribute('autocomplete'), e.getAttribute('placeholder'),
                  e.getAttribute('aria-label')].filter(Boolean).join(' '),
          texto: txt.slice(0, 80),
          form_com_senha: !!(form && form.querySelector('input[type=password]')),
          pagina_com_senha: !!document.querySelector('input[type=password]')};
}
"""


def _depois_da_acao(s, aviso=''):
    _esperar(s.page, maximo=20.0)
    return (aviso + '\n' if aviso else '') + _ler(s)


# ─── Ferramentas ─────────────────────────────────────────────────────────────

def _abrir(user, args):
    return _protegido('navegador_abrir', lambda: _abrir_corpo(user, args or {}))


def _abrir_corpo(user, args):
    # Permissões (banco) aqui, na thread da requisição; o navegador, na thread dele.
    rel_id = args.get('relatorio_id')
    url = str(args.get('url') or '').strip()
    rotulo = ''
    if rel_id:
        try:
            rel_id = int(rel_id)
        except (TypeError, ValueError):
            return 'relatorio_id inválido.'
        rel = next((r for r in _relatorios_visiveis(user) if r.pk == rel_id), None)
        if not rel:
            return 'Relatório não encontrado entre os que você pode ver no /power-bi/ (veja powerbi_relatorios).'
        url = rel.embed_url
        rotulo = f'Relatório: {rel.name} (id {rel.pk})\n'
        if _tipo_link(url) == 'powerbi_exige_login':
            return (f'"{rel.name}" é um Power BI que exige login Microsoft (link /groups/...): o assistente não '
                    f'consegue ler. Abra pelo portal em /power-bi/{rel.pk}/ e veja direto na tela.')
    if not url:
        return 'Informe relatorio_id (de powerbi_relatorios) ou url.'
    if '://' not in url and not url.startswith('/'):
        url = 'https://' + url
    if url.startswith('/'):
        return f'Isto é um caminho do portal: use ler_pagina(caminho="{url}").'
    motivo = checar_endereco(url)
    if motivo:
        return f'Não abro este endereço: {motivo}.'
    if not rel_id and _bi_proibido(user, url):
        return 'Este link é de um relatório do /power-bi/ que não está liberado para você.'

    s = _sessao()
    return s.rodar(_abrir_no_navegador, s, url, rotulo)


def _abrir_no_navegador(s, url, rotulo):
    s.elementos = {}
    s.bloqueios = []
    try:
        s.page.goto(url, wait_until='domcontentloaded')
    except Exception as exc:  # noqa: BLE001
        # Redirecionamento conferido pela rota vira uma navegação nova, que "interrompe" o goto.
        if 'interrupted by another navigation' not in str(exc) and not s.bloqueios:
            raise
        s.page.wait_for_timeout(500)
    _esperar(s.page, maximo=30.0)
    if s.bloqueios and (s.page.url or '').startswith(('chrome-error:', 'about:')):
        return 'Não abri: ' + s.bloqueios[-1] + '.'
    return rotulo + _ler(s)


@_no_navegador
def _ler_ferramenta(s, args):
    return _ler(s, args.get('parte') or 1, str(args.get('busca') or ''))


@_no_navegador
def _clicar(s, args):
    modo = str(args.get('modo') or 'clicar')
    if args.get('numero') in (None, '') and args.get('x') is not None and args.get('y') is not None:
        try:
            x, y = float(args['x']), float(args['y'])
        except (TypeError, ValueError):
            return 'x/y inválidos.'
        if not (0 <= x <= VIEWPORT['width'] and 0 <= y <= VIEWPORT['height']):
            return f'x/y fora da tela ({VIEWPORT["width"]}x{VIEWPORT["height"]}).'
        if modo == 'mouse':
            s.page.mouse.move(x, y)
        else:
            s.page.mouse.click(x, y, click_count=2 if modo == 'duplo' else 1)
        return _depois_da_acao(s, f'Cliquei na posição ({int(x)},{int(y)}).')

    loc = _elemento(s, args.get('numero'))
    info = loc.evaluate(_JS_INFO)
    if info['form_com_senha'] and (info['type'] == 'submit' or info['tag'] == 'button'):
        raise Recusado('Isto envia um formulário de login: o assistente não entra em contas.')
    if BOTAO_PROIBIDO.search(info['texto'] or '') or (info['type'] == 'submit' and info['tag'] == 'input'):
        raise Recusado(f'Não clico em "{info["texto"] or "enviar"}": o assistente só lê e filtra, '
                       'não envia nem altera nada em outros sites.')
    if modo == 'mouse':
        loc.hover()
        s.page.wait_for_timeout(900)
        return _depois_da_acao(s, 'Passei o mouse (dicas de ferramenta aparecem no texto/print).')
    if modo == 'duplo':
        loc.dblclick()
    elif modo == 'ctrl':
        loc.click(modifiers=['ControlOrMeta'])
    else:
        loc.click()
    return _depois_da_acao(s, f'Cliquei em [{args.get("numero")}] "{info["texto"]}".')


@_no_navegador
def _digitar(s, args):
    loc = _elemento(s, args.get('numero'))
    info = loc.evaluate(_JS_INFO)
    if info['type'] == 'password' or CAMPO_SENSIVEL.search(info['attrs'] or '') or info['form_com_senha']:
        raise Recusado('Este campo é de senha/credencial (ou de um formulário de login): o assistente não '
                       'digita nele.')
    texto = str(args.get('texto') or '')[:200]
    if info['tag'] == 'select':
        try:
            loc.select_option(label=texto)
        except Exception:  # noqa: BLE001
            loc.select_option(value=texto)
        return _depois_da_acao(s, f'Selecionei "{texto}".')
    loc.fill(texto)
    if args.get('enter'):
        loc.press('Enter')
    return _depois_da_acao(s, f'Digitei "{texto}"' + (' e apertei Enter.' if args.get('enter') else '.'))


@_no_navegador
def _tecla(s, args):
    tecla = str(args.get('tecla') or '').strip()
    if tecla not in TECLAS_PERMITIDAS:
        return 'Tecla não permitida. Use uma de: ' + ', '.join(sorted(TECLAS_PERMITIDAS)) + '.'
    s.page.keyboard.press(tecla)
    return _depois_da_acao(s, f'Apertei {tecla}.')


@_no_navegador
def _rolar(s, args):
    direcao = str(args.get('direcao') or 'baixo')
    passo = {'baixo': (0, 700), 'cima': (0, -700), 'direita': (700, 0), 'esquerda': (-700, 0)}.get(direcao, (0, 700))
    if args.get('numero') not in (None, ''):
        loc = _elemento(s, args.get('numero'))
        loc.hover()          # a rolagem vai para dentro do elemento (tabela do Power BI, lista)
    else:
        s.page.mouse.move(VIEWPORT['width'] / 2, VIEWPORT['height'] / 2)
    s.page.mouse.wheel(*passo)
    s.page.wait_for_timeout(900)
    return _depois_da_acao(s, f'Rolei para {direcao}.')


@_no_navegador
def _esperar_ferramenta(s, args):
    try:
        seg = min(max(float(args.get('segundos') or 3), 0.5), 15)
    except (TypeError, ValueError):
        seg = 3
    s.page.wait_for_timeout(int(seg * 1000))
    return _depois_da_acao(s, f'Esperei {seg:g} s.')


@_no_navegador
def _voltar(s, args):
    resp = None
    try:
        resp = s.page.go_back(wait_until='domcontentloaded')
    except Exception:  # noqa: BLE001
        resp = None
    if resp is None and s.paginas:
        # Aba aberta por link "nova guia": fecha e volta para a anterior.
        _silencioso(s.page.close)
        s.page = s.paginas.pop()
        return _depois_da_acao(s, 'Fechei a aba nova e voltei para a anterior.')
    if resp is None:
        return 'Não há página anterior.'
    return _depois_da_acao(s, 'Voltei uma página.')


_JS_PAGINAS = r"""
() => Array.from(document.querySelectorAll('li.animated, [role=tab]'))
  .filter(e => e.getBoundingClientRect().height > 0)
  .map(e => (e.innerText || e.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim())
"""


@_no_navegador
def _powerbi_pagina(s, args):
    page = s.page
    if _host_de(page.url) != 'app.powerbi.com':
        return 'A página aberta não é um Power BI. Abra um relatório com navegador_abrir.'
    alvo = str(args.get('pagina') or '').strip()
    rotulos = {'proxima': ('Próxima Página', 'Next Page'), 'anterior': ('Página Anterior', 'Previous Page')}
    if _normaliza(alvo) in ('proxima', 'anterior'):
        for nome in rotulos[_normaliza(alvo)]:
            botao = page.locator(f'button[aria-label="{nome}"]').first
            if botao.count():
                botao.click()
                return _depois_da_acao(s, f'Fui para a página {_normaliza(alvo)}.')
        return 'Este relatório não tem botões de página.'

    # Lista de páginas: o "1 de 49" no rodapé abre o menu; relatórios com abas usam role=tab.
    menu = page.locator('a.middleText').first
    abriu_menu = False
    if menu.count():
        menu.click()
        page.wait_for_timeout(700)
        abriu_menu = True
    nomes = page.evaluate(_JS_PAGINAS)
    nomes = [n for n in nomes if n and n not in ('Mostrar os atalhos de teclado', 'Pular para o conteúdo principal')
             and not n.startswith('Mostrar dicas')]
    if not nomes:
        if abriu_menu:
            page.keyboard.press('Escape')
        return 'Não achei a lista de páginas deste relatório (pode ter só uma página).'
    if not alvo:
        if abriu_menu:
            page.keyboard.press('Escape')
        return 'Páginas do relatório:\n' + '\n'.join(f'{i}. {n}' for i, n in enumerate(nomes, 1))

    escolhido = None
    if alvo.isdigit() and 1 <= int(alvo) <= len(nomes):
        escolhido = int(alvo) - 1
    else:
        a = _normaliza(alvo)
        exatos = [i for i, n in enumerate(nomes) if _normaliza(n) == a]
        parecidos = [i for i, n in enumerate(nomes) if a in _normaliza(n)]
        if exatos:
            escolhido = exatos[0]
        elif len(parecidos) == 1:
            escolhido = parecidos[0]
        elif parecidos:
            if abriu_menu:
                page.keyboard.press('Escape')
            return 'Mais de uma página parecida: ' + '; '.join(nomes[i] for i in parecidos[:15]) + '. Diga qual.'
    if escolhido is None:
        if abriu_menu:
            page.keyboard.press('Escape')
        return 'Página não encontrada. Páginas: ' + '; '.join(nomes[:60])
    page.locator('li.animated, [role=tab]').filter(has_text=nomes[escolhido]).first.click()
    return _depois_da_acao(s, f'Página do relatório: {nomes[escolhido]}.')


@_no_navegador
def _print(s, args):
    if s.page is None or s.page.url in ('', 'about:blank'):
        return 'Nenhuma página aberta. Use navegador_abrir primeiro.'
    if args.get('numero') not in (None, ''):
        dados = _elemento(s, args.get('numero')).screenshot(type='jpeg', quality=QUALIDADE_PRINT + 15)
        legenda = f'Print do elemento [{args.get("numero")}]'
    else:
        dados = s.page.screenshot(type='jpeg', quality=QUALIDADE_PRINT, full_page=False)
        legenda = f'Print da tela ({VIEWPORT["width"]}x{VIEWPORT["height"]}) de {s.page.url[:200]}'
    return [
        {'type': 'text', 'text': legenda + '. As posições @x,y da leitura batem com este print.'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                     'data': base64.b64encode(dados).decode('ascii')}},
    ]


_NUMERO = {'type': 'integer', 'description': 'Número do elemento na última leitura ([N]).'}

TOOLS = {
    'powerbi_relatorios': {
        'fn': _powerbi_relatorios,
        'description': ('Lista os relatórios/BIs do /power-bi/ que o usuário pode ver (id, nome, descrição, tipo de '
                        'link e se o assistente consegue abrir). Comece por aqui em perguntas sobre BI/dashboards.'),
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'navegador_abrir': {
        'fn': _abrir,
        'description': (
            'Abre no navegador do assistente um relatório do /power-bi/ (relatorio_id) ou qualquer link público '
            'http(s) e devolve a leitura: texto, números dos visuais do Power BI, segmentações e a lista numerada '
            'de elementos clicáveis. Navegador anônimo: páginas logadas do portal → use ler_pagina; Power BI com '
            'login Microsoft não abre. A aba continua aberta durante esta pergunta (filtros clicados valem).'),
        'input_schema': {'type': 'object', 'properties': {
            'relatorio_id': {'type': 'integer', 'description': 'id de powerbi_relatorios.'},
            'url': {'type': 'string', 'description': 'Link http(s) público, se não for um relatório da lista.'}},
            'required': []},
    },
    'navegador_ler': {
        'fn': _ler_ferramenta,
        'description': ('Lê de novo a página aberta (texto, visuais, filtros e elementos numerados). Use busca para '
                        'achar um elemento/linha (ex.: uma loja na lista da segmentação) e parte para páginas longas.'),
        'input_schema': {'type': 'object', 'properties': {
            'parte': {'type': 'integer'},
            'busca': {'type': 'string', 'description': 'Filtra elementos e linhas que contêm este termo.'}},
            'required': []},
    },
    'navegador_clicar': {
        'fn': _clicar,
        'description': (
            'Clica num elemento pelo número da leitura (ou em x,y do print). Serve para segmentações/filtros do '
            'Power BI (abra o combobox e clique na opção), abas, botões de navegação, paginação, ordenar coluna. '
            'modo: "clicar" (padrão), "ctrl" (seleção múltipla na segmentação), "duplo", "mouse" (só passa o '
            'mouse — mostra a dica com o valor do gráfico). Devolve a página relida. Não envia formulários.'),
        'input_schema': {'type': 'object', 'properties': {
            'numero': _NUMERO,
            'x': {'type': 'number'}, 'y': {'type': 'number'},
            'modo': {'type': 'string', 'enum': ['clicar', 'ctrl', 'duplo', 'mouse']}},
            'required': []},
    },
    'navegador_digitar': {
        'fn': _digitar,
        'description': ('Digita num campo (busca de segmentação, caixa de pesquisa) ou escolhe opção de um <select> '
                        'pelo texto. enter=true aperta Enter depois. Nunca em senha/credencial.'),
        'input_schema': {'type': 'object', 'properties': {
            'numero': _NUMERO, 'texto': {'type': 'string'}, 'enter': {'type': 'boolean'}},
            'required': ['numero', 'texto']},
    },
    'navegador_tecla': {
        'fn': _tecla,
        'description': 'Aperta uma tecla na página (Escape fecha menus, Enter, setas, PageDown, Tab).',
        'input_schema': {'type': 'object', 'properties': {'tecla': {'type': 'string'}}, 'required': ['tecla']},
    },
    'navegador_rolar': {
        'fn': _rolar,
        'description': ('Rola a página, ou dentro de um elemento (numero) — ex.: tabela do Power BI que só mostra '
                        'parte das linhas.'),
        'input_schema': {'type': 'object', 'properties': {
            'direcao': {'type': 'string', 'enum': ['baixo', 'cima', 'direita', 'esquerda']}, 'numero': _NUMERO},
            'required': []},
    },
    'navegador_esperar': {
        'fn': _esperar_ferramenta,
        'description': 'Espera alguns segundos (até 15) a página terminar de carregar e relê.',
        'input_schema': {'type': 'object', 'properties': {'segundos': {'type': 'number'}}, 'required': []},
    },
    'navegador_voltar': {
        'fn': _voltar,
        'description': 'Volta para a página anterior (ou fecha a aba aberta por um link de nova guia).',
        'input_schema': {'type': 'object', 'properties': {}, 'required': []},
    },
    'powerbi_pagina': {
        'fn': _powerbi_pagina,
        'description': ('Troca a página do relatório Power BI aberto: pelo nome (ou parte dele), pelo número, ou '
                        '"proxima"/"anterior". Sem "pagina", lista as páginas.'),
        'input_schema': {'type': 'object', 'properties': {'pagina': {'type': 'string'}}, 'required': []},
    },
    'navegador_print': {
        'fn': _print,
        'description': ('Tira um print da tela (ou de um elemento) e devolve como imagem para você ver gráficos, '
                        'botões-imagem e o que o texto não mostra.'),
        'input_schema': {'type': 'object', 'properties': {'numero': _NUMERO}, 'required': []},
    },
}
