"""Texto formatado na descrição e nos comentários da meta (/impulso/metas/<id>/) — 25/11/2026.

Formatação leve, do jeito que se escreve no WhatsApp/Markdown, guardada como texto puro
(o texto antigo, sem marcação, continua aparecendo igual):

- ``**negrito**``, ``*itálico*`` ou ``_itálico_``, ``~~riscado~~``, `` `código` ``;
- linhas com ``- `` ou ``* `` viram lista; ``1. `` lista numerada; ``# `` / ``## `` título;
  ``> `` citação; linha em branco separa parágrafos;
- ``[texto](https://…)`` e endereços soltos (https://…) viram link (abre em outra aba).

O HTML que a pessoa digitar é escapado ANTES de qualquer marcação: a saída só tem as tags
geradas aqui. Link só com http/https. O mesmo desenho existe em JavaScript na pré-visualização
(``templates/impulso/_editor_formatacao.html``).
"""
import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

_LINK = re.compile(r'\[([^\]\n]{1,300})\]\((https?://[^\s)]{1,1000})\)')
_URL = re.compile(r'(?<![="\w/])(https?://[^\s<]{3,1000})')
_CODIGO = re.compile(r'`([^`\n]{1,500})`')
_NEGRITO = re.compile(r'\*\*(?=\S)(.+?)(?<=\S)\*\*')
_ITALICO = re.compile(r'(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])|(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])')
_RISCADO = re.compile(r'~~(?=\S)(.+?)(?<=\S)~~')
_ITEM = re.compile(r'^\s*[-*•]\s+(.*)$')
_NUMERO = re.compile(r'^\s*(\d{1,3})[.)]\s+(.*)$')
_TITULO = re.compile(r'^(#{1,3})\s+(.*)$')
_CITACAO = re.compile(r'^&gt;\s?(.*)$')


def _inline(texto):
    """Marcação dentro da linha. ``texto`` já vem escapado."""
    guardados = []

    def guardar(html):
        guardados.append(html)
        return f'\x00{len(guardados) - 1}\x00'

    texto = _CODIGO.sub(lambda m: guardar(f'<code>{m.group(1)}</code>'), texto)
    texto = _LINK.sub(lambda m: guardar(
        f'<a href="{m.group(2)}" target="_blank" rel="noopener noreferrer">{m.group(1)}</a>'), texto)

    def url_solta(m):
        url = m.group(1)
        fim = ''
        while url and url[-1] in '.,;:!?)':
            fim = url[-1] + fim
            url = url[:-1]
        return guardar(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>') + fim

    texto = _URL.sub(url_solta, texto)
    texto = _NEGRITO.sub(r'<strong>\1</strong>', texto)
    texto = _RISCADO.sub(r'<s>\1</s>', texto)
    texto = _ITALICO.sub(lambda m: f'<em>{m.group(1) or m.group(2)}</em>', texto)
    return re.sub(r'\x00(\d+)\x00', lambda m: guardados[int(m.group(1))], texto)


def para_html(texto):
    """O texto guardado → HTML seguro, já marcado como seguro para o template."""
    if not texto:
        return mark_safe('')
    linhas = escape(str(texto).replace('\r\n', '\n').replace('\r', '\n')).split('\n')
    saida, paragrafo, lista, tipo_lista = [], [], [], ''

    def fechar_paragrafo():
        if paragrafo:
            saida.append('<p>' + '<br>'.join(_inline(l) for l in paragrafo) + '</p>')
            paragrafo.clear()

    def fechar_lista():
        nonlocal tipo_lista
        if lista:
            saida.append(f'<{tipo_lista}>' + ''.join(f'<li>{_inline(i)}</li>' for i in lista) + f'</{tipo_lista}>')
            lista.clear()
        tipo_lista = ''

    for linha in linhas:
        item, numero = _ITEM.match(linha), _NUMERO.match(linha)
        titulo, citacao = _TITULO.match(linha), _CITACAO.match(linha)
        if item or numero:
            fechar_paragrafo()
            novo = 'ul' if item else 'ol'
            if tipo_lista and tipo_lista != novo:
                fechar_lista()
            tipo_lista = novo
            lista.append(item.group(1) if item else numero.group(2))
            continue
        fechar_lista()
        if not linha.strip():
            fechar_paragrafo()
        elif titulo:
            fechar_paragrafo()
            nivel = len(titulo.group(1))
            saida.append(f'<p class="fmt-titulo fmt-t{nivel}">{_inline(titulo.group(2))}</p>')
        elif citacao:
            fechar_paragrafo()
            saida.append(f'<blockquote>{_inline(citacao.group(1))}</blockquote>')
        else:
            paragrafo.append(linha)
    fechar_paragrafo()
    fechar_lista()
    return mark_safe(''.join(saida))


def sem_marcacao(texto):
    """O texto sem os sinais de formatação — para resumos de uma linha (cartões, listas)."""
    if not texto:
        return ''
    texto = _LINK.sub(r'\1', str(texto))
    texto = re.sub(r'(\*\*|~~|`)', '', texto)
    texto = re.sub(r'(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])', r'\1', texto)
    texto = re.sub(r'(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])', r'\1', texto)
    texto = re.sub(r'^\s*(#{1,3}|>|[-*•])\s+', '', texto, flags=re.M)
    return texto
