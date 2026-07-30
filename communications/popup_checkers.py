"""Checker do portal_popups: obriga o "de acordo" em todos os comunicados ativos.

Enquanto o usuário tiver algum comunicado ativo direcionado a ele sem
CommunicationRead.status == 'ESTOU_CIENTE', o popup bloqueante permanece.
Como o popup usa action_url='/communications/', toda a seção de comunicados
fica liberada (on_action_page faz startswith), permitindo ler e dar o de acordo.
"""
from datetime import date

from django.db.models import Q
from django.utils import timezone

from portal_popups.checkers import register_popup_checker


# A obrigatoriedade do "de acordo" vale apenas para comunicados publicados
# (created_at = Data de Envio) A PARTIR desta data. Comunicados anteriores não
# bloqueiam ninguém — a regra passou a valer em 27/07/2026.
REGRA_ATIVA_DESDE = date(2026, 7, 27)


def comunicados_pendentes(user):
    """Comunicados ativos direcionados ao usuário que ainda aguardam o "de acordo".

    Fonte única usada tanto pelo checker do popup quanto pela listagem exibida
    dentro dele.
    """
    from .models import Communication

    if not getattr(user, 'is_authenticated', False):
        return Communication.objects.none()

    now = timezone.now()
    return (
        Communication.objects
        .filter(created_at__date__gte=REGRA_ATIVA_DESDE)
        .filter(Q(recipients=user) | Q(send_to_all=True))
        .filter(Q(active_from__isnull=True) | Q(active_from__lte=now))
        .filter(Q(active_until__isnull=True) | Q(active_until__gte=now))
        .exclude(
            communicationread__user=user,
            communicationread__status='ESTOU_CIENTE',
        )
        .distinct()
        .order_by('-is_pinned', '-created_at')
    )


@register_popup_checker('comunicados_pendentes', 'Comunicados: todos com "Estou Ciente"')
def comunicados_todos_cientes(user):
    """Concluído (True) quando não há comunicado pendente de "de acordo"."""
    return not comunicados_pendentes(user).exists()
