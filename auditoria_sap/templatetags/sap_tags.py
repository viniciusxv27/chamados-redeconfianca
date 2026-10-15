from django import template

from auditoria_sap.permissions import nome_da_loja

register = template.Library()


@register.filter
def loja(pdv):
    """O nome da loja como a tela mostra: "L220" → "ITAIM PAULISTA"."""
    return nome_da_loja(pdv)
