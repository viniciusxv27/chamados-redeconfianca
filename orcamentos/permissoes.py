"""Quem usa e quem gere o módulo de Orçamentos.

- Usa: todo colaborador ativo lotado em loja (setor com ADABAS ou nome "Loja …"),
  o SUPERADMIN, os gestores e quem o SUPERADMIN liberar.
- Gere (importa mailing e vê o painel): o SUPERADMIN (todas as lojas) e os
  gestores que ele escolher (as lojas marcadas; nenhuma marcada = todas).
"""
from users.models import Sector


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def e_loja(setor):
    return bool(setor) and (bool((setor.adabas or '').strip()) or setor.name.strip().lower().startswith('loja'))


def todas_as_lojas():
    return [s for s in Sector.objects.order_by('name') if e_loja(s)]


def _gestor(user):
    if not hasattr(user, '_orc_gestor'):
        from .models import GestorOrcamentos
        user._orc_gestor = GestorOrcamentos.objects.filter(user=user).prefetch_related('lojas').first()
    return user._orc_gestor


def pode_gerir(user):
    return e_superadmin(user) or bool(user and user.is_authenticated and _gestor(user))


def lojas_geridas(user):
    """As lojas que a pessoa gere (mailing e painel)."""
    if e_superadmin(user):
        return todas_as_lojas()
    g = _gestor(user) if user and user.is_authenticated else None
    if not g:
        return []
    marcadas = list(g.lojas.all())
    return sorted(marcadas, key=lambda s: s.name) if marcadas else todas_as_lojas()


def liberado(user):
    from .models import LiberacaoOrcamentos
    return LiberacaoOrcamentos.objects.filter(user=user).exists()


def pode_usar(user):
    if not (user and getattr(user, 'is_authenticated', False) and user.is_active):
        return False
    return e_superadmin(user) or e_loja(user.sector) or pode_gerir(user) or liberado(user)


def lojas_do_vendedor(user):
    """As lojas cujo mailing a pessoa trabalha: a dela, ou as que ela gere."""
    if pode_gerir(user):
        return lojas_geridas(user)
    return [user.sector] if e_loja(user.sector) else []
