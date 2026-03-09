import csv
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import Sector, User

from .models import (
    ExperienciaAnswer,
    ExperienciaEvaluator,
    ExperienciaQuestion,
    ExperienciaTemplate,
    ExperienciaTodo,
)


def _is_gerente_or_superadmin(user):
    """Verifica se o usuário é gerente (no grupo 'Gerentes') ou superadmin."""
    if user.hierarchy == 'SUPERADMIN' or user.is_superuser:
        return True
    return user.groups.filter(name='Gerentes').exists()


def _is_superadmin(user):
    return user.hierarchy == 'SUPERADMIN' or user.is_superuser


def _is_evaluator_for(user, sector):
    """Verifica se o usuário é avaliador para o setor."""
    if _is_superadmin(user):
        return True
    return ExperienciaEvaluator.objects.filter(
        user=user, is_active=True, sectors=sector
    ).exists()


def _get_user_sectors(user):
    """Retorna os setores do usuário."""
    sectors = list(user.sectors.all())
    if user.sector and user.sector not in sectors:
        sectors.append(user.sector)
    return sectors


# ─── Dashboard ────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')

    is_superadmin = _is_superadmin(user)

    # To-dos do usuário (setores dele)
    user_sectors = _get_user_sectors(user)

    if is_superadmin:
        todos = ExperienciaTodo.objects.all()
    else:
        todos = ExperienciaTodo.objects.filter(sector__in=user_sectors)

    now = timezone.now()
    current_todos = todos.filter(month=now.month, year=now.year)
    pending_evaluation = todos.filter(status='enviado')

    # Verificar se é avaliador
    is_evaluator = is_superadmin or ExperienciaEvaluator.objects.filter(
        user=user, is_active=True
    ).exists()

    context = {
        'current_todos': current_todos.select_related('sector', 'template', 'launched_by'),
        'pending_evaluation': pending_evaluation.select_related('sector', 'template') if is_evaluator else [],
        'is_superadmin': is_superadmin,
        'is_evaluator': is_evaluator,
        'total_todos': todos.count(),
        'approved_todos': todos.filter(status='finalizado').count(),
    }
    return render(request, 'experiencia/dashboard.html', context)


# ─── Gestão de Templates (Perguntas) ──────────────────────────────────────────

