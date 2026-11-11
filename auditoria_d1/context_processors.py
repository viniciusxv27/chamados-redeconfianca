from .permissoes import pode_ver


def auditoria_d1_menu(request):
    """Item "Auditoria D-1" do menu: SUPERADMIN e quem ele liberou."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    return {'auditoria_d1_liberada': pode_ver(user)}
