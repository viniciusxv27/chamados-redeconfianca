from .permissions import can_access_cartoes


def cartoes_menu(request):
    """Expõe ``can_access_cartoes`` ao template para gatear o item de menu."""
    return {'can_access_cartoes': can_access_cartoes(getattr(request, 'user', None))}
