from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Max, Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from users.models import Sector, User

from .ai import generate_ai_summary
from .forms import AssignmentForm, FeedbackForm
from .models import Feedback, FeedbackAssignment, FeedbackReminderDismissal
from .reminders import get_pending_reminders


def _is_superadmin(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(user, 'hierarchy', '') == 'SUPERADMIN'


def superadmin_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not _is_superadmin(request.user):
            return HttpResponseForbidden('Acesso restrito a superadministradores.')
        return view_func(request, *args, **kwargs)
    return _wrapped


@login_required
def dashboard(request):
    user = request.user
    my_targets = FeedbackAssignment.objects.filter(
        evaluator=user, status='ACTIVE'
    ).select_related('evaluatee').order_by('-created_at')

    given = Feedback.objects.filter(evaluator=user).select_related('evaluatee').order_by('-created_at')[:10]
    received = Feedback.objects.filter(evaluatee=user).select_related('evaluator').order_by('-created_at')[:10]

    stats = {
        'targets_count': my_targets.count(),
        'given_count': Feedback.objects.filter(evaluator=user).count(),
        'received_count': Feedback.objects.filter(evaluatee=user).count(),
    }

    context = {
        'my_targets': my_targets,
        'given': given,
        'received': received,
        'stats': stats,
        'is_superadmin': _is_superadmin(user),
    }
    return render(request, 'feedback/dashboard.html', context)


@login_required
def my_pending(request):
    my_targets = FeedbackAssignment.objects.filter(
        evaluator=request.user, status='ACTIVE'
    ).select_related('evaluatee').order_by('-created_at')
    return render(request, 'feedback/pending.html', {'my_targets': my_targets})


@login_required
def create_feedback(request, assignment_id=None):
    assignment = None
    evaluatee = None

    if assignment_id:
        assignment = get_object_or_404(FeedbackAssignment, pk=assignment_id)
        if assignment.evaluator_id != request.user.id and not _is_superadmin(request.user):
            return HttpResponseForbidden('Você não pode aplicar este feedback.')
        evaluatee = assignment.evaluatee
    else:
        # Sem atribuição: apenas superadmin pode escolher livremente,
        # ou usuário comum se foi indicado como avaliador em alguma atribuição.
        target_id = request.GET.get('evaluatee') or request.POST.get('evaluatee')
        if target_id:
            evaluatee = get_object_or_404(User, pk=target_id)
            if not _is_superadmin(request.user):
                has_assignment = FeedbackAssignment.objects.filter(
                    evaluator=request.user, evaluatee=evaluatee, status='ACTIVE'
                ).exists()
                if not has_assignment:
                    return HttpResponseForbidden('Você não tem atribuição para avaliar este colaborador.')

    if request.method == 'POST':
        if not evaluatee:
            messages.error(request, 'Selecione o colaborador a ser avaliado.')
            return redirect('feedback:dashboard')

        form = FeedbackForm(request.POST)
        if form.is_valid():
            fb = form.save(commit=False)
            fb.evaluator = request.user
            fb.evaluatee = evaluatee
            fb.assignment = assignment
            if not fb.nome_colaborador:
                fb.nome_colaborador = evaluatee.get_full_name() or evaluatee.username
            fb.save()

            # Tenta gerar resumo IA imediatamente (silencioso em caso de falha).
            try:
                generate_ai_summary(fb)
            except Exception:
                pass

            messages.success(request, 'Feedback registrado com sucesso.')
            return redirect('feedback:detail', feedback_id=fb.pk)
    else:
        initial = {}
        if evaluatee:
            initial['nome_colaborador'] = evaluatee.get_full_name() or evaluatee.username
            primary_sector = getattr(evaluatee, 'primary_sector', None)
            if callable(primary_sector):
                try:
                    primary_sector = primary_sector()
                except Exception:
                    primary_sector = None
            if primary_sector:
                initial['setor_area'] = getattr(primary_sector, 'name', '') or ''
        form = FeedbackForm(initial=initial)

    return render(request, 'feedback/create.html', {
        'form': form,
        'assignment': assignment,
        'evaluatee': evaluatee,
    })


@login_required
def feedback_detail(request, feedback_id):
    fb = get_object_or_404(
        Feedback.objects.select_related('evaluator', 'evaluatee', 'assignment'),
        pk=feedback_id,
    )

    is_admin = _is_superadmin(request.user)
    if not (is_admin or request.user.id in (fb.evaluator_id, fb.evaluatee_id)):
        return HttpResponseForbidden('Você não tem permissão para ver este feedback.')

    show_ai = is_admin
    ai_text = ''
    if show_ai:
        ai_text = fb.ai_summary or generate_ai_summary(fb)

    # O avaliado (sem ser admin nem o avaliador) não deve ver a nota geral.
    is_evaluatee_only = (
        not is_admin
        and request.user.id == fb.evaluatee_id
        and request.user.id != fb.evaluator_id
    )

    return render(request, 'feedback/detail.html', {
        'fb': fb,
        'show_ai': show_ai,
        'ai_text': ai_text,
        'previous': fb.previous_feedback(),
        'evolution_delta': fb.evolution_delta(),
        'is_evaluatee_only': is_evaluatee_only,
    })


@superadmin_required
@require_POST
def regenerate_ai_summary(request, feedback_id):
    fb = get_object_or_404(Feedback, pk=feedback_id)
    text = generate_ai_summary(fb, force=True)
    if not text and fb.ai_summary_error:
        messages.error(request, f'Erro ao gerar resumo: {fb.ai_summary_error}')
    else:
        messages.success(request, 'Resumo IA atualizado.')
    return redirect('feedback:detail', feedback_id=fb.pk)


@login_required
def user_history(request, user_id):
    target = get_object_or_404(User, pk=user_id)

    is_admin = _is_superadmin(request.user)
    if not (is_admin or request.user.id == target.id):
        # Permite também ao avaliador atual ver histórico de quem ele avalia.
        has_relation = FeedbackAssignment.objects.filter(
            evaluator=request.user, evaluatee=target
        ).exists() or Feedback.objects.filter(
            evaluator=request.user, evaluatee=target
        ).exists()
        if not has_relation:
            return HttpResponseForbidden('Sem permissão para ver este histórico.')

    feedbacks = Feedback.objects.filter(evaluatee=target).select_related('evaluator').order_by('-data', '-created_at')

    # Série temporal de evolução das médias
    timeline = []
    for fb in reversed(list(feedbacks)):
        avg = fb.average_score()
        if avg is not None:
            timeline.append({'date': fb.data.isoformat(), 'avg': avg, 'id': fb.pk})

    overall_avg = feedbacks.aggregate(avg=Avg('nota_comunicacao'))['avg']  # placeholder

    return render(request, 'feedback/history.html', {
        'target': target,
        'feedbacks': feedbacks,
        'timeline': timeline,
        'is_superadmin': is_admin,
    })


@superadmin_required
def manage_all(request):
    q = (request.GET.get('q') or '').strip()
    feedbacks = Feedback.objects.select_related('evaluator', 'evaluatee').order_by('-created_at')
    assignments = FeedbackAssignment.objects.select_related('evaluator', 'evaluatee').order_by('-created_at')

    if q:
        feedbacks = feedbacks.filter(
            Q(evaluatee__first_name__icontains=q) | Q(evaluatee__last_name__icontains=q)
            | Q(evaluator__first_name__icontains=q) | Q(evaluator__last_name__icontains=q)
            | Q(setor_area__icontains=q)
        )
        assignments = assignments.filter(
            Q(evaluatee__first_name__icontains=q) | Q(evaluatee__last_name__icontains=q)
            | Q(evaluator__first_name__icontains=q) | Q(evaluator__last_name__icontains=q)
        )

    user_stats = (
        Feedback.objects.values('evaluatee_id', 'evaluatee__first_name', 'evaluatee__last_name')
        .annotate(total=Count('id'))
        .order_by('-total')[:20]
    )

    return render(request, 'feedback/manage.html', {
        'feedbacks': feedbacks[:100],
        'assignments': assignments[:100],
        'user_stats': user_stats,
        'q': q,
    })


@superadmin_required
@require_http_methods(['GET', 'POST'])
def assign_view(request):
    if request.method == 'POST':
        form = AssignmentForm(request.POST)
        if form.is_valid():
            evaluator_id = form.cleaned_data['evaluator']
            evaluatee_ids_raw = form.cleaned_data['evaluatees']
            evaluatee_ids = [int(x) for x in evaluatee_ids_raw.split(',') if x.strip().isdigit()]

            if not evaluatee_ids:
                messages.error(request, 'Selecione pelo menos um colaborador a ser avaliado.')
                return redirect('feedback:assign')

            evaluator = get_object_or_404(User, pk=evaluator_id)
            created = 0
            for ev_id in evaluatee_ids:
                if ev_id == evaluator.id:
                    continue
                evaluatee = User.objects.filter(pk=ev_id).first()
                if not evaluatee:
                    continue
                exists = FeedbackAssignment.objects.filter(
                    evaluator=evaluator, evaluatee=evaluatee, status='ACTIVE'
                ).exists()
                if exists:
                    continue
                FeedbackAssignment.objects.create(
                    evaluator=evaluator,
                    evaluatee=evaluatee,
                    notes=form.cleaned_data.get('notes') or '',
                    monthly=form.cleaned_data.get('monthly') or False,
                    created_by=request.user,
                )
                created += 1
            messages.success(request, f'{created} atribuição(ões) criada(s).')
            return redirect('feedback:manage')
        else:
            messages.error(request, 'Dados inválidos.')

    return render(request, 'feedback/assign.html', {})


@superadmin_required
@require_POST
def delete_assignment(request, assignment_id):
    a = get_object_or_404(FeedbackAssignment, pk=assignment_id)
    a.delete()
    messages.success(request, 'Atribuição removida.')
    return redirect('feedback:manage')


@login_required
def api_search_users(request):
    q = (request.GET.get('q') or '').strip()
    qs = User.objects.filter(is_active=True)
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q)
        )
    qs = qs.order_by('first_name', 'last_name')[:20]
    return JsonResponse({
        'results': [
            {
                'id': u.id,
                'name': u.get_full_name() or u.username,
                'email': u.email,
                'sector': getattr(getattr(u, 'sector', None), 'name', '') or '',
            }
            for u in qs
        ]
    })


