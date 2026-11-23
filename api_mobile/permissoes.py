"""Quem passa na API do app."""
from rest_framework.exceptions import APIException
from rest_framework.permissions import BasePermission

from .bloqueios import bloqueio_atual


class PortalBloqueado(APIException):
    """423 Locked: a pessoa está bloqueada no portal (férias, jornada, curso, trilha, cadastro)."""
    status_code = 423
    default_code = 'bloqueado'

    def __init__(self, bloqueio):
        super().__init__(detail={'erro': bloqueio['mensagem'], 'bloqueio': bloqueio})


def e_superadmin(user):
    """O mesmo teste das travas do portal (portal_popups/context_processors.py ``_e_superadmin``)."""
    return bool(user and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def bloqueio_do_usuario(user, usar_cache=True):
    # Ajuste de pré-cadastro: a regra de users/pre_registration_middleware.py, que não prende o
    # SUPERADMIN (23/11/2026). As outras travas vêm dos próprios middlewares (bloqueios.py), que já
    # deixam o SUPERADMIN passar.
    if not e_superadmin(user) and getattr(user, 'needs_pre_registration_adjustment', lambda: False)():
        return {'tipo': 'cadastro', 'titulo': 'Cadastro com ajustes pendentes',
                'mensagem': 'Seu cadastro precisa de ajustes. Corrija no portal para continuar.',
                'caminho_web': '/'}
    return bloqueio_atual(user, usar_cache=usar_cache)


class SemBloqueio(BasePermission):
    """Logado e sem bloqueio do portal — o mesmo que os middlewares exigem na web."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        bloqueio = bloqueio_do_usuario(user)
        if bloqueio:
            raise PortalBloqueado(bloqueio)
        return True
