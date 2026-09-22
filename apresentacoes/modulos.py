"""Catálogo dos módulos do portal e o contexto que a IA usa para explicá-los.

O catálogo sai do próprio menu lateral, renderizado para quem está olhando:
todo módulo novo que entra no menu aparece aqui sem cadastro — é o que faz
"toda entrega nova ganhar a sua apresentação" funcionar sem lembrar de nada.

O contexto de um módulo junta o que o código já diz sobre ele: docstrings,
modelos e campos, telas (rotas), títulos das páginas e as frases dos testes
(`t('…')`), que descrevem o comportamento em português.
"""
import ast
import inspect
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

from django.apps import apps
from django.conf import settings
from django.core.cache import caches
from django.urls import URLPattern, URLResolver, get_resolver, resolve, reverse
from django.urls.exceptions import NoReverseMatch, Resolver404

logger = logging.getLogger(__name__)

IGNORAR_APPS = {'apresentacoes', 'admin', 'auth', 'contenttypes', 'sessions'}
IGNORAR_CAMINHOS = ('/', '/admin/', '/logout/', '/accounts/logout/')
PALAVRAS_SEM_CAPTURA = ('api', 'json', 'export', 'csv', 'pdf', 'xlsx', 'download', 'baixar', 'excluir', 'delete',
                        'remover', 'salvar', 'toggle', 'marcar', 'logout', 'webhook', 'callback', 'retorno',
                        'sincron', 'bater', 'enviar', 'upload', 'arquivo', 'imprimir', 'etiqueta', 'decidir',
                        'aprovacao', 'responder', 'status', 'progresso', 'ping', 'poll', 'stream', 'ws', 'oauth',
                        'conectar', 'desconectar', 'recebimento', 'concluir', 'duplicar', 'restaurar')
MAX_CONTEXTO = 45000
ROTULOS_GENERICOS = {'novo', 'nova', 'lista', 'listar', 'dashboard', 'painel', 'início', 'inicio', 'home',
                     'gerenciar', 'todos', 'todas', 'meus', 'minhas', 'configurações', 'admin', 'core'}
MAX_PAGINAS = 6


