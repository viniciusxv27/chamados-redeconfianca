from django import template

register = template.Library()

@register.filter
def get_tutorial_progress(tutorial_progress_dict, tutorial_id):
    """Retorna o progresso do tutorial para um usuário específico"""
    return tutorial_progress_dict.get(tutorial_id, None)

@register.filter  
def has_completed(progress):
    """Verifica se o progresso está concluído"""
    return progress and progress.completed_at is not None

@register.filter
def has_viewed(progress):
    """Verifica se o progresso foi visualizado"""
    return progress and progress.viewed_at is not None

@register.filter
def dict_get(dictionary, key):
    """Obtém valor de um dicionário usando uma chave"""
    if not dictionary or not isinstance(dictionary, dict):
        return None
    return dictionary.get(key)