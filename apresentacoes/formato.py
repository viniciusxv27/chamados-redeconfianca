"""Formato do documento das apresentações — e o saneador que o garante.

Quadro fixo de 1920×1080 px. O documento inteiro (tema + slides + elementos)
mora num JSONField. O front desenha exatamente o que está aqui; a exportação
.pptx lê o mesmo dicionário.

Tudo o que chega do navegador ou da IA passa por `sanear_documento` antes de
ser gravado. O documento é aberto por outras pessoas (o SUPERADMIN vê todas as
apresentações), então HTML de texto, URLs de mídia, cores e números são
filtrados por lista de permissão: o que não está na lista some, sem erro.
"""
import copy
import re
import secrets
from html import escape
from html.parser import HTMLParser

FORMATO = 1
LARGURA, ALTURA = 1920, 1080

MAX_SLIDES = 300
MAX_ELEMENTOS = 400
MAX_HTML = 20000
MAX_NOTAS = 20000
MAX_TABELA = 30            # linhas e colunas

LAYOUTS = ('capa', 'secao', 'conteudo', 'quadro', 'encerramento', 'livre')
TIPOS_ELEMENTO = ('texto', 'imagem', 'forma', 'icone', 'video', 'tabela', 'apagar')
SLOTS = ('', 'rotulo', 'titulo', 'titulo_destaque', 'subtitulo', 'texto', 'botao', 'paginacao',
         'imagem', 'logo', 'decoracao', 'area_conteudo')
TRANSICOES = ('nenhuma', 'fade', 'empurrar-esquerda', 'empurrar-direita', 'empurrar-cima', 'empurrar-baixo',
              'revelar', 'zoom', 'cobrir')
ANIMACOES = ('nenhuma', 'aparecer', 'fade', 'subir', 'descer', 'entrar-esquerda', 'entrar-direita', 'zoom', 'revelar')
GATILHOS = ('auto', 'clique', 'junto')
FORMAS = ('retangulo', 'retangulo-arredondado', 'pilula', 'circulo', 'linha', 'seta', 'triangulo', 'estrela', 'hexagono')
AJUSTES_IMAGEM = ('cover', 'contain', 'fill')
FILTROS = ('', 'cinza', 'escurecer', 'desfocar')
SOMBRAS = ('', 'suave', 'forte', 'brilho')
ALINHAMENTOS = ('left', 'center', 'right', 'justify')
VERTICAIS = ('top', 'middle', 'bottom')
PAPEIS_TEXTO = ('titulo', 'texto')
ESTILOS_BORDA = ('solid', 'dashed', 'dotted')
CHAVES_COR_TEMA = ('fundo', 'superficie', 'primaria', 'secundaria', 'destaque', 'texto', 'texto_suave')

TEMA_PADRAO = {
    'fonte_titulo': 'Montserrat',
    'fonte_texto': 'Montserrat',
    'cores': {
        'fundo': '#0B0612', 'superficie': '#1A0F24', 'primaria': '#E23FCF', 'secundaria': '#8B3DFF',
        'destaque': '#FFD15C', 'texto': '#FFFFFF', 'texto_suave': '#D9C9E8',
    },
}

_RE_HEX = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$')
_RE_RGBA = re.compile(r'^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+|1\.0+)\s*)?\)$')
_RE_TEMA = re.compile(r'^tema:(%s)$' % '|'.join(CHAVES_COR_TEMA))
_RE_FONTE = re.compile(r"^[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 \-']{0,79}$")
_RE_MIDIA = re.compile(r'^/apresentacoes/midia/(\d{1,12})/$')
_RE_STATIC = re.compile(r'^static:apresentacoes/[A-Za-z0-9_\-./]{1,200}$')
_RE_ICONE = re.compile(r'^fa-(solid|regular|brands) fa-[a-z0-9\-]{1,60}$')
_RE_ID = re.compile(r'^[a-z]-[A-Za-z0-9]{4,24}$')
_RE_LINK_SLIDE = re.compile(r'^#slide-\d{1,4}$')
_RE_GRADIENTE = re.compile(
    r'^(linear|radial)-gradient\(\s*[#A-Za-z0-9 ,.%()\-]{3,400}\)$')


def novo_id(prefixo='e'):
    return f'{prefixo}-{secrets.token_hex(4)}'


# ---------------------------------------------------------------------------
# Valores simples
# ---------------------------------------------------------------------------
def cor(valor, padrao=''):
    if not isinstance(valor, str):
        return padrao
    v = valor.strip()
    if v in ('', 'transparent'):
        return v
    if _RE_HEX.match(v) or _RE_TEMA.match(v):
        return v
    if _RE_RGBA.match(v.replace(' ', '')) or _RE_RGBA.match(v):
        return v.replace(' ', '')
    return padrao


