"""Expõe flags do Impulso para os templates (usado na sidebar do base.html)."""
from .utils import is_impulso_manager, is_impulso_member


def impulso_menu(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'can_access_impulso': False, 'is_impulso_gestor': False}
    return {
        'can_access_impulso': is_impulso_member(user),
        'is_impulso_gestor': is_impulso_manager(user),
    }
