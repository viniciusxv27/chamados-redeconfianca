"""Leitura das respostas do IMAP que o imaplib entrega cruas.

O imaplib devolve o FETCH como uma lista de pedaços: ``bytes`` para o texto da
linha e ``(linha_com_{n}, literal)`` quando o servidor manda um literal. Aqui
esses pedaços viram valores Python (listas, str, bytes, None), e daí saem:

- ``respostas_fetch``: um dict por mensagem (UID, FLAGS, BODYSTRUCTURE…);
- ``partes_da_estrutura``: o BODYSTRUCTURE achatado em partes com a seção IMAP
  ("1", "2.1"…), para baixar só o texto e o anexo pedido, nunca o e-mail todo;
- ``pastas_do_list``: a resposta do LIST com os atributos de uso especial;
- ``utf7_decodificar``/``utf7_codificar``: nome de pasta no UTF-7 modificado
  do IMAP (RFC 3501 §5.1.3) — "Itens Excluídos" chega como "Itens Exclu&AO0-dos".
"""
import base64
import re

_TOKEN = re.compile(
    rb'\s+'
    rb'|(?P<abre>\()'
    rb'|(?P<fecha>\))'
    rb'|"(?P<str>(?:\\.|[^"\\])*)"'
    rb'|\{(?P<lit>\d+)\+?\}$'
    rb'|(?P<atomo>[^\s()"\[\]{]+(?:\[[^\]]*\](?:<\d+(?:\.\d+)?>)?)?|\{)'
)


class _Literal(bytes):
    """Marca o conteúdo que veio como literal (não é átomo nem string)."""


def _tokens(pedacos):
    for pedaco in pedacos:
        if pedaco is None:
            continue
        if isinstance(pedaco, tuple):
            cabeca, literal = pedaco[0], pedaco[1]
            yield from _tokens_de(cabeca)
            yield ('lit', _Literal(literal or b''))
        else:
            yield from _tokens_de(pedaco)


def _tokens_de(texto):
    pos = 0
    while pos < len(texto):
        m = _TOKEN.match(texto, pos)
        if not m:
            pos += 1
            continue
        pos = m.end()
        if m.group('abre'):
            yield ('(', None)
        elif m.group('fecha'):
            yield (')', None)
        elif m.group('str') is not None:
            yield ('str', re.sub(rb'\\(.)', rb'\1', m.group('str')))
        elif m.group('lit') is not None:
            continue                       # o literal vem no próximo pedaço
        elif m.group('atomo') is not None:
            yield ('atomo', m.group('atomo'))


def _valor(tokens):
    tipo, valor = next(tokens)
    if tipo == '(':
        lista = []
        while True:
            try:
                item = _valor(tokens)
            except _FimDeLista:
                return lista
            lista.append(item)
    if tipo == ')':
        raise _FimDeLista
    if tipo == 'lit':
        return bytes(valor)
    if tipo == 'str':
        return valor.decode('utf-8', 'replace')
    texto = valor.decode('utf-8', 'replace')
    return None if texto.upper() == 'NIL' else texto


class _FimDeLista(Exception):
    pass


def valores(pedacos):
    """Todos os valores de uma resposta, na ordem."""
    tokens = iter(list(_tokens(pedacos)))
    saida = []
    while True:
        try:
            saida.append(_valor(tokens))
        except StopIteration:
            return saida
        except _FimDeLista:
            continue


def respostas_fetch(pedacos):
    """``[{'UID': '12', 'FLAGS': [...], 'BODY[HEADER]': b'...'}, ...]``.

    A resposta de cada mensagem é ``<seq> (CHAVE valor CHAVE valor …)``.
    """
    saida = []
    for item in valores(pedacos):
        if not isinstance(item, list):
            continue                       # o número de sequência
        dados = {}
        for i in range(0, len(item) - 1, 2):
            chave = item[i]
            if isinstance(chave, str):
                dados[chave.upper()] = item[i + 1]
        saida.append(dados)
    return saida


def chave_body(dados, prefixo='BODY['):
    """Valor do primeiro item que começa com BODY[ (o servidor ecoa a seção)."""
    for chave, valor in dados.items():
        if chave.startswith(prefixo):
            return valor
    return None


# ── BODYSTRUCTURE ─────────────────────────────────────────────────────────

def _params(lista):
    if not isinstance(lista, list):
        return {}
    return {str(lista[i]).lower(): (lista[i + 1] or '') for i in range(0, len(lista) - 1, 2)}