def numero(valor, padrao=0.0, minimo=None, maximo=None, casas=2):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return padrao
    if n != n or n in (float('inf'), float('-inf')):          # NaN/infinito
        return padrao
    if minimo is not None:
        n = max(minimo, n)
    if maximo is not None:
        n = min(maximo, n)
    n = round(n, casas)
    return int(n) if n == int(n) else n


def escolha(valor, opcoes, padrao=None):
    return valor if valor in opcoes else (opcoes[0] if padrao is None else padrao)


def texto(valor, maximo=500):
    if not isinstance(valor, str):
        return ''
    return valor.replace('\x00', '')[:maximo]


def booleano(valor):
    return bool(valor) if isinstance(valor, (bool, int)) else str(valor).lower() in ('true', '1', 'sim')


def fonte(valor):
    if not isinstance(valor, str):
        return ''
    v = valor.strip().strip('"\'')
    return v if _RE_FONTE.match(v) else ''


def src_midia(valor):
    """Só mídia servida pelo portal (com permissão) ou estático do módulo."""
    if not isinstance(valor, str):
        return ''
    v = valor.strip()
    return v if (_RE_MIDIA.match(v) or (_RE_STATIC.match(v) and '..' not in v)) else ''


def id_midia(src):
    m = _RE_MIDIA.match(src or '')
    return int(m.group(1)) if m else None


def gradiente(valor):
    if not isinstance(valor, str):
        return ''
    v = valor.strip()
    if not v or len(v) > 420 or not _RE_GRADIENTE.match(v):
        return ''
    # Nada de url(), expression() ou var(): só cores, ângulos e porcentagens.
    baixo = v.lower()
    if 'url' in baixo or 'expression' in baixo or 'var(' in baixo or ';' in v:
        return ''
    return v


def link(valor):
    if not isinstance(valor, str):
        return ''
    v = valor.strip()[:500]
    if _RE_LINK_SLIDE.match(v) or re.match(r'^https?://[^\s<>"\']+$', v):
        return v
    return ''


def borda(valor):
    valor = valor if isinstance(valor, dict) else {}
    return {
        'cor': cor(valor.get('cor')),
        'largura': numero(valor.get('largura'), 0, 0, 200),
        'estilo': escolha(valor.get('estilo'), ESTILOS_BORDA),
    }


# ---------------------------------------------------------------------------
# HTML restrito dos textos
# ---------------------------------------------------------------------------
TAGS_PERMITIDAS = {'b', 'strong', 'i', 'em', 'u', 's', 'br', 'p', 'div', 'span', 'ul', 'ol', 'li', 'a'}
TAGS_VAZIAS = {'br'}
TAGS_DESCARTAR_CONTEUDO = {'script', 'style', 'iframe', 'object', 'embed', 'template', 'svg', 'math', 'noscript',
                           'textarea', 'select', 'head', 'title'}
_ESTILOS_SPAN = {
    'color': lambda v: cor(v) if not v.startswith('tema:') else '',
    'background-color': lambda v: cor(v) if not v.startswith('tema:') else '',
    'font-size': lambda v: v if re.match(r'^\d{1,3}(\.\d{1,2})?px$', v) else '',
    'font-weight': lambda v: v if re.match(r'^(normal|bold|[1-9]00)$', v) else '',
    'font-style': lambda v: v if v in ('normal', 'italic') else '',
    'text-decoration': lambda v: v if v in ('none', 'underline', 'line-through', 'underline line-through') else '',
    'font-family': lambda v: (f"'{fonte(v)}'" if fonte(v) else ''),
}


def _estilo_span(bruto):
    partes = []
    for declaracao in (bruto or '').split(';'):
        if ':' not in declaracao:
            continue
        prop, valor = declaracao.split(':', 1)
        prop = prop.strip().lower()
        valor = valor.strip().strip('"').strip()
        if prop in _ESTILOS_SPAN:
            limpo = _ESTILOS_SPAN[prop](valor.strip("'") if prop == 'font-family' else valor)
            if limpo:
                partes.append(f'{prop}: {limpo}')
    return '; '.join(partes)


