"""Quem, além do organizador e dos convidados, manda nas reuniões."""


def e_superadmin(user):
    """SUPERADMIN do portal: hierarquia SUPERADMIN ou superusuário do Django.

    O resto do portal (agenda, drive, usuários) aceita as duas coisas. Aqui só
    se olhava `is_superuser`, e o SUPERADMIN cadastrado pela hierarquia — o
    caso normal — não via as reuniões dos outros.
    """
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))
