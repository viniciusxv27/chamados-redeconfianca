"""Template tags/filters do módulo Impulso."""
from django import template

from ..utils import faixa_info, is_impulso_manager, is_impulso_member

register = template.Library()


@register.filter
def in_group(user, group_name):
    """Verifica se o usuário pertence a um CommunicationGroup pelo nome."""
    if not user or not user.is_authenticated:
        return False
    return user.communication_groups.filter(name__iexact=group_name).exists()


@register.filter
def impulso_gestor(user):
    return is_impulso_manager(user)


@register.filter
def impulso_membro(user):
    return is_impulso_member(user)


@register.simple_tag
def faixa_badge(nome):
    """Retorna o dict de estilo da faixa (label, cor, bg, text, icon)."""
    return faixa_info(nome)
