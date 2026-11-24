def carreira_menu(request):
    """O item "Progresso" no menu: SUPERADMIN sempre; o comercial depois que ele libera."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'carreira_liberado': False}
    try:
        from .regras import pode_ver_modulo
        return {'carreira_liberado': pode_ver_modulo(user)}
    except Exception:  # noqa: BLE001 — menu nunca derruba a página
        return {'carreira_liberado': False}
