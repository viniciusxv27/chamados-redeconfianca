from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import datetime, timedelta, date
import json

from .models import ChecklistTemplate, ChecklistTask, ChecklistAssignment, ChecklistExecution, ChecklistTaskExecution
from users.models import User, Sector


@login_required
def checklist_dashboard(request):
    """Dashboard principal dos checklists"""
    user = request.user
    
    # Checklists atribuídos ao usuário
    my_assignments = ChecklistAssignment.objects.filter(
        assigned_to=user,
        is_active=True
    ).select_related('template', 'assigned_by')
    
    # Execuções pendentes de hoje
    today = timezone.now().date()
    today_executions = ChecklistExecution.objects.filter(
        assignment__assigned_to=user,
        execution_date=today
    ).select_related('assignment__template')
    
    # Estatísticas
    stats = {
        'total_assignments': my_assignments.count(),
        'today_pending': today_executions.filter(status='pending').count(),
        'today_completed': today_executions.filter(status='completed').count(),
        'overdue': ChecklistExecution.objects.filter(
            assignment__assigned_to=user,
            status='overdue'
        ).count()
    }
    
    # Templates disponíveis para criação (usuário do mesmo setor)
    available_templates = []
    if user.sector:
        available_templates = ChecklistTemplate.objects.filter(
            sector=user.sector,
            is_active=True
        ).prefetch_related('tasks')
    
    context = {
        'my_assignments': my_assignments[:5],  # Primeiros 5
        'today_executions': today_executions,
        'stats': stats,
        'available_templates': available_templates,
    }
    return render(request, 'checklists/dashboard.html', context)


@login_required
def create_assignment(request):
    """Criar nova atribuição de checklist"""
    if request.method == 'POST':
        template_id = request.POST.get('template_id')
        assigned_to_id = request.POST.get('assigned_to')
        schedule_type = request.POST.get('schedule_type')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        custom_dates_str = request.POST.get('custom_dates', '[]')
        
        # Validações
        if not all([template_id, assigned_to_id, schedule_type, start_date, end_date]):
            messages.error(request, 'Todos os campos obrigatórios devem ser preenchidos.')
            return redirect('checklists:create_assignment')
        
        try:
            template = get_object_or_404(ChecklistTemplate, id=template_id)
            assigned_to = get_object_or_404(User, id=assigned_to_id)
            
            # Verificar se o usuário pode atribuir para este setor
            if not request.user.sector or request.user.sector != template.sector:
                messages.error(request, 'Você só pode criar checklists do seu setor.')
                return redirect('checklists:dashboard')
            
            # Processar datas personalizadas
            custom_dates = []
            if schedule_type == 'custom':
                try:
                    custom_dates = json.loads(custom_dates_str)
                except (json.JSONDecodeError, TypeError):
                    custom_dates = []
            
            # Criar atribuição
            assignment = ChecklistAssignment.objects.create(
                template=template,
                assigned_to=assigned_to,
                assigned_by=request.user,
                schedule_type=schedule_type,
                start_date=datetime.strptime(start_date, '%Y-%m-%d').date(),
                end_date=datetime.strptime(end_date, '%Y-%m-%d').date(),
                custom_dates=custom_dates
            )
            
            # Criar execuções para as datas ativas
            create_executions_for_assignment(assignment)
            
            messages.success(request, f'Checklist atribuído para {assigned_to.get_full_name()} com sucesso!')
            return redirect('checklists:dashboard')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar atribuição: {str(e)}')
            return redirect('checklists:create_assignment')
    
    # GET - mostrar formulário
    user_sector = request.user.sector
    if not user_sector:
        messages.error(request, 'Você precisa estar em um setor para criar checklists.')
        return redirect('checklists:dashboard')
    
    # Templates do setor do usuário
    templates = ChecklistTemplate.objects.filter(
        sector=user_sector,
        is_active=True
    ).prefetch_related('tasks')
    
    # Usuários para atribuição
    sector_users = User.objects.filter(
        is_active=True
    ).exclude(id=request.user.id).order_by('first_name', 'last_name')
    
    context = {
        'templates': templates,
        'users': sector_users,
    }
    return render(request, 'checklists/create_assignment.html', context)


def create_executions_for_assignment(assignment):
    """Cria as execuções baseadas nas datas ativas da atribuição"""
    active_dates = assignment.get_active_dates()
    
    for exec_date in active_dates:
        # Verificar se já existe execução para esta data
        execution, created = ChecklistExecution.objects.get_or_create(
            assignment=assignment,
            execution_date=exec_date,
            defaults={'status': 'pending'}
        )
        
        if created:
            # Criar execuções das tarefas
            for task in assignment.template.tasks.all():
                ChecklistTaskExecution.objects.create(
                    execution=execution,
                    task=task
                )


