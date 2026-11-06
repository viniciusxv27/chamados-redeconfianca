"""Quem vê cada categoria de documento do colaborador (e o arquivo).

Padrão: Atestados só para RH/DP e SUPERADMIN (dado de saúde); o resto para quem gerencia
cadastros. O SUPERADMIN muda por categoria em /users/manage/documentos/permissoes/ e pode
liberar pessoas a mais. O SUPERADMIN sempre vê tudo.
"""
from django.db import transaction

from .models import CATEGORIA_DOC_ATESTADO, CATEGORIAS_DOCUMENTO, PermissaoCategoriaDocumento as P

PADRAO = {CATEGORIA_DOC_ATESTADO: P.RH_DP}


def _superadmin(user):
    return bool(user and user.is_authenticated and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def regras(user=None):
    """{categoria: (acesso, {ids a mais})}. Guardado no próprio usuário durante a requisição."""
    if user is not None and hasattr(user, '_regras_docs'):
        return user._regras_docs
    saida = {c: (PADRAO.get(c, P.GESTORES), set()) for c, _r in CATEGORIAS_DOCUMENTO}
    try:
        with transaction.atomic():      # savepoint: sem a tabela, a transação da página segue viva
            for regra in P.objects.prefetch_related('usuarios'):
                saida[regra.categoria] = (regra.acesso, {u.pk for u in regra.usuarios.all()})
    except Exception:  # noqa: BLE001 — tabela ainda não migrada: vale o padrão
        pass
    if user is not None:
        user._regras_docs = saida
    return saida


def pode_ver_categoria(user, categoria):
    if not (user and user.is_authenticated):
        return False
    if _superadmin(user):
        return True
    acesso, a_mais = regras(user).get(categoria, (P.GESTORES, set()))
    if user.pk in a_mais:
        return True
    if acesso == P.SUPERADMIN:
        return False
    if acesso == P.RH_DP:
        return user.pode_ver_atestados()
    return user.can_manage_users()


def categorias_visiveis(user):
    return [(c, r) for c, r in CATEGORIAS_DOCUMENTO if pode_ver_categoria(user, c)]


def pode_ver_documento(user, doc):
    """O próprio colaborador vê os documentos dele; os outros, conforme a categoria."""
    if user and user.is_authenticated and doc.user_id == user.pk:
        return True
    return pode_ver_categoria(user, doc.categoria_efetiva)
