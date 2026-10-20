from .permissoes import pode_entrar


def acess_menu(request):
    """Item "Acess" do menu: SUPERADMIN e quem ele liberou."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    try:
        return {'acess_liberado': pode_entrar(user)}
    except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
        return {'acess_liberado': False}
