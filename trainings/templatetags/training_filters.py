from django import template

register = template.Library()

@register.filter
def div(value, divisor):
    """Divide um valor por outro"""
    try:
        return float(value) / float(divisor) if divisor else 0
    except (ValueError, ZeroDivisionError):
        return 0

@register.filter
def mul(value, multiplier):
    """Multiplica um valor por outro"""
    try:
        return float(value) * float(multiplier)
    except ValueError:
        return 0

@register.filter
def percentage_progress(watched, total):
    """Calcula a porcentagem de progresso"""
    try:
        if not total or total == 0:
            return 0
        return min((float(watched) / float(total)) * 100, 100)
    except (ValueError, ZeroDivisionError):
        return 0