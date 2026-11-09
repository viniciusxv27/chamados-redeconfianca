"""Quem manda na Rotina Gerencial."""


def e_superadmin(user):
    """SUPERADMIN do portal: hierarquia SUPERADMIN ou superusuário do Django.

    Mesmo critério de reuniões, agenda e drive — quem é SUPERADMIN pela
    hierarquia (o caso normal) precisa enxergar a gestão tanto quanto o
    superusuário.
    """
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def pode_gerir(user):
    """Gerencia a rotina de outras pessoas: SUPERADMIN ou quem ele pôs em GestorRotina.

    Fica guardado no objeto do usuário: a mesma requisição pergunta várias vezes.
    """
    if not (user and user.is_authenticated):
        return False
    if e_superadmin(user):
        return True
    guardado = getattr(user, '_rotina_gestor', None)
    if guardado is None:
        from .models import GestorRotina
        guardado = GestorRotina.objects.filter(user_id=user.pk).exists()
        try:
            user._rotina_gestor = guardado
        except Exception:  # noqa: BLE001
            pass
    return guardado
