"""Bloqueio de navegação para SUPERVISOR com feedbacks de setor pendentes.

Um supervisor precisa aplicar o feedback dos colaboradores PADRÃO do seu setor
dentro da periodicidade definida pelo tempo de casa (mesma regra dos relatórios).
Enquanto houver pendências, um popup travado é exibido no portal.
"""
from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone


def _supervisor_sector_targets(user):
    """Colaboradores PADRÃO ativos que o supervisor deve avaliar (por setor)."""
    from users.models import User

    if getattr(user, 'hierarchy', '') != 'SUPERVISOR':
        return User.objects.none()

    from .views import _user_sector_ids

    sector_ids = _user_sector_ids(user)
    if not sector_ids:
        return User.objects.none()

    return (
        User.objects
        .filter(is_active=True, status=User.STATUS_ATIVO, hierarchy__in=['PADRAO', 'PADRÃO'])
        .filter(Q(sectors__in=sector_ids) | Q(sector_id__in=sector_ids))
        .exclude(id=user.id)
        .select_related('sector')
        .distinct()
        .order_by('first_name', 'last_name')
    )


def supervisor_pending_feedback(user, today=None):
    """Lista de colaboradores do setor cujo feedback está pendente no período."""
    from .models import Feedback
    from .views import _rule_for_user

    if today is None:
        today = timezone.localdate()

    targets = list(_supervisor_sector_targets(user))
    if not targets:
        return []

    ids = [u.id for u in targets]
    max_window_start = today - timedelta(days=90)
    recent = (
        Feedback.objects
        .filter(evaluatee_id__in=ids, data__gte=max_window_start)
        .values_list('evaluatee_id', 'data')
    )
    dates_by_user: dict[int, list] = {}
    for evaluatee_id, data in recent:
        dd = data.date() if hasattr(data, 'date') else data
        dates_by_user.setdefault(evaluatee_id, []).append(dd)

    pending = []
    for u in targets:
        rule, _months = _rule_for_user(u, today)
        period_start = today - timedelta(days=rule['period_days'])
        if any(d >= period_start for d in dates_by_user.get(u.id, [])):
            continue
        pending.append({
            'id': u.id,
            'name': u.get_full_name() or u.username,
            'sector': u.sector.name if u.sector else '',
            'rule_label': rule['label'],
            'period_label': rule['period_label'],
            'url': reverse('feedback:create') + f'?evaluatee={u.id}',
        })
    return pending
