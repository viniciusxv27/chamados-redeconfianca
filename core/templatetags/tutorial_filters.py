from django import template

register = template.Library()

@register.filter
def format_br(value, decimals=2):
    """Formata número no padrão brasileiro (vírgula como separador decimal)"""
    try:
        if value is None:
            value = 0
        # Formatar com o número de casas decimais especificado
        formatted = f"{float(value):.{decimals}f}"
        # Trocar ponto por vírgula
        return formatted.replace('.', ',')
    except (ValueError, TypeError):
        return value

@register.filter
def get_progress_for_user(tutorial, user):
    """Retorna o progresso do usuário para um tutorial específico"""
    try:
        return tutorial.get_progress_for_user(user)
    except:
        return None

@register.filter
def has_completed(tutorial, user):
    """Verifica se o usuário completou o tutorial"""
    progress = tutorial.get_progress_for_user(user)
    return progress and progress.completed_at is not None

@register.filter
def has_viewed(tutorial, user):
    """Verifica se o usuário visualizou o tutorial"""
    progress = tutorial.get_progress_for_user(user)
    return progress and progress.viewed_at is not None

@register.filter
def iq_decimal(value):
    """Converte IQ de percentual (80) para decimal (0,8) no formato brasileiro"""
    try:
        if value is None:
            value = 0
        decimal_value = float(value) / 100
        formatted = f"{decimal_value:.1f}"
        return formatted.replace('.', ',')
    except (ValueError, TypeError):
        return value