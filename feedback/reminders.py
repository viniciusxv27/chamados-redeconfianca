"""Geração de lembretes de feedback (mensal e janela de experiência)."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from django.urls import reverse

from .models import Feedback, FeedbackAssignment, FeedbackReminderDismissal


REMINDER_THRESHOLD_DAYS = 10


def _last_day_of_month(today: date) -> date:
    last = monthrange(today.year, today.month)[1]
    return date(today.year, today.month, last)


def _has_feedback_in_month(evaluator_id: int, evaluatee_id: int, today: date) -> bool:
    return Feedback.objects.filter(
        evaluator_id=evaluator_id,
        evaluatee_id=evaluatee_id,
        data__year=today.year,
        data__month=today.month,
    ).exists()


def _has_feedback_after(evaluator_id: int, evaluatee_id: int, after_date: date) -> bool:
    return Feedback.objects.filter(
        evaluator_id=evaluator_id,
        evaluatee_id=evaluatee_id,
        data__gte=after_date,
    ).exists()


def get_pending_reminders(user, today: date | None = None) -> list[dict]:
    """Retorna a lista de lembretes pendentes para o usuário (avaliador)."""
    from django.utils import timezone as dj_tz

    if today is None:
        today = dj_tz.localdate()

    reminders: list[dict] = []

    assignments = (
        FeedbackAssignment.objects
        .filter(evaluator=user, status='ACTIVE')
        .select_related('evaluatee')
    )

    dismissed_keys = set(
        FeedbackReminderDismissal.objects
        .filter(user=user)
        .values_list('key', flat=True)
    )

    # === Lembretes mensais ===
    last_day = _last_day_of_month(today)
    days_to_month_end = (last_day - today).days
    if 0 <= days_to_month_end <= REMINDER_THRESHOLD_DAYS:
        month_key_part = f'{today.year:04d}-{today.month:02d}'
        for a in assignments.filter(monthly=True):
            if _has_feedback_in_month(user.id, a.evaluatee_id, today):
                continue
            key = f'monthly:{month_key_part}:{a.id}'
            if key in dismissed_keys:
                continue
            reminders.append({
                'key': key,
                'type': 'monthly',
                'assignment_id': a.id,
                'evaluatee_id': a.evaluatee_id,
                'evaluatee_name': a.evaluatee.get_full_name() or a.evaluatee.username,
                'days_remaining': days_to_month_end,
                'message': (
                    f'Faltam {days_to_month_end} dia(s) para o fim do mês. '
                    f'Você ainda não aplicou o feedback mensal de '
                    f'{a.evaluatee.get_full_name() or a.evaluatee.username}.'
                ),
                'action_url': reverse('feedback:create_from_assignment', args=[a.id]),
            })

    # === Lembretes de janela de experiência ===
    for a in assignments:
        evaluatee = a.evaluatee
        if not getattr(evaluatee, 'has_experience_window', False):
            continue
        admission = getattr(evaluatee, 'admission_date', None)
        if not admission:
            continue
        days_since = (today - admission).days
        if days_since < 0:
            continue

        for window_label, window_days, window_key in (
            ('1ª janela (45 dias)', 45, 'FIRST'),
            ('2ª janela (90 dias)', 90, 'SECOND'),
        ):
            days_remaining = window_days - days_since
            if not (0 <= days_remaining <= REMINDER_THRESHOLD_DAYS):
                continue
            window_start = admission + timedelta(days=window_days - REMINDER_THRESHOLD_DAYS)
            if _has_feedback_after(user.id, evaluatee.id, window_start):
                continue
            key = f'expwindow:{window_key}:{a.id}'
            if key in dismissed_keys:
                continue
            reminders.append({
                'key': key,
                'type': 'experience_window',
                'window': window_key,
                'assignment_id': a.id,
                'evaluatee_id': evaluatee.id,
                'evaluatee_name': evaluatee.get_full_name() or evaluatee.username,
                'days_remaining': days_remaining,
                'message': (
                    f'Faltam {days_remaining} dia(s) para o fim da {window_label} '
                    f'de {evaluatee.get_full_name() or evaluatee.username}. '
                    f'Aplique o feedback antes do encerramento.'
                ),
                'action_url': reverse('feedback:create_from_assignment', args=[a.id]),
            })

    return reminders