# ---------------------------------------------------------------------------
# Relatórios de cumprimento de feedback por período (regra por tempo de casa)
# ---------------------------------------------------------------------------

# Regras de periodicidade (em dias), conforme tempo de casa do colaborador.
PERIOD_RULES = [
    {'key': 'novato_15d', 'label': 'Novato (até 4 meses)', 'max_months': 4, 'period_days': 15, 'period_label': '15 em 15 dias'},
    {'key': 'novato_30d', 'label': 'Novato (5 a 12 meses)', 'max_months': 12, 'period_days': 30, 'period_label': '1 em 1 mês'},
    {'key': 'veterano_90d', 'label': 'Veterano (mais de 12 meses)', 'max_months': None, 'period_days': 90, 'period_label': '3 em 3 meses'},
]


def _tenure_months(user, today):
    """Meses de casa baseados em date_joined (data de criação do usuário)."""
    base = getattr(user, 'date_joined', None)
    if not base:
        return 0
    base_date = base.date() if hasattr(base, 'date') else base
    days = max((today - base_date).days, 0)
    return days / 30.44


def _rule_for_user(user, today):
    months = _tenure_months(user, today)
    for rule in PERIOD_RULES:
        if rule['max_months'] is None or months <= rule['max_months']:
            return rule, months
    return PERIOD_RULES[-1], months


