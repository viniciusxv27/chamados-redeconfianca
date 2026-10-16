from .permissoes import pode_entrar


def financeiro_menu(request):
    """Item "Financeiro" do menu: SUPERADMIN e quem ele liberou."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    try:
        return {'financeiro_liberado': pode_entrar(user)}
    except Exception:  # noqa: BLE001 — tabela ainda não migrada: o menu não pode cair
        return {'financeiro_liberado': False}
