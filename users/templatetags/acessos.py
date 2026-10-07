"""Filtros de acesso para o menu: {% if user|tem_acesso:'fornecedores' %}.

Uma pergunta só, respondida pelo gate de verdade de cada módulo (regra normal
OU liberação individual). Antes o menu repetia a regra por conta própria —
`group.name == "Gestores de Compras"` — e a liberação feita na tela do usuário
abria a view sem aparecer no menu.
"""
from django import template

from users.module_access import tem_acesso as _tem_acesso

register = template.Library()


@register.filter
def tem_acesso(user, chave):
    return _tem_acesso(user, chave)


@register.filter
def sap_na_gestao(user):
    """O gerente de loja acha a Visão SAP em "Gestão Administrativa" (a da loja dele)."""
    try:
        from auditoria_sap.permissions import no_menu_da_gestao
        return no_menu_da_gestao(user)
    except Exception:  # noqa: BLE001 — menu nunca derruba a página
        return False
