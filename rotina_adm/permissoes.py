"""Quem entra na Rotina ADM e com qual papel.

SUPERADMIN sempre; fora dele, só quem está em ``AcessoRotinaAdm`` — pela pessoa
ou por um grupo de comunicação. Gestor vale mais que Visualizador quando a
pessoa tem os dois (um pelo grupo, outro direto).
"""
from functools import wraps

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect

SUPERADMIN = 'SUPERADMIN'
GESTOR = 'GESTOR'
VISUALIZADOR = 'VISUALIZADOR'


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def papel(user):
    """'SUPERADMIN', 'GESTOR', 'VISUALIZADOR' ou None. Guardado no usuário (uma consulta por request)."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if e_superadmin(user):
        return SUPERADMIN
    if not hasattr(user, '_rotina_adm_papel'):
        from .models import AcessoRotinaAdm
        try:
            papeis = set(AcessoRotinaAdm.objects.filter(Q(user=user) | Q(grupo__members=user))
                         .values_list('papel', flat=True))
        except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
            papeis = set()
        user._rotina_adm_papel = GESTOR if GESTOR in papeis else (VISUALIZADOR if papeis else None)
    return user._rotina_adm_papel


def pode_gerir(user):
    """Monta e copia o check-list de qualquer loja e escolhe a loja/pessoa que vê."""
    return papel(user) in (SUPERADMIN, GESTOR)


def exige_acesso(gerir=False):
    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            p = papel(request.user)
            if p is None or (gerir and p not in (SUPERADMIN, GESTOR)):
                messages.error(request, 'A Rotina ADM não está liberada para você.')
                return redirect('home')
            return view(request, *args, **kwargs)
        return _view
    return decorador
