"""Regras de acesso do módulo de Cartões.

Acesso ao módulo = SUPERADMIN OU ser responsável por algum cartão ativo.
Não há tabela de permissão extra: a "gestão" de um cartão é o próprio campo
``responsavel`` (um responsável por cartão, conforme decisão de produto).
"""
from .models import Cartao


def is_superadmin(user) -> bool:
    return bool(
        user and getattr(user, 'is_authenticated', False)
        and (user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN')
    )


def can_access_cartoes(user) -> bool:
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if is_superadmin(user):
        return True
    return Cartao.objects.filter(responsavel=user, ativo=True).exists()


def cartoes_do_usuario(user):
    """Cartões visíveis: SUPERADMIN vê todos; demais veem apenas os seus."""
    qs = Cartao.objects.select_related('responsavel').all()
    if is_superadmin(user):
        return qs
    return qs.filter(responsavel=user)


def can_manage_cartao(user, cartao) -> bool:
    """Pode ver o extrato e lançar gastos: SUPERADMIN ou o responsável do cartão."""
    return is_superadmin(user) or cartao.responsavel_id == getattr(user, 'id', None)
