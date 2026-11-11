from .permissoes import pode_ver


def contas_pagar_menu(request):
    """Item "Contas a Pagar" do menu: SUPERADMIN e quem ele liberou."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    return {'contas_pagar_liberado': pode_ver(user)}
