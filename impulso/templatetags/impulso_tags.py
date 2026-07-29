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


@register.simple_tag
def blocos_de(dados):
    """Blocos (CONFIAR/CONECTAR/INOVAR) formatados para as barras."""
    from ..scoring import blocos_resumo
    try:
        return blocos_resumo(dados)
    except Exception:
        return []


@register.filter
def pct_de(valor, maximo):
    """Percentual seguro para larguras de barra."""
    try:
        maximo = float(maximo)
        if maximo <= 0:
            return 0
        return round(float(valor) / maximo * 100, 1)
    except (TypeError, ValueError):
        return 0
