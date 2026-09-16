"""Tag do cartão da Rotina Gerencial na home: `{% cartao_rotina_home as cartao %}`."""
from django import template

from rotina.home import cartao_seguro

register = template.Library()


@register.simple_tag(takes_context=True)
def cartao_rotina_home(context):
    """HTML do cartão de hoje, ou '' para quem não tem rotina liberada (ou se algo falhar).

    Confia no `rotina_liberada` do context processor: quem não tem rotina não
    custa nenhuma consulta a mais.
    """
    if not context.get('rotina_liberada'):
        return ''
    request = context.get('request')
    user = getattr(request, 'user', None) or context.get('user')
    if not (user and user.is_authenticated):
        return ''
    return cartao_seguro(user)
