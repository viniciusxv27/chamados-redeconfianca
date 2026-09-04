"""O item do Banco de Talentos no menu.

Fica num context processor, e não num {% if %} com consulta no template, porque
a resposta depende de grupo do usuário — uma consulta por render de menu, em
toda página do portal, sairia caro.
"""


def banco_de_talentos(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    try:
        from .permissions import pode_usar
        return {'pode_ver_talentos': pode_usar(user)}
    except Exception:                                        # nunca derruba o menu
        return {'pode_ver_talentos': False}
