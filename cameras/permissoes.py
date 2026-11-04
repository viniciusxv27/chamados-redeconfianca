from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def exige_superadmin(view):
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not e_superadmin(request.user):
            messages.error(request, 'As câmeras são só para o SUPERADMIN.')
            return redirect('home')
        return view(request, *args, **kwargs)
    return _view
