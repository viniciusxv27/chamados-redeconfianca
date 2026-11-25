from django import template

from auditoria_sap.permissions import nome_da_loja, pode_ver_estoque_lp

register = template.Library()


@register.filter
def loja(pdv):
    """O nome da loja como a tela mostra: "L220" → "ITAIM PAULISTA"."""
    return nome_da_loja(pdv)


@register.filter
def ve_estoque_lp(user):
    """A aba Estoque LP (só o SUPERADMIN)."""
    try:
        return pode_ver_estoque_lp(user)
    except Exception:  # noqa: BLE001 — aba nunca derruba a página
        return False