class _Saneador(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.saida = []
        self.pilha = []
        self.descartando = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in TAGS_DESCARTAR_CONTEUDO:
            self.descartando += 1
            return
        if self.descartando or tag not in TAGS_PERMITIDAS:
            return
        atributos = ''
        if tag == 'span':
            estilo = _estilo_span(dict(attrs).get('style'))
            if estilo:
                atributos = f' style="{escape(estilo, quote=True)}"'
        elif tag == 'a':
            href = link(dict(attrs).get('href') or '')
            if href:
                atributos = f' href="{escape(href, quote=True)}"'
        if tag in TAGS_VAZIAS:
            self.saida.append(f'<{tag}>')
            return
        self.saida.append(f'<{tag}{atributos}>')
        self.pilha.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag.lower() in TAGS_VAZIAS and not self.descartando:
            self.saida.append(f'<{tag.lower()}>')

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in TAGS_DESCARTAR_CONTEUDO:
            self.descartando = max(0, self.descartando - 1)
            return
        if self.descartando or tag not in TAGS_PERMITIDAS or tag in TAGS_VAZIAS:
            return
        if tag in self.pilha:
            # Fecha o que ficou aberto dentro dela: HTML mal formado não escapa da caixa.
            while self.pilha:
                aberta = self.pilha.pop()
                self.saida.append(f'</{aberta}>')
                if aberta == tag:
                    break

    def handle_data(self, data):
        if not self.descartando:
            self.saida.append(escape(data, quote=False))

    def resultado(self):
        while self.pilha:
            self.saida.append(f'</{self.pilha.pop()}>')
        return ''.join(self.saida)


def sanear_html(bruto):
    if not isinstance(bruto, str) or not bruto:
        return ''
    parser = _Saneador()
    try:
        parser.feed(bruto[:MAX_HTML * 2])
        parser.close()
    except Exception:                                           # noqa: BLE001 — HTML quebrado vira texto
        return escape(re.sub(r'<[^>]*>', '', bruto))[:MAX_HTML]
    return parser.resultado()[:MAX_HTML]


def texto_para_html(bruto):
    """Texto puro (da IA, por exemplo) em HTML seguro, com as quebras de linha."""
    return '<br>'.join(escape(linha, quote=False) for linha in str(bruto or '').split('\n'))


def html_para_texto(html):
    sem_tags = re.sub(r'<br\s*/?>|</(p|div|li)>', '\n', html or '', flags=re.I)
    sem_tags = re.sub(r'<[^>]+>', '', sem_tags)
    from html import unescape
    return re.sub(r'\n{3,}', '\n\n', unescape(sem_tags)).strip()


# ---------------------------------------------------------------------------
# Tema, slide e elementos
# ---------------------------------------------------------------------------
def sanear_tema(tema):
    tema = tema if isinstance(tema, dict) else {}
    cores_brutas = tema.get('cores') if isinstance(tema.get('cores'), dict) else {}
    cores = {}
    for chave in CHAVES_COR_TEMA:
        c = cor(cores_brutas.get(chave))
        cores[chave] = c if c and not c.startswith('tema:') else TEMA_PADRAO['cores'][chave]
    return {
        'fonte_titulo': fonte(tema.get('fonte_titulo')) or TEMA_PADRAO['fonte_titulo'],
        'fonte_texto': fonte(tema.get('fonte_texto')) or TEMA_PADRAO['fonte_texto'],
        'cores': cores,
    }


def _animacao(valor):
    valor = valor if isinstance(valor, dict) else {}
    return {
        'tipo': escolha(valor.get('tipo'), ANIMACOES),
        'duracao': numero(valor.get('duracao'), 600, 0, 20000, 0),
        'atraso': numero(valor.get('atraso'), 0, 0, 60000, 0),
        'gatilho': escolha(valor.get('gatilho'), GATILHOS),
    }


def _estilo_texto(valor):
    valor = valor if isinstance(valor, dict) else {}
    return {
        'fonte': fonte(valor.get('fonte')),
        'papel': escolha(valor.get('papel'), PAPEIS_TEXTO, 'texto'),
        'tamanho': numero(valor.get('tamanho'), 36, 4, 600),
        'peso': int(numero(valor.get('peso'), 400, 100, 900, 0) // 100 * 100) or 400,
        'italico': booleano(valor.get('italico', False)),
        'sublinhado': booleano(valor.get('sublinhado', False)),
        'tachado': booleano(valor.get('tachado', False)),
        'maiusculas': booleano(valor.get('maiusculas', False)),
        'cor': cor(valor.get('cor'), 'tema:texto') or 'tema:texto',
        'alinhamento': escolha(valor.get('alinhamento'), ALINHAMENTOS),
        'vertical': escolha(valor.get('vertical'), VERTICAIS),
        'entrelinha': numero(valor.get('entrelinha'), 1.15, 0.5, 4),
        'espacamento': numero(valor.get('espacamento'), 0, -50, 200),
        'fundo': cor(valor.get('fundo')),
        'borda': borda(valor.get('borda')),
        'raio': numero(valor.get('raio'), 0, 0, 2000),
        'preenchimento': numero(valor.get('preenchimento'), 0, 0, 400),
        'sombra': escolha(valor.get('sombra', ''), SOMBRAS, ''),
    }


def _tabela(el, valor):
    linhas_brutas = valor.get('linhas') if isinstance(valor.get('linhas'), list) else []
    linhas = []
    for linha in linhas_brutas[:MAX_TABELA]:
        if isinstance(linha, list):
            linhas.append([texto(c if isinstance(c, str) else ('' if c is None else str(c)), 500)
                           for c in linha[:MAX_TABELA]])
    largura = max((len(l) for l in linhas), default=0)
    el['linhas'] = [l + [''] * (largura - len(l)) for l in linhas] or [['']]
    el['cabecalho'] = booleano(valor.get('cabecalho', True))
    e = valor.get('estilo') if isinstance(valor.get('estilo'), dict) else {}
    el['estilo'] = {
        'fonte': fonte(e.get('fonte')),
        'tamanho': numero(e.get('tamanho'), 28, 6, 200),
        'cor': cor(e.get('cor'), 'tema:texto') or 'tema:texto',
        'fundo_cabecalho': cor(e.get('fundo_cabecalho'), 'tema:primaria'),
        'cor_cabecalho': cor(e.get('cor_cabecalho'), '#FFFFFF'),
        'fundo_linhas': cor(e.get('fundo_linhas'), ''),
        'fundo_alternado': cor(e.get('fundo_alternado'), ''),
        'borda': cor(e.get('borda'), ''),
        'alinhamento': escolha(e.get('alinhamento'), ALINHAMENTOS),
        'raio': numero(e.get('raio'), 0, 0, 200),
    }


def sanear_elemento(valor):
    if not isinstance(valor, dict):
        return None
    tipo = valor.get('tipo')
    if tipo not in TIPOS_ELEMENTO:
        return None
    ident = valor.get('id')
    el = {
        'id': ident if isinstance(ident, str) and _RE_ID.match(ident) else novo_id('e'),
        'tipo': tipo,
        'x': numero(valor.get('x'), 0, -20000, 20000),
        'y': numero(valor.get('y'), 0, -20000, 20000),
        'w': numero(valor.get('w'), 100, 1, 40000),
        'h': numero(valor.get('h'), 100, 1, 40000),
        'rotacao': numero(valor.get('rotacao'), 0, -3600, 3600),
        'opacidade': numero(valor.get('opacidade', 1), 1, 0, 1),
        'bloqueado': booleano(valor.get('bloqueado', False)),
        'slot': escolha(valor.get('slot', ''), SLOTS, ''),
        'animacao': _animacao(valor.get('animacao')),
        'link': link(valor.get('link')),
    }
    if tipo == 'texto':
        el['html'] = sanear_html(valor.get('html'))
        el['estilo'] = _estilo_texto(valor.get('estilo'))
        el['autoajuste'] = booleano(valor.get('autoajuste', False))
    elif tipo == 'imagem':
        el['src'] = src_midia(valor.get('src'))
        el['midia'] = id_midia(el['src'])
        el['ajuste'] = escolha(valor.get('ajuste'), AJUSTES_IMAGEM)
        foco = valor.get('foco') if isinstance(valor.get('foco'), dict) else {}
        el['foco'] = {'x': numero(foco.get('x'), 0.5, 0, 1, 3), 'y': numero(foco.get('y'), 0.5, 0, 1, 3)}
        el['raio'] = numero(valor.get('raio'), 0, 0, 5000)
        el['borda'] = borda(valor.get('borda'))
        el['sombra'] = escolha(valor.get('sombra', ''), SOMBRAS, '')
        el['filtro'] = escolha(valor.get('filtro', ''), FILTROS, '')
        el['alt'] = texto(valor.get('alt'), 300)
    elif tipo == 'forma':
        el['forma'] = escolha(valor.get('forma'), FORMAS)
        el['preenchimento'] = cor(valor.get('preenchimento'))
        el['gradiente'] = gradiente(valor.get('gradiente'))
        el['borda'] = borda(valor.get('borda'))
        el['raio'] = numero(valor.get('raio'), 0, 0, 5000)
        el['sombra'] = escolha(valor.get('sombra', ''), SOMBRAS, '')
    elif tipo == 'icone':
        icone = valor.get('icone') if isinstance(valor.get('icone'), str) else ''
        el['icone'] = icone if _RE_ICONE.match(icone.strip()) else 'fa-solid fa-star'
        el['cor'] = cor(valor.get('cor'), 'tema:destaque') or 'tema:destaque'
        el['fundo'] = cor(valor.get('fundo'))
        el['raio'] = numero(valor.get('raio'), 0, 0, 5000)
    elif tipo == 'video':
        el['src'] = src_midia(valor.get('src'))
        el['midia'] = id_midia(el['src'])
        el['poster'] = src_midia(valor.get('poster'))
        el['autoplay'] = booleano(valor.get('autoplay', True))
        el['loop'] = booleano(valor.get('loop', False))
        el['mudo'] = booleano(valor.get('mudo', True))
        el['controles'] = booleano(valor.get('controles', False))
        el['raio'] = numero(valor.get('raio'), 0, 0, 5000)
        el['ajuste'] = escolha(valor.get('ajuste'), AJUSTES_IMAGEM)
    elif tipo == 'tabela':
        _tabela(el, valor)
    return el


def sanear_slide(valor):
    if not isinstance(valor, dict):
        return None
    ident = valor.get('id')
    fundo = valor.get('fundo') if isinstance(valor.get('fundo'), dict) else {}
    transicao = valor.get('transicao') if isinstance(valor.get('transicao'), dict) else {}
    narracao = valor.get('narracao') if isinstance(valor.get('narracao'), dict) else None
    if narracao:
        url = src_midia(narracao.get('url'))
        narracao = ({'midia': id_midia(url), 'url': url, 'duracao': numero(narracao.get('duracao'), 0, 0, 3600)}
                    if url else None)
    elementos = []
    for bruto in (valor.get('elementos') if isinstance(valor.get('elementos'), list) else [])[:MAX_ELEMENTOS]:
        el = sanear_elemento(bruto)
        if el:
            elementos.append(el)
    return {
        'id': ident if isinstance(ident, str) and _RE_ID.match(ident) else novo_id('s'),
        'nome': texto(valor.get('nome'), 120),
        'layout': escolha(valor.get('layout'), LAYOUTS, 'livre'),
        'fundo': {
            'cor': cor(fundo.get('cor'), 'tema:fundo') or 'tema:fundo',
            'gradiente': gradiente(fundo.get('gradiente')),
            'imagem': src_midia(fundo.get('imagem')),
            'ajuste': escolha(fundo.get('ajuste'), ('cover', 'contain')),
        },
        'transicao': {
            'tipo': escolha(transicao.get('tipo'), TRANSICOES, 'fade'),
            'duracao': numero(transicao.get('duracao'), 700, 0, 10000, 0),
        },
        'notas': texto(valor.get('notas'), MAX_NOTAS),
        'narracao': narracao,
        'oculto': booleano(valor.get('oculto', False)),
        'elementos': elementos,
    }


def sanear_documento(valor):
    """Documento limpo, sempre com a estrutura completa. Nunca levanta."""
    valor = valor if isinstance(valor, dict) else {}
    slides = []
    vistos = set()
    for bruto in (valor.get('slides') if isinstance(valor.get('slides'), list) else [])[:MAX_SLIDES]:
        slide = sanear_slide(bruto)
        if not slide:
            continue
        if slide['id'] in vistos:                     # id repetido (slide colado) ganha outro
            slide['id'] = novo_id('s')
        vistos.add(slide['id'])
        ids_el = set()
        for el in slide['elementos']:
            if el['id'] in ids_el:
                el['id'] = novo_id('e')
            ids_el.add(el['id'])
        slides.append(slide)
    return {
        'formato': FORMATO,
        'largura': LARGURA,
        'altura': ALTURA,
        'tema': sanear_tema(valor.get('tema')),
        'slides': slides,
    }


def documento_vazio(tema=None):
    return sanear_documento({'tema': copy.deepcopy(tema or TEMA_PADRAO), 'slides': []})


def midias_usadas(documento):
    """Ids das mídias referenciadas (para conferir permissão e para a biblioteca)."""
    ids = set()
    for slide in (documento or {}).get('slides') or []:
        for src in (slide.get('fundo', {}).get('imagem'), (slide.get('narracao') or {}).get('url')):
            if id_midia(src):
                ids.add(id_midia(src))
        for el in slide.get('elementos') or []:
            for chave in ('src', 'poster'):
                if id_midia(el.get(chave)):
                    ids.add(id_midia(el.get(chave)))
    return ids
