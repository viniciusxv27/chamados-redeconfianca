from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Obtém um item do dicionário usando uma chave"""
    if dictionary and hasattr(dictionary, 'get'):
        return dictionary.get(key, '')
    return ''