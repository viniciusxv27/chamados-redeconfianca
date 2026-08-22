"""Acesso ao módulo de Vendas.

Duas alturas, não duas portas: **todo mundo entra**, e o que muda é o recorte.
O superadmin vê a empresa inteira; o vendedor vê o que ele mesmo vendeu ou
lançou. Isso evita a alternativa comum — um módulo só para administrador e um
relatório paralelo para o resto — que sempre acaba com dois números diferentes
para a mesma venda.
"""
from django.db.models import Q


def is_superadmin(user) -> bool:
    return bool(
        user and getattr(user, 'is_authenticated', False)
        and (user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN')
    )


def can_access_vendas(user) -> bool:
    """Quem entra no módulo. Vendedor entra e vê o dele."""
    return bool(user and getattr(user, 'is_authenticated', False))


def pode_lancar_venda(user) -> bool:
    """Quem lança venda pelo portal."""
    return can_access_vendas(user)


def pode_gerenciar_precos(user) -> bool:
    """Tabela de preços é cadastro da empresa: só administrador mexe."""
    return is_superadmin(user)


def vendas_do_usuario(user, qs):
    """Recorta o queryset para o que a pessoa pode ver.

    Vendedor enxerga a venda em que ele é o vendedor **ou** foi quem lançou —
    quem digita a venda de outro precisa conseguir conferir o que digitou.
    """
    if is_superadmin(user):
        return qs
    return qs.filter(Q(vendedor=user) | Q(created_by=user))
