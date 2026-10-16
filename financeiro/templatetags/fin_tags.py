from django import template

register = template.Library()


@register.filter
def contem(colecao, item):
    """`set|contem:item` — None vira vazio."""
    return bool(colecao) and item in colecao
