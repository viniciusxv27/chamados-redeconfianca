"""Quem enxerga o mapa.

O módulo é **oculto**: não aparece no menu e o endereço não é divulgado. Isso
não é o mesmo que ser seguro, então o acesso é conferido no servidor, em toda
tela e em toda API — endereço não divulgado que responde para qualquer um é só
um endereço que ninguém achou ainda.
"""


def pode_ver_mapa(user) -> bool:
    """Só administração: é dado de localização de pessoa."""
    return bool(
        user and getattr(user, 'is_authenticated', False)
        and (user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN')
    )
