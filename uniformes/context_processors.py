from .permissoes import pode_gerir


def uniformes_menu(request):
    """Expõe ``uniformes_liberado`` ao base.html (item do menu)."""
    return {'uniformes_liberado': pode_gerir(getattr(request, 'user', None))}
