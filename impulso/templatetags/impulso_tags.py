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


@register.filter
def partes_da_ideia(texto):
    """Descrição da ideia em (título, corpo) para o cartão.

    Muita ideia chega com a primeira linha como título ("Matinal mensal") e o
    resto embaixo; outras são um parágrafo só. Primeira linha curta vira o título
    do cartão; texto corrido fica inteiro no corpo.
    """
    linhas = texto_limpo(texto).split('\n')
    while linhas and not linhas[0].strip():
        linhas.pop(0)
    if not linhas:
        return {'titulo': '', 'corpo': ''}
    primeira = linhas[0].strip()
    resto = '\n'.join(linhas[1:]).strip('\n')
    if len(primeira) <= 120 and (resto or len(primeira) <= 90):
        return {'titulo': primeira, 'corpo': resto}
    return {'titulo': '', 'corpo': '\n'.join(linhas).strip()}


@register.filter
def texto_limpo(texto):
    """Quebras de linha arrumadas para mostrar com ``whitespace-pre-line``.

    Texto colado do Word/WhatsApp chega com ``\r\n``, espaços sobrando e várias
    linhas em branco seguidas — na tela isso virava buracos no meio do cartão.
    """
    import re
    linhas = [linha.strip() for linha in str(texto or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(linhas)).strip()
