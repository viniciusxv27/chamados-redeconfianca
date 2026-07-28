"""Checker do portal_popups: exige a conclusão da trilha 5 (FUNÇÕES SAP)
para os membros do CommunicationGroup 'Gerente / ADM'.

O gate por grupo fica AQUI (não no popup) para respeitar a associação ao vivo:
quem entra/sai do grupo passa a ser exigido/liberado automaticamente. O popup
é target_all=True e este checker devolve "concluído" para quem não é do grupo.
"""
from portal_popups.checkers import register_popup_checker

GRUPO_ALVO = 'Gerente / ADM'
TRILHA_ID = 5


@register_popup_checker('trilha5_gerente_adm', 'Trilha "FUNÇÕES SAP" concluída (grupo Gerente / ADM)')
def trilha5_concluida(user):
    """Concluído (True) para quem não é do grupo, ou quem já concluiu a trilha 5."""
    if not getattr(user, 'is_authenticated', False):
        return True
    # Só exige de quem faz parte do grupo "Gerente / ADM".
    if not user.communication_groups.filter(name__iexact=GRUPO_ALVO).exists():
        return True
    from .models import Certificate, TrailProgress
    return (
        TrailProgress.objects.filter(
            trail_id=TRILHA_ID, user=user, status='completed').exists()
        or Certificate.objects.filter(trail_id=TRILHA_ID, user=user).exists()
    )
