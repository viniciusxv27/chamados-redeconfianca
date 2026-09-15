"""Quem manda na Rotina Gerencial."""


def e_superadmin(user):
    """SUPERADMIN do portal: hierarquia SUPERADMIN ou superusuário do Django.

    Mesmo critério de reuniões, agenda e drive — quem é SUPERADMIN pela
    hierarquia (o caso normal) precisa enxergar a gestão tanto quanto o
    superusuário.
    """
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))
