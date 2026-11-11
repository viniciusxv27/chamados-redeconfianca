"""Números no jeito brasileiro para a tela de metas (milhar com ponto, decimal com vírgula)."""
from django import template

register = template.Library()


def _br(valor, casas):
    texto = '{:,.{}f}'.format(float(valor), casas)
    return texto.replace(',', '§').replace('.', ',').replace('§', '.')


@register.filter
def num(valor, casas=0):
    if valor is None or valor == '':
        return '—'
    try:
        return _br(valor, int(casas))
    except (TypeError, ValueError):
        return '—'


@register.filter
def campo(valor):
    """Valor para dentro do <input>: sem milhar, vírgula decimal só se houver centavos."""
    if valor is None:
        return ''
    v = float(valor)
    return _br(v, 0 if v == int(v) else 2)


@register.filter
def pct(valor):
    if valor is None:
        return '—'
    return '{}{}%'.format('+' if valor > 0 else '', _br(valor * 100, 1))


@register.filter
def tom(valor):
    if valor is None:
        return 'vazio'
    if valor > 0.0005:
        return 'sobe'
    if valor < -0.0005:
        return 'desce'
    return 'neutro'


@register.filter
def dado(valor):
    """Para data-*: número cru com ponto, vazio quando não há."""
    if valor is None:
        return ''
    return repr(float(valor))


@register.filter
def item(lista, i):
    try:
        return lista[int(i)]
    except (IndexError, TypeError, ValueError, KeyError):
        return None


@register.filter
def chave(dicionario, nome):
    try:
        return dicionario.get(nome)
    except AttributeError:
        return None