@superadmin_required
def reports(request):
    today = timezone.localdate()
    sector_filter_id = request.GET.get('sector')
    status_filter = (request.GET.get('status') or '').strip().lower()  # '', 'ok', 'pending'

    # Universo: usuários ativos. Exclui superuser explicito do relatório.
    users_qs = (
        User.objects.filter(is_active=True)
        .select_related('sector')
        .order_by('first_name', 'last_name')
    )

    # Pré-carrega contagem de feedbacks por evaluatee dentro de janela máxima (90 dias).
    max_window_start = today - timezone.timedelta(days=90)
    recent_feedbacks = (
        Feedback.objects
        .filter(created_at__date__gte=max_window_start)
        .values('evaluatee_id', 'created_at')
    )
    feedbacks_by_user = {}
    for row in recent_feedbacks:
        feedbacks_by_user.setdefault(row['evaluatee_id'], []).append(row['created_at'].date() if hasattr(row['created_at'], 'date') else row['created_at'])

    # Última data de feedback por usuário (qualquer data, para mostrar referência).
    last_feedback_by_user = dict(
        Feedback.objects
        .values('evaluatee_id')
        .annotate(last=Max('created_at'))
        .values_list('evaluatee_id', 'last')
    )

    rows = []
    for u in users_qs:
        rule, months = _rule_for_user(u, today)
        period_start = today - timezone.timedelta(days=rule['period_days'])
        dates = feedbacks_by_user.get(u.id, [])
        in_period = [d for d in dates if d >= period_start]
        compliant = len(in_period) > 0
        last_dt = last_feedback_by_user.get(u.id)
        rows.append({
            'user': u,
            'sector': u.sector,
            'rule': rule,
            'tenure_months': round(months, 1),
            'period_start': period_start,
            'count_in_period': len(in_period),
            'compliant': compliant,
            'last_feedback': last_dt,
        })

    # Visão geral por setor (PDV).
    sectors_map = {}
    for r in rows:
        sec = r['sector']
        sec_key = sec.id if sec else 0
        bucket = sectors_map.setdefault(sec_key, {
            'sector': sec,
            'sector_name': sec.name if sec else 'Sem setor',
            'total': 0,
            'ok': 0,
            'pending': 0,
        })
        bucket['total'] += 1
        if r['compliant']:
            bucket['ok'] += 1
        else:
            bucket['pending'] += 1

    overview = sorted(
        sectors_map.values(),
        key=lambda b: (b['sector_name'] or '').lower(),
    )
    for b in overview:
        b['ok_pct'] = round((b['ok'] / b['total']) * 100, 1) if b['total'] else 0.0

    # Detalhamento por setor selecionado (ou todos quando sem filtro).
    detailed = rows
    selected_sector = None
    if sector_filter_id:
        try:
            sid = int(sector_filter_id)
            selected_sector = Sector.objects.filter(id=sid).first()
            detailed = [r for r in rows if (r['sector'].id if r['sector'] else 0) == sid]
        except (TypeError, ValueError):
            pass

    if status_filter == 'ok':
        detailed = [r for r in detailed if r['compliant']]
    elif status_filter == 'pending':
        detailed = [r for r in detailed if not r['compliant']]

    totals = {
        'total': len(rows),
        'ok': sum(1 for r in rows if r['compliant']),
        'pending': sum(1 for r in rows if not r['compliant']),
    }
    totals['ok_pct'] = round((totals['ok'] / totals['total']) * 100, 1) if totals['total'] else 0.0

    return render(request, 'feedback/reports.html', {
        'overview': overview,
        'detailed': detailed,
        'totals': totals,
        'rules': PERIOD_RULES,
        'today': today,
        'selected_sector': selected_sector,
        'status_filter': status_filter,
        'all_sectors': Sector.objects.all().order_by('name'),
    })


@login_required
def api_reminders(request):
    reminders = get_pending_reminders(request.user)
    return JsonResponse({'reminders': reminders, 'count': len(reminders)})


@login_required
@require_POST
def api_dismiss_reminder(request):
    key = (request.POST.get('key') or '').strip()
    if not key:
        return JsonResponse({'ok': False, 'error': 'key requerido'}, status=400)
    FeedbackReminderDismissal.objects.get_or_create(user=request.user, key=key)
    return JsonResponse({'ok': True})
