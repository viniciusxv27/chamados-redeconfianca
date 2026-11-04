"""A barra de abas de Trilhas e Treinamentos, igual nas telas dos dois apps."""
from django import template

from ..modulo import abas_de, pode_gerir_trilhas

register = template.Library()


@register.inclusion_tag('knowledge_trails/_abas_modulo.html', takes_context=True)
def abas_trilhas_treinamentos(context, ativa):
    """Desenha as abas que a pessoa vê; ``ativa`` é a chave da aba desta tela."""
    return {'abas': abas_de(context.get('user')), 'ativa': ativa}


@register.filter
def gere_trilhas(user):
    """Para o menu e a busca do portal, sem repetir a regra no template."""
    return pode_gerir_trilhas(user)
