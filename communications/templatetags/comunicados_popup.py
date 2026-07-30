"""Tags do popup de comunicados pendentes.

Usada dentro de templates/portal_popups/_popup_gate.html. Como é uma tag, a
consulta só roda quando o popup realmente aparece — nas demais páginas não
custa nada.
"""
from django import template

register = template.Library()

LIMITE_PADRAO = 3


@register.simple_tag
def comunicados_para_ler(user, limite=LIMITE_PADRAO):
    """Retorna {'itens': [até `limite` comunicados], 'extras': quantos sobraram}."""
    try:
        from ..popup_checkers import comunicados_pendentes
        qs = comunicados_pendentes(user)
        total = qs.count()
        return {
            'itens': list(qs[:limite]),
            'extras': max(total - limite, 0),
            'total': total,
        }
    except Exception:
        return {'itens': [], 'extras': 0, 'total': 0}
