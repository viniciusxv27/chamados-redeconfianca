"""Tag do cartão do Quiz na home: `{% cartao_quiz_home as cartao %}`."""
from django import template

from quiz.home import cartao_seguro

register = template.Library()


@register.simple_tag(takes_context=True)
def cartao_quiz_home(context):
    """HTML do cartão, ou '' para quem não tem sala aberta (ou se algo falhar).

    Confia no `quiz_salas_abertas` do context processor: quem não tem sala
    nenhuma não custa consulta a mais.
    """
    if not context.get('quiz_salas_abertas'):
        return ''
    request = context.get('request')
    user = getattr(request, 'user', None) or context.get('user')
    if not (user and user.is_authenticated):
        return ''
    return cartao_seguro(user)
