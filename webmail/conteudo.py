"""O corpo do e-mail: decodificar, limpar e mostrar sem riscos.

E-mail HTML é conteúdo de terceiros. Ele é mostrado num ``<iframe sandbox>``
**sem** ``allow-scripts`` (nenhum script roda, nem atributo ``on*``), com uma
CSP própria no documento e, antes disso, passa por esta limpeza — três camadas,
porque cada uma sozinha já falhou em algum cliente de e-mail.

Imagens remotas ficam bloqueadas até a pessoa pedir: elas são o jeito comum de
avisar ao remetente que o e-mail foi aberto (pixel de rastreio). As imagens que
vêm dentro do próprio e-mail (``cid:``) entram como ``data:``, sem ida à rede.
"""
import base64
import html
import quopri
import re
from email.header import decode_header, make_header
from email.utils import getaddresses

from lxml import etree
from lxml import html as lhtml

# ── Decodificação ─────────────────────────────────────────────────────────

def decodificar_parte(bruto, encoding):
    bruto = bruto or b''
    encoding = (encoding or '').lower()
    try:
        if encoding == 'base64':
            return base64.b64decode(re.sub(rb'[^A-Za-z0-9+/=]', b'', bruto) + b'===')
        if encoding == 'quoted-printable':
            return quopri.decodestring(bruto)
    except (ValueError, TypeError):
        return bruto
    return bruto


def texto_da_parte(bruto, charset):
    for tentativa in (charset, 'utf-8', 'windows-1252', 'latin-1'):
        if not tentativa:
            continue
        try:
            return bruto.decode(tentativa)
        except (LookupError, UnicodeDecodeError):
            continue
    return bruto.decode('utf-8', 'replace')


def cabecalho(valor):
    """'=?UTF-8?B?...?=' → texto."""
    if not valor:
        return ''
    try:
        return str(make_header(decode_header(valor))).strip()
    except (ValueError, LookupError, UnicodeDecodeError):
        return str(valor).strip()


def enderecos(valor):
    """'Fulano <a@b>, c@d' → [{'nome': 'Fulano', 'email': 'a@b'}, ...]."""
    if not valor:
        return []
    saida = []
    for nome, email in getaddresses([cabecalho(valor)]):
        email = (email or '').strip()
        if not email and not nome:
            continue
        saida.append({'nome': (nome or '').strip().strip('"'), 'email': email})
    return saida


# ── Limpeza do HTML ───────────────────────────────────────────────────────

_PROIBIDAS = {'script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet', 'form',
              'input', 'button', 'select', 'textarea', 'link', 'meta', 'base', 'noscript',
              'svg', 'math', 'template', 'portal', 'audio', 'video', 'source', 'track'}
_URL_ATTRS = ('href', 'src', 'background', 'action', 'poster', 'srcset', 'xlink:href')
_PERIGOSO = re.compile(r'^\s*(javascript|vbscript|data:text/html|file):', re.I)
_CSS_PERIGOSO = re.compile(r'expression\s*\(|javascript:|behavior\s*:|-moz-binding|@import', re.I)
_CSS_URL_REMOTA = re.compile(r'url\(\s*([\'"]?)\s*(https?:)?//', re.I)


