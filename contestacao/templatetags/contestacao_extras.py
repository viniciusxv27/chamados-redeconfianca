from django import template

register = template.Library()


@register.filter
def digits_only(value):
    """Return only numeric characters from a value."""
    if value is None:
        return ''
    return ''.join(ch for ch in str(value) if ch.isdigit())
