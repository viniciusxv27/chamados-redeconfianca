from django.db import DatabaseError

from .permissoes import e_superadmin


def cameras_menu(request):
    """Item do menu e aviso de alertas novos: só o SUPERADMIN paga a consulta."""
    user = getattr(request, 'user', None)
    if not e_superadmin(user):
        return {}
    from .models import AlertaCamera
    try:
        novos = AlertaCamera.objects.filter(status=AlertaCamera.Status.NOVO).count()
    except DatabaseError:  # servidor que ainda não rodou o migrate
        novos = 0
    return {'cameras_liberado': True, 'cameras_alertas_novos': novos}