class _LeitorDeMenu(HTMLParser):
    """Lê os links do menu lateral: href, ícone e rótulo."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.itens = []
        self._atual = None
        self._capturando = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get('class') or '').split()
        if tag == 'a' and 'menu-item' in classes and (a.get('href') or '').startswith('/'):
            self._atual = {'href': a['href'], 'icone': '', 'rotulo': '', 'span': ''}
        elif self._atual is not None:
            if tag == 'i' and not self._atual['icone']:
                self._atual['icone'] = ' '.join(c for c in classes if c.startswith('fa') and c != 'fas') or ''
            elif tag == 'div' and 'tooltip-collapsed' in classes:
                self._capturando = 'rotulo'
            elif tag == 'span' and not self._atual['span']:
                self._capturando = 'span'

    def handle_endtag(self, tag):
        if tag in ('div', 'span'):
            self._capturando = None
        if tag == 'a' and self._atual is not None:
            rotulo = (self._atual['rotulo'] or self._atual['span']).strip()
            if rotulo:
                self.itens.append({'href': self._atual['href'], 'icone': self._atual['icone'], 'rotulo': rotulo})
            self._atual = None

    def handle_data(self, data):
        if self._atual is not None and self._capturando:
            self._atual[self._capturando] += data.strip() and (' ' + data.strip()) or ''


def _app_da_rota(caminho):
    try:
        encontrado = resolve(urlparse(caminho).path)
    except Resolver404:
        return None, None
    funcao = encontrado.func
    modulo = getattr(funcao, '__module__', '') or ''
    if hasattr(funcao, 'view_class'):
        modulo = funcao.view_class.__module__
    config = apps.get_containing_app_config(modulo)
    return (config.label if config else None), encontrado


def catalogo(request, usar_cache=True):
    """Módulos que a pessoa vê no menu: [{label, nome, icone, url, telas: [...]}]."""
    chave = f'apresentacoes:catalogo:{request.user.pk}'
    if usar_cache:
        guardado = caches['local'].get(chave)
        if guardado is not None:
            return guardado
    from django.template.loader import render_to_string

    try:
        html = render_to_string('base.html', {}, request=request)
    except Exception as exc:                                    # noqa: BLE001 — sem menu, catálogo pelos apps
        logger.warning('Menu não renderizou para o catálogo de módulos: %s', exc)
        html = ''
    leitor = _LeitorDeMenu()
    leitor.feed(html)
    modulos = {}
    for item in leitor.itens:
        caminho = urlparse(item['href']).path
        if caminho in IGNORAR_CAMINHOS:
            continue
        label, _ = _app_da_rota(caminho)
        if not label or label in IGNORAR_APPS:
            continue
        verbose = str(apps.get_app_config(label).verbose_name)
        modulo = modulos.setdefault(label, {
            'label': label, 'nome': item['rotulo'], 'icone': item['icone'] or 'fa-cube', 'url': caminho,
            'verbose': verbose, 'telas': []})
        # "Novo", "Dashboard"… não dizem que módulo é: vale o próximo rótulo que diga, ou o nome do app.
        if modulo['nome'].strip().lower() in ROTULOS_GENERICOS:
            if item['rotulo'].strip().lower() not in ROTULOS_GENERICOS:
                modulo['nome'], modulo['url'], modulo['icone'] = item['rotulo'], caminho, item['icone'] or modulo['icone']
            elif verbose.strip().lower() not in ROTULOS_GENERICOS | {label}:
                modulo['nome'] = verbose
        if all(t['url'] != caminho for t in modulo['telas']):
            modulo['telas'].append({'url': caminho, 'nome': item['rotulo']})
    resultado = sorted(modulos.values(), key=lambda m: m['nome'].lower())
    caches['local'].set(chave, resultado, 600)
    return resultado


# ---------------------------------------------------------------------------
# Contexto para a IA
# ---------------------------------------------------------------------------
def _doc_do_arquivo(caminho, limite=2500):
    try:
        arvore = ast.parse(Path(caminho).read_text(encoding='utf-8'))
    except (OSError, SyntaxError, ValueError):
        return ''
    return (ast.get_docstring(arvore) or '')[:limite]


def _modelos(config):
    linhas = []
    for modelo in config.get_models():
        meta = modelo._meta
        campos = []
        for campo in meta.get_fields():
            if not getattr(campo, 'concrete', False) or campo.auto_created:
                continue
            descricao = str(getattr(campo, 'verbose_name', campo.name))
            escolhas = getattr(campo, 'choices', None)
            if escolhas:
                descricao += ' (' + ', '.join(str(r) for _, r in list(escolhas)[:12]) + ')'
            campos.append(descricao)
        doc = (inspect.getdoc(modelo) or '').split('\n\n')[0][:300]
        linhas.append(f'- {meta.verbose_name}: ' + '; '.join(campos[:30]) + (f'. {doc}' if doc else ''))
    return '\n'.join(linhas)


def _padroes_do_app(label):
    """(rota completa, nome, função) das URLs cujas views são deste app."""
    encontrados = []

    def percorrer(padroes, prefixo='', namespace=None):
        for padrao in padroes:
            if isinstance(padrao, URLResolver):
                ns = padrao.namespace or namespace
                percorrer(padrao.url_patterns, prefixo + str(padrao.pattern), ns)
            elif isinstance(padrao, URLPattern):
                funcao = padrao.callback
                modulo = getattr(getattr(funcao, 'view_class', funcao), '__module__', '')
                config = apps.get_containing_app_config(modulo)
                if config and config.label == label:
                    encontrados.append((prefixo + str(padrao.pattern), padrao.name, namespace, funcao))
    percorrer(get_resolver().url_patterns)
    return encontrados


def _rotas(label):
    linhas = []
    for rota, nome, _, funcao in _padroes_do_app(label)[:80]:
        doc = (inspect.getdoc(getattr(funcao, 'view_class', funcao)) or '').split('\n')[0][:160]
        linhas.append(f'- /{rota} ({nome or "-"})' + (f': {doc}' if doc else ''))
    return '\n'.join(linhas)


_RE_TITULOS = re.compile(r'<(h1|h2|h3|label|button|th)[^>]*>(.*?)</\1>', re.S | re.I)


def _textos_das_telas(label):
    pasta = Path(settings.BASE_DIR) / 'templates' / label
    if not pasta.is_dir():
        return ''
    linhas = []
    for arquivo in sorted(pasta.glob('*.html'))[:30]:
        try:
            conteudo = arquivo.read_text(encoding='utf-8')
        except OSError:
            continue
        textos = []
        for _, bruto in _RE_TITULOS.findall(conteudo):
            limpo = re.sub(r'{%.*?%}|{{.*?}}|<[^>]+>', ' ', bruto, flags=re.S)
            limpo = re.sub(r'\s+', ' ', limpo).strip()
            if 2 < len(limpo) < 90 and limpo not in textos:
                textos.append(limpo)
        if textos:
            linhas.append(f'- {arquivo.name}: ' + ' | '.join(textos[:25]))
    return '\n'.join(linhas)


_RE_TESTE = re.compile(r"""\bt\(\s*(?:f?['"])(.{8,160}?)['"]""")


def _comportamentos(label):
    frases = []
    for arquivo in sorted(Path(settings.BASE_DIR).glob(f'teste_{label}*.py'))[:6]:
        try:
            conteudo = arquivo.read_text(encoding='utf-8')
        except OSError:
            continue
        for frase in _RE_TESTE.findall(conteudo):
            if '{' not in frase and frase not in frases:
                frases.append(frase)
    return '\n'.join(f'- {f}' for f in frases[:150])


def contexto_do_modulo(label, nome_no_menu=''):
    config = apps.get_app_config(label)
    pasta = Path(config.path)
    partes = [f'Módulo: {nome_no_menu or config.verbose_name} (app "{label}", {config.verbose_name}).']
    for arquivo in ('__init__.py', 'models.py', 'views.py', 'permissoes.py', 'permissions.py',
                    'servicos.py', 'services.py'):
        doc = _doc_do_arquivo(pasta / arquivo)
        if doc:
            partes.append(f'[{arquivo}] {doc}')
    blocos = (
        ('Modelos e campos', _modelos(config)),
        ('Telas e rotas', _rotas(label)),
        ('Títulos, rótulos e botões das telas', _textos_das_telas(label)),
        ('Comportamentos garantidos pelos testes', _comportamentos(label)),
    )
    for titulo, conteudo in blocos:
        if conteudo:
            partes.append(f'{titulo}:\n{conteudo}')
    return '\n\n'.join(partes)[:MAX_CONTEXTO]


def paginas_para_capturar(label, telas_do_menu=()):
    """Telas sem parâmetro e de leitura, começando pelas do menu. [{url, nome}]"""
    paginas = []
    vistos = set()
    for tela in telas_do_menu:
        if tela['url'] not in vistos:
            paginas.append({'url': tela['url'], 'nome': tela['nome']})
            vistos.add(tela['url'])
    for rota, nome, namespace, _ in _padroes_do_app(label):
        if not nome or '<' in rota or any(p in f'{rota} {nome}'.lower() for p in PALAVRAS_SEM_CAPTURA):
            continue
        try:
            url = reverse(f'{namespace}:{nome}' if namespace else nome)
        except NoReverseMatch:
            continue
        if url in vistos:
            continue
        vistos.add(url)
        paginas.append({'url': url, 'nome': nome.replace('_', ' ').capitalize()})
        if len(paginas) >= MAX_PAGINAS:
            break
    return paginas[:MAX_PAGINAS]
