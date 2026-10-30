from .permissoes import papel


def rotina_adm_menu(request):
    """Item "Rotina ADM" do menu: SUPERADMIN e quem ele liberou (pessoa ou grupo)."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    return {'rotina_adm_liberada': papel(user) is not None}