def limpar_html(fonte, cids=None, imagens=False):
    """Devolve ``(html_limpo_do_body, tinha_imagem_remota)``."""
    cids = cids or {}
    if not (fonte or '').strip():
        return '', False
    try:
        doc = lhtml.document_fromstring(fonte)
    except (etree.ParserError, ValueError):
        return '<pre>' + html.escape(fonte) + '</pre>', False

    remota = False
    for el in list(doc.iter()):
        if not isinstance(el.tag, str):           # comentário, instrução
            if el.getparent() is not None:
                el.drop_tree()                    # drop_tree guarda o texto que vem depois
            continue
        tag = el.tag.lower().split('}')[-1]
        if tag in _PROIBIDAS:
            el.drop_tree()
            continue
        if tag == 'style':
            texto = el.text or ''
            if _CSS_PERIGOSO.search(texto):
                texto = _CSS_PERIGOSO.sub('', texto)
            if _CSS_URL_REMOTA.search(texto):
                remota = True
            el.text = texto
            continue
        for attr in list(el.attrib):
            nome = attr.lower()
            valor = el.attrib.get(attr, '')
            if nome.startswith('on') or nome in ('formaction', 'srcdoc', 'ping'):
                del el.attrib[attr]
            elif nome in _URL_ATTRS and _PERIGOSO.match(valor or ''):
                del el.attrib[attr]
            elif nome == 'style':
                if _CSS_PERIGOSO.search(valor):
                    valor = _CSS_PERIGOSO.sub('', valor)
                if _CSS_URL_REMOTA.search(valor):
                    remota = True
                el.attrib[attr] = valor
        if tag == 'img':
            src = (el.get('src') or '').strip()
            if src.lower().startswith('cid:'):
                dado = cids.get(src[4:].strip('<>'))
                if dado:
                    el.set('src', dado)
                else:
                    el.attrib.pop('src', None)
            elif src.lower().startswith(('http:', 'https:', '//')):
                remota = True
                if not imagens:
                    el.set('data-wm-src', src)
                    el.attrib.pop('src', None)
                    el.attrib.pop('srcset', None)
        elif tag == 'a':
            href = el.get('href') or ''
            if href and not href.startswith('#'):
                el.set('target', '_blank')
                el.set('rel', 'noopener noreferrer')
        elif (el.get('background') or '').lower().startswith(('http', '//')):
            remota = True
            if not imagens:
                el.attrib.pop('background', None)

    corpo = doc.find('.//body')
    estilos = ''.join(lhtml.tostring(s, encoding='unicode') for s in doc.iter('style'))
    if corpo is None:
        miolo = lhtml.tostring(doc, encoding='unicode')
    else:
        miolo = (corpo.text and html.escape(corpo.text) or '') + ''.join(
            lhtml.tostring(filho, encoding='unicode') for filho in corpo)
        if estilos and '<style' not in miolo:
            miolo = estilos + miolo
    return miolo, remota


_URL = re.compile(r'(https?://[^\s<>"\']+[^\s<>"\'.,;:!?)\]])', re.I)


def texto_para_html(texto):
    """Texto puro com links clicáveis e as linhas citadas ("> ") recuadas."""
    linhas = []
    for linha in (texto or '').replace('\r\n', '\n').split('\n'):
        nivel = 0
        while linha.startswith('>'):
            nivel += 1
            linha = linha[1:].lstrip(' ') if linha[1:2] == ' ' else linha[1:]
        conteudo = _URL.sub(r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>', html.escape(linha))
        if nivel:
            conteudo = f'<span class="wm-citado" style="border-left:3px solid #d1d5db;padding-left:8px;color:#6b7280;margin-left:{(nivel - 1) * 10}px;display:inline-block">{conteudo or "&nbsp;"}</span>'
        linhas.append(conteudo)
    return '<div style="white-space:pre-wrap;font-family:inherit">' + '\n'.join(linhas) + '</div>'


def html_para_texto(fonte):
    """Versão em texto do que foi escrito (a parte text/plain do envio)."""
    if not (fonte or '').strip():
        return ''
    try:
        doc = lhtml.fragment_fromstring(fonte, create_parent='div')
    except (etree.ParserError, ValueError):
        return re.sub(r'<[^>]+>', '', fonte)
    for br in doc.iter('br'):
        br.tail = '\n' + (br.tail or '')
    for el in doc.iter('p', 'div', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'blockquote'):
        el.tail = '\n' + (el.tail or '')
    for a in doc.iter('a'):
        href = a.get('href') or ''
        if href.startswith('http') and href not in (a.text_content() or ''):
            a.tail = f' ({href})' + (a.tail or '')
    texto = doc.text_content()
    texto = re.sub(r'[ \t]+\n', '\n', texto)
    return re.sub(r'\n{3,}', '\n\n', texto).strip()


def documento_do_iframe(miolo, imagens=False):
    """O documento completo que vai no ``srcdoc`` do iframe."""
    img_src = "data: blob: https: http:" if imagens else "data: blob:"
    csp = (f"default-src 'none'; img-src {img_src}; style-src 'unsafe-inline'"
           + (" https: http:" if imagens else "") + "; font-src data:" + (" https:" if imagens else ""))
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<base target="_blank">'
        '<style>html,body{margin:0;padding:0;background:#fff}'
        'body{padding:4px 2px 12px;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;'
        'color:#1f2937;word-wrap:break-word;overflow-wrap:anywhere}'
        'img{max-width:100%;height:auto}table{max-width:100%}pre{white-space:pre-wrap}'
        'blockquote{margin:8px 0;padding-left:12px;border-left:3px solid #e5e7eb;color:#4b5563}'
        'a{color:#ea580c}img[data-wm-src]{display:inline-block;min-width:24px;min-height:24px;'
        'background:#f3f4f6;border-radius:4px}</style></head><body>'
        + miolo + '</body></html>'
    )


def limpar_assinatura(fonte):
    """A assinatura é HTML digitado pela própria pessoa, mas passa pela mesma limpeza."""
    miolo, _ = limpar_html(fonte or '', imagens=True)
    return miolo
