from django import template

register = template.Library()

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