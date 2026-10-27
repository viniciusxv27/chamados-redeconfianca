from .permissoes import pode_no_template


def fibras_menu(request):
    """Item "Fibras" do menu e o que cada tela de Fibras mostra: SUPERADMIN e quem ele liberou."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    try:
        fb_pode = pode_no_template(user)
    except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
        fb_pode = {}
    return {'fibras_liberado': bool(fb_pode.get('modulo_ver')), 'fb_pode': fb_pode}
