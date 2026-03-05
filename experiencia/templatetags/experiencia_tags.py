from django import template

register = template.Library()


@register.filter
def in_group(user, group_name):
    """Verifica se o usuário pertence a um grupo Django pelo nome."""
    return user.groups.filter(name=group_name).exists()
