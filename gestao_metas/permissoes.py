"""Quem entra no Gerenciamento de metas e com qual papel.

SUPERADMIN sempre (e só ele mexe na lista); fora dele, quem está em
``AcessoGestaoMetas`` — pela pessoa ou por um grupo de comunicação. Editor vale
mais que Visualizador quando a pessoa tem os dois. Quem não tem acesso recebe
404: o módulo não aparece nem no menu.
"""
from functools import wraps

from django.db.models import Q
from django.http import Http404, JsonResponse

SUPERADMIN = 'SUPERADMIN'
EDITOR = 'EDITOR'
VISUALIZADOR = 'VISUALIZADOR'


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def papel(user):
    """'SUPERADMIN', 'EDITOR', 'VISUALIZADOR' ou None (guardado no usuário: uma consulta por request)."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if e_superadmin(user):
        return SUPERADMIN
    if not hasattr(user, '_gestao_metas_papel'):
        from .models import AcessoGestaoMetas
        try:
            papeis = set(AcessoGestaoMetas.objects.filter(Q(user=user) | Q(grupo__members=user))
                         .values_list('papel', flat=True))
        except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
            papeis = set()
        user._gestao_metas_papel = EDITOR if EDITOR in papeis else (VISUALIZADOR if papeis else None)
    return user._gestao_metas_papel


def pode_ver(user):
    return papel(user) is not None


def pode_editar(user):
    return papel(user) in (SUPERADMIN, EDITOR)


def exige(editar=False, superadmin=False):
    """404 para quem não tem acesso; 403 (JSON quando for fetch) para quem só vê e tentou mudar."""
    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            p = papel(request.user)
            if p is None:
                raise Http404
            barrado = (superadmin and p != SUPERADMIN) or (editar and p not in (SUPERADMIN, EDITOR))
            if barrado:
                aviso = 'Seu acesso ao Gerenciamento de metas é só de visualização.'
                if superadmin:
                    aviso = 'Só o SUPERADMIN faz isso.'
                if request.content_type == 'application/json' or 'application/json' in request.headers.get('Accept', ''):
                    return JsonResponse({'ok': False, 'erro': aviso}, status=403)
                from django.contrib import messages
                from django.shortcuts import redirect
                messages.error(request, aviso)
                return redirect('gestao_metas:inicio')
            return view(request, *args, **kwargs)
        return _view
    return decorador
