"""Quem vê o módulo de Uniformes.

- **SUPERADMIN** — gere o estoque e é o único que diz quem mais gere
  (a lista de `AcessoUniformes`);
- **quem o SUPERADMIN liberou** — gere o estoque (itens, entradas, retiradas,
  devoluções, ajustes, importação), mas não mexe na lista;
- **o resto** — não vê o módulo: nem menu, nem página (404).

A ficha do colaborador mostra os uniformes dele para quem já abre a ficha; a
própria pessoa vê os seus no "Meu perfil".
"""
from django.db import DatabaseError, transaction


def e_superadmin(user) -> bool:
    return bool(
        user and getattr(user, 'is_authenticated', False)
        and (user.is_superuser or getattr(user, 'hierarchy', None) == 'SUPERADMIN')
    )


def esta_na_lista(user) -> bool:
    """A pessoa foi liberada? Guardado no usuário: o menu pergunta em toda página."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if not hasattr(user, '_uniformes_liberado'):
        from .models import AcessoUniformes
        try:
            # Savepoint: no servidor que ainda não rodou o migrate, a falha não
            # envenena a transação da página que só queria montar o menu.
            with transaction.atomic():
                user._uniformes_liberado = AcessoUniformes.objects.filter(user=user).exists()
        except DatabaseError:
            user._uniformes_liberado = False
    return user._uniformes_liberado


def pode_gerir(user) -> bool:
    return e_superadmin(user) or esta_na_lista(user)


def pode_administrar_acessos(user) -> bool:
    return e_superadmin(user)
