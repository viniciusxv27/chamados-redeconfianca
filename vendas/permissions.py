"""Acesso ao módulo de Vendas.

Por ora, restrito a SUPERADMIN (módulo administrativo, fora do menu). O ponto
único abaixo facilita ampliar depois (ex.: liberar 'Lançar venda' para lojas).
"""


def is_superadmin(user) -> bool:
    return bool(
        user and getattr(user, 'is_authenticated', False)
        and (user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN')
    )


def can_access_vendas(user) -> bool:
    return is_superadmin(user)
