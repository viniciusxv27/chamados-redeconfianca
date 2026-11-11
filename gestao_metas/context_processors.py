from .permissoes import pode_ver


def gestao_metas_menu(request):
    """Item "Gerenciamento de metas" do menu: SUPERADMIN e quem ele liberou."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    return {'gestao_metas_liberado': pode_ver(user)}
