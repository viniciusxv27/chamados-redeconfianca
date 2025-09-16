from django import template

register = template.Library()

@register.filter
def mul(value, arg):
    """Multiplica dois números"""
    try:
        return int(value) * int(arg)
    except (ValueError, TypeError):
        return 0

@register.filter  
def range_filter(value):
    """Cria uma range para usar no template"""
    try:
        return range(int(value))
    except (ValueError, TypeError):
        return range(0)