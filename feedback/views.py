from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from users.models import User

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

    return render(request, 'feedback/detail.html', {
        'fb': fb,
        'show_ai': show_ai,
        'ai_text': ai_text,
        'previous': fb.previous_feedback(),
        'evolution_delta': fb.evolution_delta(),
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