@login_required
def template_list(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('experiencia:dashboard')

    templates = ExperienciaTemplate.objects.filter(is_active=True).select_related('created_by')
    if not _is_superadmin(user):
        templates = templates.filter(created_by=user)

    return render(request, 'experiencia/template_list.html', {
        'templates': templates,
        'is_superadmin': _is_superadmin(user),
    })


@login_required
def template_create(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('experiencia:dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        questions_text = request.POST.getlist('question_text')
        questions_points = request.POST.getlist('question_points')

        if not name:
            messages.error(request, 'O nome do template é obrigatório.')
            return redirect('experiencia:template_create')

        if not questions_text or not any(q.strip() for q in questions_text):
            messages.error(request, 'Adicione pelo menos uma pergunta.')
            return redirect('experiencia:template_create')

        template = ExperienciaTemplate.objects.create(
            name=name,
            description=description,
            created_by=user,
        )

        for i, (text, pts) in enumerate(zip(questions_text, questions_points)):
            text = text.strip()
            if text:
                try:
                    points = int(pts)
                except (ValueError, TypeError):
                    points = 0
                ExperienciaQuestion.objects.create(
                    template=template,
                    text=text,
                    order=i,
                    points=max(0, points),
                )

        messages.success(request, f'Template "{name}" criado com sucesso!')
        return redirect('experiencia:template_list')

    return render(request, 'experiencia/template_form.html', {
        'is_superadmin': _is_superadmin(user),
    })


@login_required
def template_edit(request, template_id):
    user = request.user
    template = get_object_or_404(ExperienciaTemplate, id=template_id, is_active=True)

    if not _is_superadmin(user) and template.created_by != user:
        messages.error(request, 'Você não tem permissão.')
        return redirect('experiencia:template_list')

    if request.method == 'POST':
        template.name = request.POST.get('name', '').strip() or template.name
        template.description = request.POST.get('description', '').strip()
        template.save()

        # Remove perguntas antigas e recria
        template.questions.all().delete()

        questions_text = request.POST.getlist('question_text')
        questions_points = request.POST.getlist('question_points')

        for i, (text, pts) in enumerate(zip(questions_text, questions_points)):
            text = text.strip()
            if text:
                try:
                    points = int(pts)
                except (ValueError, TypeError):
                    points = 0
                ExperienciaQuestion.objects.create(
                    template=template,
                    text=text,
                    order=i,
                    points=max(0, points),
                )

        messages.success(request, f'Template "{template.name}" atualizado!')
        return redirect('experiencia:template_list')

    return render(request, 'experiencia/template_form.html', {
        'template': template,
        'questions': template.questions.all(),
        'is_superadmin': _is_superadmin(user),
    })


@login_required
@require_POST
def template_delete(request, template_id):
    user = request.user
    template = get_object_or_404(ExperienciaTemplate, id=template_id)

    if not _is_superadmin(user) and template.created_by != user:
        messages.error(request, 'Você não tem permissão.')
        return redirect('experiencia:template_list')

    template.is_active = False
    template.save()
    messages.success(request, f'Template "{template.name}" removido.')
    return redirect('experiencia:template_list')


# ─── Lançar To-Do (Gestor seleciona template + mês + setor) ──────────────────

@login_required
def launch_todo(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('experiencia:dashboard')

    is_superadmin = _is_superadmin(user)

    if is_superadmin:
        templates = ExperienciaTemplate.objects.filter(is_active=True)
        sectors = Sector.objects.all().order_by('name')
    else:
        templates = ExperienciaTemplate.objects.filter(is_active=True, created_by=user)
        sectors = Sector.objects.filter(
            models.Q(users=user) | models.Q(primary_users=user)
        ).distinct().order_by('name')

    if request.method == 'POST':
        template_id = request.POST.get('template_id')
        sector_ids = request.POST.getlist('sector_ids')
        month = request.POST.get('month')
        year = request.POST.get('year')

        try:
            template = ExperienciaTemplate.objects.get(id=template_id, is_active=True)
            month = int(month)
            year = int(year)
        except (ExperienciaTemplate.DoesNotExist, ValueError, TypeError):
            messages.error(request, 'Dados inválidos.')
            return redirect('experiencia:launch_todo')

        if month < 1 or month > 12:
            messages.error(request, 'Mês inválido.')
            return redirect('experiencia:launch_todo')

        created_count = 0
        skipped_count = 0
        for sid in sector_ids:
            try:
                sector = Sector.objects.get(id=sid)
            except Sector.DoesNotExist:
                continue

            if ExperienciaTodo.objects.filter(
                template=template, sector=sector, month=month, year=year
            ).exists():
                skipped_count += 1
                continue

            ExperienciaTodo.objects.create(
                template=template,
                sector=sector,
                month=month,
                year=year,
                launched_by=user,
            )
            created_count += 1

        if created_count:
            messages.success(request, f'{created_count} to-do(s) lançado(s) com sucesso!')
        if skipped_count:
            messages.warning(request, f'{skipped_count} setor(es) já possuíam to-do para este mês/template.')

        return redirect('experiencia:dashboard')

    now = timezone.now()
    return render(request, 'experiencia/launch_todo.html', {
        'templates': templates,
        'sectors': sectors,
        'current_month': now.month,
        'current_year': now.year,
        'is_superadmin': is_superadmin,
    })


# ─── Preencher / Responder o To-Do ───────────────────────────────────────────

@login_required
def fill_todo(request, todo_id):
    user = request.user
    todo = get_object_or_404(
        ExperienciaTodo.objects.select_related('template', 'sector'),
        id=todo_id,
    )

    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('experiencia:dashboard')

    # Verificar se o usuário pertence ao setor (ou é superadmin)
    if not _is_superadmin(user):
        user_sectors = _get_user_sectors(user)
        if todo.sector not in user_sectors:
            messages.error(request, 'Você não pertence a este setor.')
            return redirect('experiencia:dashboard')

    if not todo.can_be_filled():
        messages.error(request, 'Este to-do não pode ser preenchido no momento.')
        return redirect('experiencia:dashboard')

    questions = todo.template.questions.all()
    existing_answers = {a.question_id: a for a in todo.answers.all()}

    # Se recusado parcialmente, filtrar só as recusadas
    if todo.status == 'recusado':
        rejected_ids = [
            a.question_id for a in todo.answers.filter(status='recusado')
        ]
        questions = questions.filter(id__in=rejected_ids)

    if request.method == 'POST':
        for question in questions:
            response = request.POST.get(f'response_{question.id}', 'nao')
            if response not in ('sim', 'nao', 'nao_se_aplica'):
                response = 'nao'
            observation = request.POST.get(f'observation_{question.id}', '').strip()
            photo = request.FILES.get(f'photo_{question.id}')

            answer, created = ExperienciaAnswer.objects.get_or_create(
                todo=todo,
                question=question,
                defaults={
                    'response': response,
                    'observation': observation,
                    'answered_by': user,
                    'answered_at': timezone.now(),
                    'status': 'pendente',
                },
            )
            if not created:
                answer.response = response
                answer.observation = observation
                answer.answered_by = user
                answer.answered_at = timezone.now()
                answer.status = 'pendente'
                answer.rejection_reason = ''

            if photo:
                answer.photo = photo

            answer.save()

        todo.status = 'enviado'
        todo.submitted_by = user
        todo.save()

        messages.success(request, 'To-do enviado para avaliação!')
        return redirect('experiencia:dashboard')

    # Preparar dados para o template
    questions_with_answers = []
    for q in questions:
        existing = existing_answers.get(q.id)
        questions_with_answers.append({
            'question': q,
            'answer': existing,
        })

    return render(request, 'experiencia/fill_todo.html', {
        'todo': todo,
        'questions_with_answers': questions_with_answers,
        'total_points': todo.template.total_points(),
    })


# ─── Visualizar To-Do (leitura) ──────────────────────────────────────────────

@login_required
def view_todo(request, todo_id):
    user = request.user
    todo = get_object_or_404(
        ExperienciaTodo.objects.select_related('template', 'sector', 'launched_by', 'submitted_by', 'evaluated_by'),
        id=todo_id,
    )

    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Sem permissão.')
        return redirect('experiencia:dashboard')

    answers = todo.answers.select_related('question', 'answered_by').all()
    applicable = [a for a in answers if a.response != 'nao_se_aplica']
    total_points = sum(a.question.points for a in applicable)
    approved_points = sum(
        a.question.points for a in applicable
        if a.response == 'sim' and a.status == 'aprovado'
    )

    return render(request, 'experiencia/view_todo.html', {
        'todo': todo,
        'answers': answers,
        'total_points': total_points,
        'approved_points': approved_points,
        'score': todo.score_percentage,
        'is_superadmin': _is_superadmin(user),
    })


# ─── Avaliar To-Do ───────────────────────────────────────────────────────────

@login_required
def evaluate_todo(request, todo_id):
    user = request.user
    todo = get_object_or_404(
        ExperienciaTodo.objects.select_related('template', 'sector'),
        id=todo_id,
        status='enviado',
    )

    if not _is_evaluator_for(user, todo.sector):
        messages.error(request, 'Você não é avaliador para este setor.')
        return redirect('experiencia:dashboard')

    answers = todo.answers.select_related('question', 'answered_by').all()

    if request.method == 'POST':
        has_rejection = False

        for answer in answers:
            action = request.POST.get(f'action_{answer.id}')
            if action == 'aprovar':
                answer.status = 'aprovado'
                answer.rejection_reason = ''
            elif action == 'recusar':
                answer.status = 'recusado'
                answer.rejection_reason = request.POST.get(
                    f'rejection_reason_{answer.id}', ''
                ).strip()
                has_rejection = True
            answer.save()

        todo.evaluated_by = user
        todo.evaluation_date = timezone.now()

        if has_rejection:
            todo.status = 'recusado'
            messages.warning(request, 'To-do devolvido com itens recusados.')
        else:
            todo.status = 'finalizado'
            messages.success(request, 'To-do aprovado com sucesso!')

        todo.update_score()
        todo.save()

        return redirect('experiencia:dashboard')

    return render(request, 'experiencia/evaluate_todo.html', {
        'todo': todo,
        'answers': answers,
        'total_points': todo.template.total_points(),
    })


# ─── Gerenciar Avaliadores (Superadmin) ──────────────────────────────────────

@login_required
def manage_evaluators(request):
    user = request.user
    if not _is_superadmin(user):
        messages.error(request, 'Apenas superadmins podem gerenciar avaliadores.')
        return redirect('experiencia:dashboard')

    evaluators = ExperienciaEvaluator.objects.filter(is_active=True).select_related('user')
    sectors = Sector.objects.all().order_by('name')
    users_list = User.objects.filter(
        is_active=True,
    ).exclude(
        hierarchy='PADRAO',
    ).order_by('first_name', 'last_name')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'add':
            user_id = request.POST.get('user_id')
            sector_ids = request.POST.getlist('sector_ids')
            try:
                eval_user = User.objects.get(id=user_id, is_active=True)
            except User.DoesNotExist:
                messages.error(request, 'Usuário não encontrado.')
                return redirect('experiencia:manage_evaluators')

            evaluator, created = ExperienciaEvaluator.objects.get_or_create(
                user=eval_user,
                defaults={'is_active': True},
            )
            if not evaluator.is_active:
                evaluator.is_active = True
                evaluator.save()

            if sector_ids:
                evaluator.sectors.set(Sector.objects.filter(id__in=sector_ids))
            else:
                evaluator.sectors.clear()

            messages.success(request, f'Avaliador {eval_user.get_full_name()} configurado!')

        elif action == 'remove':
            evaluator_id = request.POST.get('evaluator_id')
            try:
                evaluator = ExperienciaEvaluator.objects.get(id=evaluator_id)
                evaluator.is_active = False
                evaluator.save()
                messages.success(request, 'Avaliador removido.')
            except ExperienciaEvaluator.DoesNotExist:
                messages.error(request, 'Avaliador não encontrado.')

        return redirect('experiencia:manage_evaluators')

    return render(request, 'experiencia/manage_evaluators.html', {
        'evaluators': evaluators,
        'sectors': sectors,
        'users_list': users_list,
    })


# ─── Relatórios ──────────────────────────────────────────────────────────────

@login_required
def reports(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Sem permissão.')
        return redirect('experiencia:dashboard')

    is_superadmin = _is_superadmin(user)

    # Filtros
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')
    sector_filter = request.GET.get('sector')
    status_filter = request.GET.get('status')

    todos = ExperienciaTodo.objects.select_related(
        'template', 'sector', 'launched_by', 'submitted_by', 'evaluated_by'
    )

    if not is_superadmin:
        user_sectors = _get_user_sectors(user)
        todos = todos.filter(sector__in=user_sectors)

    if month_filter:
        try:
            todos = todos.filter(month=int(month_filter))
        except ValueError:
            pass
    if year_filter:
        try:
            todos = todos.filter(year=int(year_filter))
        except ValueError:
            pass
    if sector_filter:
        todos = todos.filter(sector_id=sector_filter)
    if status_filter:
        todos = todos.filter(status=status_filter)

    # Estatísticas
    total = todos.count()
    finalized = todos.filter(status='finalizado').count()
    avg_score = todos.filter(status='finalizado').aggregate(
        avg=models.Avg('score_percentage')
    )['avg'] or 0

    sectors = Sector.objects.all().order_by('name') if is_superadmin else Sector.objects.filter(
        id__in=[s.id for s in _get_user_sectors(user)]
    ).order_by('name')

    now = timezone.now()

    context = {
        'todos': todos.order_by('-year', '-month', 'sector__name'),
        'sectors': sectors,
        'total': total,
        'finalized': finalized,
        'avg_score': round(avg_score, 1),
        'is_superadmin': is_superadmin,
        'current_month': now.month,
        'current_year': now.year,
        'filter_month': month_filter or '',
        'filter_year': year_filter or '',
        'filter_sector': sector_filter or '',
        'filter_status': status_filter or '',
    }
    return render(request, 'experiencia/reports.html', context)


@login_required
def export_report(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        return HttpResponse(status=403)

    is_superadmin = _is_superadmin(user)

    todos = ExperienciaTodo.objects.select_related(
        'template', 'sector', 'launched_by', 'submitted_by', 'evaluated_by'
    )
    if not is_superadmin:
        user_sectors = _get_user_sectors(user)
        todos = todos.filter(sector__in=user_sectors)

    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')
    sector_filter = request.GET.get('sector')
    status_filter = request.GET.get('status')

    if month_filter:
        try:
            todos = todos.filter(month=int(month_filter))
        except ValueError:
            pass
    if year_filter:
        try:
            todos = todos.filter(year=int(year_filter))
        except ValueError:
            pass
    if sector_filter:
        todos = todos.filter(sector_id=sector_filter)
    if status_filter:
        todos = todos.filter(status=status_filter)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="experiencia_vivo_relatorio.csv"'
    response.write('\ufeff')  # BOM for Excel

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Setor', 'Mês/Ano', 'Template', 'Status', 'Pontuação (%)',
        'Lançado por', 'Enviado por', 'Avaliado por', 'Data Avaliação',
    ])

    for todo in todos.order_by('-year', '-month'):
        writer.writerow([
            todo.sector.name,
            f"{todo.month:02d}/{todo.year}",
            todo.template.name,
            todo.get_status_display(),
            f"{todo.score_percentage:.1f}",
            todo.launched_by.get_full_name() if todo.launched_by else '',
            todo.submitted_by.get_full_name() if todo.submitted_by else '',
            todo.evaluated_by.get_full_name() if todo.evaluated_by else '',
            todo.evaluation_date.strftime('%d/%m/%Y %H:%M') if todo.evaluation_date else '',
        ])

    return response


# ─── Histórico / Arquivo ─────────────────────────────────────────────────────

@login_required
def archive(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Sem permissão.')
        return redirect('experiencia:dashboard')

    is_superadmin = _is_superadmin(user)

    if is_superadmin:
        todos = ExperienciaTodo.objects.all()
    else:
        user_sectors = _get_user_sectors(user)
        todos = ExperienciaTodo.objects.filter(sector__in=user_sectors)

    todos = todos.select_related(
        'template', 'sector', 'launched_by'
    ).order_by('-year', '-month', 'sector__name')

    return render(request, 'experiencia/archive.html', {
        'todos': todos,
        'is_superadmin': is_superadmin,
    })


# ─── API Upload de Foto (para captura de câmera via JS) ──────────────────────

@login_required
@require_POST
def api_upload_photo(request, answer_id):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        return JsonResponse({'error': 'Sem permissão'}, status=403)

    answer = get_object_or_404(ExperienciaAnswer, id=answer_id)
    photo = request.FILES.get('photo')

    if not photo:
        return JsonResponse({'error': 'Nenhuma foto enviada'}, status=400)

    answer.photo = photo
    answer.save()

    return JsonResponse({'success': True, 'url': answer.photo.url})
