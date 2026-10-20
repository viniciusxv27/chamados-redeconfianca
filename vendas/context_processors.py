from .permissions import pode_no_template


def vendas_menu(request):
    """Item "Vendas" do menu e o que cada tela de Vendas mostra: SUPERADMIN e quem ele liberou."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    try:
        vd_pode = pode_no_template(user)
    except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
        vd_pode = {}
    return {'vendas_liberado': bool(vd_pode.get('modulo_ver')), 'vd_pode': vd_pode}