def _disposicao(item):
    """(tipo, params) da disposição numa parte simples, se houver."""
    for elemento in item[7:]:
        if (isinstance(elemento, list) and len(elemento) == 2 and isinstance(elemento[0], str)
                and elemento[0].lower() in ('attachment', 'inline')):
            return elemento[0].lower(), _params(elemento[1])
    return '', {}


def partes_da_estrutura(estrutura, secao=''):
    """Achata o BODYSTRUCTURE. Cada parte simples vira um dict com a seção."""
    if not isinstance(estrutura, list) or not estrutura:
        return []
    if isinstance(estrutura[0], list):     # multipart: filhos e depois o subtipo
        partes = []
        n = 0
        for filho in estrutura:
            if not isinstance(filho, list):
                break
            n += 1
            partes.extend(partes_da_estrutura(filho, f'{secao}.{n}' if secao else str(n)))
        subtipo = next((x for x in estrutura if isinstance(x, str)), '')
        for p in partes:
            p.setdefault('dentro_de', subtipo.lower())
        return partes
    tipo = (estrutura[0] or '').lower()
    subtipo = (estrutura[1] or '').lower() if len(estrutura) > 1 else ''
    params = _params(estrutura[2] if len(estrutura) > 2 else None)
    disp, disp_params = _disposicao(estrutura)
    nome = disp_params.get('filename') or params.get('name') or ''
    try:
        tamanho = int(estrutura[6]) if len(estrutura) > 6 else 0
    except (TypeError, ValueError):
        tamanho = 0
    return [{
        'secao': secao or '1',
        'tipo': tipo,
        'subtipo': subtipo,
        'mime': f'{tipo}/{subtipo}',
        'charset': params.get('charset', ''),
        'id': (estrutura[3] or '').strip('<>') if len(estrutura) > 3 and isinstance(estrutura[3], str) else '',
        'encoding': (estrutura[5] or '7bit').lower() if len(estrutura) > 5 and isinstance(estrutura[5], str) else '7bit',
        'tamanho': tamanho,
        'disposicao': disp,
        'nome': nome,
    }]


# ── LIST ──────────────────────────────────────────────────────────────────

_LIST = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>NIL|"(?:\\.|[^"])*")\s+(?P<nome>.*)$')


def pastas_do_list(pedacos):
    """``[{'nome': 'INBOX', 'flags': {'\\haschildren'}, 'delim': '/'}]``."""
    saida = []
    for pedaco in pedacos:
        if pedaco is None:
            continue
        if isinstance(pedaco, tuple):
            linha, nome_bruto = pedaco[0], pedaco[1]
        else:
            linha, nome_bruto = pedaco, None
        m = _LIST.match(linha.strip())
        if not m:
            continue
        if nome_bruto is None:
            nome_bruto = m.group('nome').strip()
            if nome_bruto.startswith(b'"') and nome_bruto.endswith(b'"'):
                nome_bruto = re.sub(rb'\\(.)', rb'\1', nome_bruto[1:-1])
        delim = m.group('delim')
        saida.append({
            'nome': nome_bruto.decode('ascii', 'replace'),
            'flags': {f.lower() for f in m.group('flags').decode('ascii', 'replace').split()},
            'delim': '' if delim == b'NIL' else delim.strip(b'"').decode('ascii', 'replace'),
        })
    return saida


# ── UTF-7 modificado (nomes de pasta) ─────────────────────────────────────

def utf7_decodificar(nome):
    def troca(m):
        miolo = m.group(1)
        if not miolo:
            return '&'
        miolo = miolo.replace(',', '/')
        miolo += '=' * (-len(miolo) % 4)
        return base64.b64decode(miolo).decode('utf-16-be', 'replace')
    return re.sub(r'&([^-]*)-', troca, nome)


def utf7_codificar(nome):
    saida, buffer = [], []

    def descarrega():
        if buffer:
            b64 = base64.b64encode(''.join(buffer).encode('utf-16-be')).decode().rstrip('=')
            saida.append('&' + b64.replace('/', ',') + '-')
            buffer.clear()

    for ch in nome:
        if 0x20 <= ord(ch) <= 0x7e:
            descarrega()
            saida.append('&-' if ch == '&' else ch)
        else:
            buffer.append(ch)
    descarrega()
    return ''.join(saida)