@login_required
def execute_checklist(request, assignment_id):
    """Executar checklist"""
    from datetime import date
    
    assignment = get_object_or_404(
        ChecklistAssignment,
        id=assignment_id,
        assigned_to=request.user
    )
    
    # Data de execução (hoje por padrão, ou a data selecionada)
    execution_date = request.GET.get('date')
    if execution_date:
        execution_date = datetime.strptime(execution_date, '%Y-%m-%d').date()
    else:
        execution_date = date.today()
    
    # Buscar ou criar execução
    execution, created = ChecklistExecution.objects.get_or_create(
        assignment=assignment,
        execution_date=execution_date,
        defaults={'status': 'pending'}
    )
    
    if created:
        # Criar execuções das tarefas
        for task in assignment.template.tasks.all():
            ChecklistTaskExecution.objects.create(
                execution=execution,
                task=task
            )
    
    if request.method == 'POST':
        # Marcar início se ainda não foi iniciado
        if not execution.started_at:
            execution.started_at = timezone.now()
            execution.status = 'in_progress'
            execution.save()
        
        # Processar tarefas
        for task in assignment.template.tasks.all():
            task_key = f'task_{task.id}'
            notes_key = f'observation_{task.id}'
            
            is_completed = request.POST.get(task_key) == 'on'
            notes = request.POST.get(notes_key, '')
            
            # Buscar ou criar task execution
            task_execution, _ = ChecklistTaskExecution.objects.get_or_create(
                execution=execution,
                task=task
            )
            
            if is_completed and not task_execution.is_completed:
                task_execution.complete_task(notes)
            elif not is_completed and task_execution.is_completed:
                task_execution.is_completed = False
                task_execution.completed_at = None
                task_execution.notes = notes
                task_execution.save()
            else:
                task_execution.notes = notes
                task_execution.save()
        
        # Verificar se todas as tarefas obrigatórias foram concluídas
        pending_required = execution.task_executions.filter(
            task__is_required=True,
            is_completed=False
        ).exists()
        
        if not pending_required and not execution.completed_at:
            execution.completed_at = timezone.now()
            execution.status = 'completed'
        
        execution.save()
        
        messages.success(request, 'Progresso salvo com sucesso!')
        return redirect('execute_checklist', assignment_id=assignment.id)
    
    # Atualizar status baseado na data
    execution.update_status()
    
    # Buscar task executions existentes
    completed_task_ids = [
        te.task.id for te in execution.task_executions.filter(is_completed=True)
    ]
    
    task_observations = {
        te.task.id: te.notes for te in execution.task_executions.all()
    }
    
    context = {
        'assignment': assignment,
        'execution': execution,
        'execution_date': execution_date,
        'completed_task_ids': completed_task_ids,
        'task_observations': task_observations,
    }
    return render(request, 'checklists/execute.html', context)


@login_required
def my_checklists(request):
    """Listar todos os checklists do usuário"""
    user = request.user
    
    # Filtros
    status_filter = request.GET.get('status', '')
    schedule_type_filter = request.GET.get('schedule_type', '')
    date_from_filter = request.GET.get('date_from', '')
    
    # Assignments do usuário
    assignments = ChecklistAssignment.objects.filter(
        assigned_to=user,
        is_active=True
    ).select_related('template', 'assigned_by').order_by('-created_at')
    
    # Aplicar filtros
    if status_filter:
        assignments = [a for a in assignments if a.get_status() == status_filter]
    
    if schedule_type_filter:
        assignments = assignments.filter(schedule_type=schedule_type_filter)
    
    if date_from_filter:
        try:
            filter_date = datetime.strptime(date_from_filter, '%Y-%m-%d').date()
            assignments = assignments.filter(start_date__gte=filter_date)
        except ValueError:
            pass
    
    # Se aplicou filtro de status, converter de volta para queryset
    if status_filter:
        assignment_ids = [a.id for a in assignments]
        assignments = ChecklistAssignment.objects.filter(
            id__in=assignment_ids
        ).select_related('template', 'assigned_by').order_by('-created_at')
    
    # Estatísticas
    all_assignments = ChecklistAssignment.objects.filter(
        assigned_to=user,
        is_active=True
    )
    
    stats = {
        'total': all_assignments.count(),
        'pending': len([a for a in all_assignments if a.get_status() == 'pending']),
        'in_progress': len([a for a in all_assignments if a.get_status() == 'in_progress']),
        'completed': len([a for a in all_assignments if a.get_status() == 'completed']),
    }
    
    # Paginação
    paginator = Paginator(assignments, 12)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'assignments': page_obj,
        'stats': stats,
        'status_filter': status_filter,
        'schedule_type_filter': schedule_type_filter,
        'date_from_filter': date_from_filter,
        'is_paginated': page_obj.has_other_pages(),
        'page_obj': page_obj,
    }
    return render(request, 'checklists/my_checklists.html', context)


@login_required
def api_get_template_details(request, template_id):
    """API para buscar detalhes de um template"""
    try:
        template = get_object_or_404(ChecklistTemplate, id=template_id)
        
        # Verificar permissão
        if request.user.sector != template.sector:
            return JsonResponse({'error': 'Sem permissão'}, status=403)
        
        tasks = [
            {
                'id': task.id,
                'title': task.title,
                'description': task.description,
                'is_required': task.is_required,
                'order': task.order
            }
            for task in template.tasks.all()
        ]
        
        data = {
            'id': template.id,
            'name': template.name,
            'description': template.description,
            'tasks': tasks
        }
        
        return JsonResponse(data)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def api_search_users(request):
    """API para buscar usuários"""
    query = request.GET.get('q', '').strip()
    
    if not query or len(query) < 2:
        return JsonResponse({'users': []})
    
    users = User.objects.filter(
        Q(first_name__icontains=query) |
        Q(last_name__icontains=query) |
        Q(username__icontains=query) |
        Q(email__icontains=query),
        is_active=True
    ).exclude(id=request.user.id)[:10]
    
    users_data = [
        {
            'id': user.id,
            'name': user.get_full_name() or user.username,
            'email': user.email,
            'sector': user.sector.name if user.sector else 'Sem setor'
        }
        for user in users
    ]
    
    return JsonResponse({'users': users_data})