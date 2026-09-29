"""Regras de acesso do módulo de Cartões.

Três papéis, do mais amplo para o mais estreito:

- **SUPERADMIN** — cuida de tudo e é quem diz **quem mais** cuida (a lista de
  `AcessoCartoes`);
- **quem o SUPERADMIN liberou** (`AcessoCartoes`) — gere o módulo como ele: vê
  todos os cartões, cria, lança gasto e concilia fatura. Não mexe na lista;
- **responsável por um cartão** — vê e lança no que é dele.

Antes só existiam o primeiro e o terceiro: quem toca o financeiro no dia a dia
precisava ser SUPERADMIN do portal, ou nada.
"""
from .models import AcessoCartoes, Cartao


def is_superadmin(user) -> bool:
    return bool(
        user and getattr(user, 'is_authenticated', False)
        and (user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN')
    )


def pode_gerir_cartoes(user) -> bool:
    """Gere o módulo: o SUPERADMIN e quem ele liberou."""
    return is_superadmin(user) or AcessoCartoes.tem_acesso(user)


def pode_administrar_acessos(user) -> bool:
    """Quem entrega (e tira) a chave do módulo: só o SUPERADMIN.

    Quem foi liberado não libera mais ninguém — senão o controle de quem cuida
    do dinheiro deixaria de estar com quem responde pelo portal.
    """
    return is_superadmin(user)


def can_access_cartoes(user) -> bool:
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if pode_gerir_cartoes(user):
        return True
    from users.module_access import user_has_module
    return (Cartao.objects.filter(responsavel=user, ativo=True).exists()
            or user_has_module(user, 'cartoes'))


def cartoes_do_usuario(user):
    """Cartões visíveis: quem gere o módulo vê todos; demais veem apenas os seus."""
    qs = Cartao.objects.select_related('responsavel').all()
    if pode_gerir_cartoes(user):
        return qs
    return qs.filter(responsavel=user)


def can_manage_cartao(user, cartao) -> bool:
    """Pode ver o extrato e lançar gastos: quem gere o módulo ou o responsável."""
    return pode_gerir_cartoes(user) or cartao.responsavel_id == getattr(user, 'id', None)
