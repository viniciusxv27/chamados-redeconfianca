from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import datetime, timedelta, date
from itertools import chain
import json

from .models import ChecklistTemplate, ChecklistTask, ChecklistAssignment, ChecklistExecution, ChecklistTaskExecution
from users.models import User, Sector


def has_checklist_admin_permission(user):
    """Verifica se o usuário tem permissão para administrar checklists"""
    if user.is_superuser:
        return True
    
    if hasattr(user, 'hierarchy') and user.hierarchy:
        return user.hierarchy in ['SUPERADMIN', 'ADMIN', 'ADMINISTRATIVO', 'SUPERVISOR']
    
    return False


@login_required
def checklist_dashboard(request):
    """Dashboard principal dos checklists"""
    user = request.user
    
    # Verificar se é supervisor ou hierarquia maior
    is_supervisor = user.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO'] or user.is_superuser
    
    # Checklists atribuídos ao usuário
    my_assignments = ChecklistAssignment.objects.filter(
        assigned_to=user,
        is_active=True
    ).select_related('template', 'assigned_by')
    
    # Para supervisores: também mostrar checklists que eles atribuíram
    assigned_by_me = ChecklistAssignment.objects.none()
    if is_supervisor:
        assigned_by_me = ChecklistAssignment.objects.filter(
            assigned_by=user,
            is_active=True
        ).exclude(
            assigned_to=user  # Não duplicar os que já estão em my_assignments
        ).select_related('template', 'assigned_to', 'assigned_by')
    
    # Execuções pendentes de hoje
    today = timezone.now().date()
    today_executions = ChecklistExecution.objects.filter(
        assignment__assigned_to=user,
        execution_date=today
    ).select_related('assignment__template')
    
    # Para supervisores: também incluir execuções dos checklists que atribuíram
    if is_supervisor:
        supervisor_executions = ChecklistExecution.objects.filter(
            assignment__assigned_by=user,
            execution_date=today
        ).exclude(
            assignment__assigned_to=user  # Não duplicar
        ).select_related('assignment__template', 'assignment__assigned_to')
        
        # Combinar as querysets
        from itertools import chain
        today_executions = list(chain(today_executions, supervisor_executions))
    
    # Separar por período e status
    if isinstance(today_executions, list):
        # Se é lista (combinada), filtrar manualmente
        pending_morning = [e for e in today_executions if e.period == 'morning' and e.status in ['pending', 'in_progress']]
        pending_afternoon = [e for e in today_executions if e.period == 'afternoon' and e.status in ['pending', 'in_progress']]
        completed_morning = [e for e in today_executions if e.period == 'morning' and e.status in ['completed', 'awaiting_approval']]
        completed_afternoon = [e for e in today_executions if e.period == 'afternoon' and e.status in ['completed', 'awaiting_approval']]
    else:
        # Se é queryset, usar filter
        pending_morning = today_executions.filter(
            period='morning',
            status__in=['pending', 'in_progress']
        )
        pending_afternoon = today_executions.filter(
            period='afternoon',
            status__in=['pending', 'in_progress']
        )
        completed_morning = today_executions.filter(
            period='morning',
            status__in=['completed', 'awaiting_approval']
        )
        completed_afternoon = today_executions.filter(
            period='afternoon',
            status__in=['completed', 'awaiting_approval']
        )
    
    # Estatísticas
    if isinstance(today_executions, list):
        total_pending = len([e for e in today_executions if e.status in ['pending', 'in_progress']])
        total_completed = len([e for e in today_executions if e.status in ['completed', 'awaiting_approval']])
    else:
        total_pending = today_executions.filter(status__in=['pending', 'in_progress']).count()
        total_completed = today_executions.filter(status__in=['completed', 'awaiting_approval']).count()
    
    stats = {
        'total_assignments': my_assignments.count() + (assigned_by_me.count() if is_supervisor else 0),
        'today_pending': total_pending,
        'today_completed': total_completed,
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
    
    # Execuções do calendário (mês atual + próximo mês + mês anterior)
    current_month = today.replace(day=1)
    next_month = (current_month + timedelta(days=32)).replace(day=1)
    previous_month = (current_month - timedelta(days=1)).replace(day=1)
    
    calendar_start = previous_month
    calendar_end = next_month.replace(day=28) + timedelta(days=4)  # garante fim do próximo mês
    calendar_end = calendar_end.replace(day=1) - timedelta(days=1)  # último dia do próximo mês
    
    calendar_executions = ChecklistExecution.objects.filter(
        assignment__assigned_to=user,
        execution_date__gte=calendar_start,
        execution_date__lte=calendar_end
    ).select_related('assignment__template').order_by('execution_date')
    
    # Para supervisores: também incluir execuções dos checklists que atribuíram no calendário
    if is_supervisor:
        supervisor_calendar_executions = ChecklistExecution.objects.filter(
            assignment__assigned_by=user,
            execution_date__gte=calendar_start,
            execution_date__lte=calendar_end
        ).exclude(
            assignment__assigned_to=user  # Não duplicar
        ).select_related('assignment__template', 'assignment__assigned_to').order_by('execution_date')
        
        # Combinar as querysets
        calendar_executions = list(chain(calendar_executions, supervisor_calendar_executions))
        # Ordenar manualmente por execution_date
        calendar_executions.sort(key=lambda x: x.execution_date)
    
    context = {
        'my_assignments': my_assignments[:5],  # Primeiros 5
        'assigned_by_me': assigned_by_me[:5] if is_supervisor else [],  # Primeiros 5 atribuídos por mim
        'today_executions': today_executions,
        'pending_morning': pending_morning,
        'pending_afternoon': pending_afternoon,
        'completed_morning': completed_morning,
        'completed_afternoon': completed_afternoon,
        'stats': stats,
        'available_templates': available_templates,
        'calendar_executions': calendar_executions,
        'is_supervisor': is_supervisor,
    }
    return render(request, 'checklists/dashboard.html', context)


@login_required
def create_assignment(request):
    """Criar nova atribuição de checklist"""
    if request.method == 'POST':
        template_id = request.POST.get('template_id')
        assignment_type = request.POST.get('assignment_type', 'user')  # 'user' ou 'group'
        assigned_to_id = request.POST.get('assigned_to')
        group_id = request.POST.get('group_id')
        schedule_type = request.POST.get('schedule_type')
        period = request.POST.get('period', 'both')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        custom_dates_str = request.POST.get('custom_dates', '[]')
        
        # Validações básicas
        if not all([template_id, schedule_type]):
            messages.error(request, 'Todos os campos obrigatórios devem ser preenchidos.')
            return redirect('checklists:create_assignment')
        
        # Validar datas baseado no tipo de agendamento
        if schedule_type == 'custom':
            # Modo personalizado: validar custom_dates
            try:
                custom_dates = json.loads(custom_dates_str)
                if not custom_dates:
                    messages.error(request, 'Selecione pelo menos uma data no calendário.')
                    return redirect('checklists:create_assignment')
            except (json.JSONDecodeError, TypeError):
                messages.error(request, 'Erro ao processar datas personalizadas.')
                return redirect('checklists:create_assignment')
        else:
            # Outros modos: validar start_date e end_date
            if not all([start_date, end_date]):
                messages.error(request, 'Informe a data de início e fim.')
                return redirect('checklists:create_assignment')
        
        try:
            template = get_object_or_404(ChecklistTemplate, id=template_id)
            
            # Determinar usuários a atribuir
            users_to_assign = []
            
            if assignment_type == 'group' and group_id:
                # Atribuir para grupo
                from communications.models import CommunicationGroup
                group = get_object_or_404(CommunicationGroup, id=group_id)
                users_to_assign = list(group.members.filter(is_active=True))
                
                if not users_to_assign:
                    messages.error(request, 'O grupo selecionado não possui membros ativos.')
                    return redirect('checklists:create_assignment')
                    
            elif assignment_type == 'user' and assigned_to_id:
                # Atribuir para usuário específico
                user = get_object_or_404(User, id=assigned_to_id)
                users_to_assign = [user]
            else:
                messages.error(request, 'Selecione um usuário ou grupo válido.')
                return redirect('checklists:create_assignment')
            
            # Verificar permissão - usuário pode atribuir templates de qualquer setor que pertence
            user_sectors = list(request.user.sectors.all())
            if request.user.sector:
                user_sectors.append(request.user.sector)
            
            if template.sector not in user_sectors:
                if not has_checklist_admin_permission(request.user):
                    messages.error(request, 'Você só pode criar checklists dos seus setores.')
                    return redirect('checklists:dashboard')
            
            # Processar datas personalizadas
            custom_dates = []
            if schedule_type == 'custom':
                try:
                    custom_dates = json.loads(custom_dates_str)
                except (json.JSONDecodeError, TypeError):
                    custom_dates = []
            
            # Criar atribuições para cada usuário
            assignments_created = 0
            for user in users_to_assign:
                # Para schedule_type 'custom', usar primeira e última data do array
                if schedule_type == 'custom' and custom_dates:
                    start_date_obj = datetime.strptime(custom_dates[0], '%Y-%m-%d').date()
                    end_date_obj = datetime.strptime(custom_dates[-1], '%Y-%m-%d').date()
                else:
                    start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
                    end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
                
                assignment = ChecklistAssignment.objects.create(
                    template=template,
                    assigned_to=user,
                    assigned_by=request.user,
                    schedule_type=schedule_type,
                    period=period,
                    start_date=start_date_obj,
                    end_date=end_date_obj,
                    custom_dates=custom_dates
                )
                
                # Criar execuções para as datas ativas
                create_executions_for_assignment(assignment)
                assignments_created += 1
            
            if assignment_type == 'group':
                messages.success(request, f'✅ Checklist atribuído para {assignments_created} usuário(s) do grupo com sucesso!')
            else:
                messages.success(request, f'✅ Checklist atribuído para {users_to_assign[0].get_full_name()} com sucesso!')
                
            return redirect('checklists:dashboard')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar atribuição: {str(e)}')
            return redirect('checklists:create_assignment')
    
    # GET - mostrar formulário
    # Obter todos os setores do usuário (principal + secundários)
    user_sectors = list(request.user.sectors.all())
    if request.user.sector:
        user_sectors.append(request.user.sector)
    
    # Remover duplicatas
    user_sectors = list(set(user_sectors))
    
    if not user_sectors:
        messages.error(request, 'Você precisa estar em um setor para criar checklists.')
        return redirect('checklists:dashboard')
    
    # Templates de TODOS os setores do usuário
    templates = ChecklistTemplate.objects.filter(
        sector__in=user_sectors,
        is_active=True
    ).prefetch_related('tasks').order_by('sector__name', 'name')
    
    # Usuários para atribuição
    sector_users = User.objects.filter(
        is_active=True
    ).exclude(id=request.user.id).order_by('first_name', 'last_name')
    
    # Grupos disponíveis
    from communications.models import CommunicationGroup
    groups = CommunicationGroup.objects.filter(is_active=True).prefetch_related('members').order_by('name')
    
    context = {
        'templates': templates,
        'users': sector_users,
        'groups': groups,
    }
    return render(request, 'checklists/create_assignment.html', context)


def create_executions_for_assignment(assignment):
    """Cria as execuções baseadas nas datas ativas da atribuição"""
    active_dates = assignment.get_active_dates()
    
    # Determinar períodos a criar
    if assignment.period == 'both':
        periods = ['morning', 'afternoon']
    else:
        periods = [assignment.period]
    
    for exec_date in active_dates:
        for period in periods:
            # Verificar se já existe execução para esta data e período
            execution, created = ChecklistExecution.objects.get_or_create(
                assignment=assignment,
                execution_date=exec_date,
                period=period,
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
def execute_today_checklists(request):
    """Executar todos os checklists de hoje em um único formulário"""
    user = request.user
    today = timezone.now().date()
    
    # Buscar todas as execuções de hoje do usuário
    today_executions = ChecklistExecution.objects.filter(
        assignment__assigned_to=user,
        execution_date=today,
        status__in=['pending', 'in_progress']
    ).select_related(
        'assignment__template'
    ).prefetch_related(
        'task_executions__task'
    ).order_by('period', 'assignment__template__name')
    
    if request.method == 'POST':
        # Processar submissão de todos os checklists
        all_completed = True
        
        for execution in today_executions:
            # Marcar início se ainda não foi iniciado
            if not execution.started_at:
                execution.started_at = timezone.now()
                execution.status = 'in_progress'
            
            # Processar tarefas deste checklist
            execution_completed = True
            for task_exec in execution.task_executions.all():
                task_key = f'task_{execution.id}_{task_exec.task.id}'
                notes_key = f'notes_{execution.id}_{task_exec.task.id}'
                image_key = f'evidence_image_{execution.id}_{task_exec.task.id}'
                video_key = f'evidence_video_{execution.id}_{task_exec.task.id}'
                
                is_completed = request.POST.get(task_key) == 'on'
                notes = request.POST.get(notes_key, '')
                evidence_image = request.FILES.get(image_key)
                evidence_video = request.FILES.get(video_key)
                
                # Atualizar task execution
                task_exec.is_completed = is_completed
                task_exec.notes = notes
                
                if evidence_image:
                    task_exec.evidence_image = evidence_image
                if evidence_video:
                    task_exec.evidence_video = evidence_video
                
                if is_completed and not task_exec.completed_at:
                    task_exec.completed_at = timezone.now()
                
                task_exec.save()
                
                # Verificar se todas as tarefas obrigatórias estão completas
                if task_exec.task.is_required and not is_completed:
                    execution_completed = False
            
            # Atualizar status da execução
            if execution_completed:
                execution.completed_at = timezone.now()
                execution.submitted_at = timezone.now()
                execution.status = 'awaiting_approval'
            else:
                all_completed = False
                execution.status = 'in_progress'
            
            execution.save()
        
        if all_completed:
            messages.success(request, '✅ Todos os checklists foram enviados para aprovação!')
        else:
            messages.info(request, '💾 Progresso salvo! Complete todas as tarefas para enviar.')
        
        return redirect('checklists:today_checklists')
    
    # GET - Mostrar formulário
    # Calcular progresso geral
    total_tasks = 0
    completed_tasks = 0
    
    # Adicionar contador de tarefas para cada execução
    for execution in today_executions:
        execution.total_tasks_count = 0
        execution.completed_tasks_count = 0
        for task_exec in execution.task_executions.all():
            execution.total_tasks_count += 1
            total_tasks += 1
            if task_exec.is_completed:
                execution.completed_tasks_count += 1
                completed_tasks += 1
    
    progress_percentage = round((completed_tasks / total_tasks * 100)) if total_tasks > 0 else 0
    
    context = {
        'today_executions': today_executions,
        'total_tasks': total_tasks,
        'completed_tasks': completed_tasks,
        'progress_percentage': progress_percentage,
        'today': today,
    }
    
    return render(request, 'checklists/today_checklists.html', context)


@login_required
def execute_checklist(request, assignment_id):
    """Executar checklist individual (view antiga - redirecionar para nova)"""
    from datetime import date
    
    assignment = get_object_or_404(
        ChecklistAssignment,
        id=assignment_id,
        assigned_to=request.user
    )
    
    # Redirecionar para a nova view unificada
    messages.info(request, '📋 Use o formulário unificado "Checklists de Hoje" para executar todos os seus checklists.')
    return redirect('checklists:today_checklists')


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


@login_required
def api_group_members(request, group_id):
    """API para buscar membros de um grupo"""
    from communications.models import CommunicationGroup
    
    try:
        group = get_object_or_404(CommunicationGroup, id=group_id, is_active=True)
        members = group.members.filter(is_active=True).order_by('first_name', 'last_name')
        
        members_data = [{
            'id': member.id,
            'name': member.get_full_name() or member.username,
            'email': member.email,
            'sector': member.sector.name if member.sector else 'Sem setor'
        } for member in members]
        
        return JsonResponse({
            'group_name': group.name,
            'members': members_data,
            'total': members.count()
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


# ===== ADMIN - TEMPLATES =====@login_required
def admin_templates(request):
    """Administração de templates (apenas para admins)"""
    if not has_checklist_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('checklists:dashboard')
    
    # Filtros
    sector_filter = request.GET.get('sector', '')
    status_filter = request.GET.get('status', '')
    
    # Buscar templates
    templates = ChecklistTemplate.objects.all().select_related('sector', 'created_by')
    
    if sector_filter:
        templates = templates.filter(sector_id=sector_filter)
    
    if status_filter == 'active':
        templates = templates.filter(is_active=True)
    elif status_filter == 'inactive':
        templates = templates.filter(is_active=False)
    
    templates = templates.order_by('-created_at')
    
    # Setores para o filtro
    sectors = Sector.objects.all().order_by('name')
    
    # Estatísticas
    stats = {
        'total': ChecklistTemplate.objects.count(),
        'active': ChecklistTemplate.objects.filter(is_active=True).count(),
        'inactive': ChecklistTemplate.objects.filter(is_active=False).count(),
    }
    
    # Paginação
    paginator = Paginator(templates, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'templates': page_obj,
        'sectors': sectors,
        'stats': stats,
        'sector_filter': sector_filter,
        'status_filter': status_filter,
        'is_paginated': page_obj.has_other_pages(),
        'page_obj': page_obj,
    }
    return render(request, 'checklists/admin_templates.html', context)


@login_required
def create_template(request):
    """Criar novo template de checklist (apenas para admins)"""
    if not has_checklist_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('checklists:dashboard')
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        sector_id = request.POST.get('sector')
        
        # Validações
        if not name or not sector_id:
            messages.error(request, 'Nome e setor são obrigatórios.')
            return redirect('checklists:create_template')
        
        try:
            sector = get_object_or_404(Sector, id=sector_id)
            
            # Criar template
            template = ChecklistTemplate.objects.create(
                name=name,
                description=description,
                sector=sector,
                created_by=request.user
            )
            
            # Processar tarefas
            task_titles = request.POST.getlist('task_title[]')
            task_descriptions = request.POST.getlist('task_description[]')
            task_required = request.POST.getlist('task_required[]')
            
            # Arquivos de instrução
            task_images = request.FILES.getlist('task_image[]')
            task_videos = request.FILES.getlist('task_video[]')
            task_documents = request.FILES.getlist('task_document[]')
            
            for i, title in enumerate(task_titles):
                if title.strip():
                    task = ChecklistTask.objects.create(
                        template=template,
                        title=title.strip(),
                        description=task_descriptions[i].strip() if i < len(task_descriptions) else '',
                        is_required=str(i) in task_required,
                        order=i
                    )
                    
                    # Adicionar imagem se fornecida
                    if i < len(task_images) and task_images[i]:
                        task.instruction_image = task_images[i]
                    
                    # Adicionar vídeo se fornecido
                    if i < len(task_videos) and task_videos[i]:
                        task.instruction_video = task_videos[i]
                    
                    # Adicionar documento se fornecido
                    if i < len(task_documents) and task_documents[i]:
                        task.instruction_document = task_documents[i]
                    
                    task.save()
            
            messages.success(request, f'Template "{template.name}" criado com sucesso!')
            return redirect('checklists:admin_templates')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar template: {str(e)}')
            return redirect('checklists:create_template')
    
    # GET - mostrar formulário
    sectors = Sector.objects.all().order_by('name')
    
    context = {
        'sectors': sectors,
    }
    return render(request, 'checklists/create_template.html', context)


@login_required
def edit_template(request, template_id):
    """Editar template de checklist (apenas para admins)"""
    if not has_checklist_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('checklists:dashboard')
    
    template = get_object_or_404(ChecklistTemplate, id=template_id)
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        sector_id = request.POST.get('sector')
        is_active = request.POST.get('is_active') == 'on'
        
        # Validações
        if not name or not sector_id:
            messages.error(request, 'Nome e setor são obrigatórios.')
            return redirect('checklists:edit_template', template_id=template.id)
        
        try:
            sector = get_object_or_404(Sector, id=sector_id)
            
            # Atualizar template
            template.name = name
            template.description = description
            template.sector = sector
            template.is_active = is_active
            template.save()
            
            # Remover tarefas antigas
            template.tasks.all().delete()
            
            # Processar novas tarefas
            task_titles = request.POST.getlist('task_title[]')
            task_descriptions = request.POST.getlist('task_description[]')
            task_required = request.POST.getlist('task_required[]')
            
            for i, title in enumerate(task_titles):
                if title.strip():
                    task = ChecklistTask.objects.create(
                        template=template,
                        title=title.strip(),
                        description=task_descriptions[i].strip() if i < len(task_descriptions) else '',
                        is_required=str(i) in task_required,
                        order=i
                    )
                    
                    # Adicionar mídia de instrução se fornecida
                    if request.FILES.get(f'task_instruction_image_{i}'):
                        task.instruction_image = request.FILES.get(f'task_instruction_image_{i}')
                    
                    if request.FILES.get(f'task_instruction_video_{i}'):
                        task.instruction_video = request.FILES.get(f'task_instruction_video_{i}')
                    
                    if request.FILES.get(f'task_instruction_document_{i}'):
                        task.instruction_document = request.FILES.get(f'task_instruction_document_{i}')
                    
                    task.save()
            
            messages.success(request, f'Template "{template.name}" atualizado com sucesso!')
            return redirect('checklists:admin_templates')
            
        except Exception as e:
            messages.error(request, f'Erro ao atualizar template: {str(e)}')
            return redirect('checklists:edit_template', template_id=template.id)
    
    # GET - mostrar formulário
    sectors = Sector.objects.all().order_by('name')
    
    context = {
        'template': template,
        'sectors': sectors,
    }
    return render(request, 'checklists/edit_template.html', context)


@login_required
def delete_template(request, template_id):
    """Deletar template de checklist (apenas para admins)"""
    if not has_checklist_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('checklists:dashboard')
    
    template = get_object_or_404(ChecklistTemplate, id=template_id)
    
    # Verificar se o template está em uso
    assignments_count = ChecklistAssignment.objects.filter(template=template, is_active=True).count()
    
    if assignments_count > 0:
        messages.error(request, f'Não é possível deletar este template pois ele possui {assignments_count} atribuição(ões) ativa(s). Desative-o primeiro.')
        return redirect('checklists:admin_templates')
    
    template_name = template.name
    template.delete()
    
    messages.success(request, f'Template "{template_name}" deletado com sucesso!')
    return redirect('checklists:admin_templates')


@login_required
def admin_approvals(request):
    """Área de aprovação de checklists para super admins"""
    # Verificar se o usuário tem permissão para acessar aprovações
    allowed_sectors = ['SUPERVISOR', 'ADMINISTRATIVO', 'ADMIN', 'SUPERUSER']
    user_sector = request.user.sector.name if request.user.sector else None
    
    if not request.user.is_superuser and user_sector not in allowed_sectors:
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('checklists:dashboard')
    
    # Filtros
    status_filter = request.GET.get('status', 'awaiting_approval')
    sector_filter = request.GET.get('sector', '')
    user_filter = request.GET.get('user', '')
    date_filter = request.GET.get('date', '')
    
    # Buscar execuções pendentes de aprovação
    executions = ChecklistExecution.objects.select_related(
        'assignment__template',
        'assignment__template__sector',
        'assignment__assigned_to'
    ).prefetch_related(
        'task_executions__task'
    )
    
    if status_filter:
        executions = executions.filter(status=status_filter)
    
    if sector_filter:
        executions = executions.filter(assignment__template__sector_id=sector_filter)
    
    if user_filter:
        executions = executions.filter(assignment__assigned_to_id=user_filter)
    
    if date_filter:
        executions = executions.filter(execution_date=date_filter)
    
    executions = executions.order_by('-submitted_at', 'execution_date')
    
    # Estatísticas
    stats = {
        'awaiting_approval': ChecklistExecution.objects.filter(status='awaiting_approval').count(),
        'approved_today': ChecklistExecution.objects.filter(
            status='completed',
            completed_at__date=timezone.now().date()
        ).count(),
    }
    
    # Setores para filtro
    from users.models import Sector
    sectors = Sector.objects.all().order_by('name')
    
    context = {
        'executions': executions,
        'stats': stats,
        'sectors': sectors,
        'status_filter': status_filter,
        'sector_filter': sector_filter,
        'user_filter': user_filter,
        'date_filter': date_filter,
    }
    
    return render(request, 'checklists/admin_approvals.html', context)


@login_required
def approve_checklist(request, execution_id):
    """Aprovar checklist executado"""
    # Verificar se o usuário tem permissão para aprovar
    allowed_sectors = ['SUPERVISOR', 'ADMINISTRATIVO', 'ADMIN', 'SUPERUSER']
    user_sector = request.user.sector.name if request.user.sector else None
    
    if not request.user.is_superuser and user_sector not in allowed_sectors:
        messages.error(request, 'Você não tem permissão para aprovar checklists.')
        return redirect('checklists:dashboard')
    
    execution = get_object_or_404(ChecklistExecution, id=execution_id)
    
    if execution.status != 'awaiting_approval':
        messages.error(request, 'Este checklist não está aguardando aprovação.')
        return redirect('checklists:admin_approvals')
    
    execution.status = 'completed'
    execution.save()
    
    messages.success(request, f'✅ Checklist "{execution.assignment.template.name}" aprovado com sucesso!')
    return redirect('checklists:admin_approvals')


@login_required
def reject_checklist(request, execution_id):
    """Rejeitar checklist executado"""
    # Verificar se o usuário tem permissão para rejeitar
    allowed_sectors = ['SUPERVISOR', 'ADMINISTRATIVO', 'ADMIN', 'SUPERUSER']
    user_sector = request.user.sector.name if request.user.sector else None
    
    if not request.user.is_superuser and user_sector not in allowed_sectors:
        messages.error(request, 'Você não tem permissão para rejeitar checklists.')
        return redirect('checklists:dashboard')
    
    execution = get_object_or_404(ChecklistExecution, id=execution_id)
    
    if execution.status != 'awaiting_approval':
        messages.error(request, 'Este checklist não está aguardando aprovação.')
        return redirect('checklists:admin_approvals')
    
    if request.method == 'POST':
        rejection_note = request.POST.get('rejection_note', '')
        
        execution.status = 'in_progress'
        execution.submitted_at = None
        
        # Adicionar nota de rejeição na primeira tarefa (ou criar sistema de notas)
        if rejection_note:
            first_task = execution.task_executions.first()
            if first_task:
                current_note = first_task.notes or ''
                first_task.notes = f"⚠️ REJEITADO: {rejection_note}\n\n{current_note}"
                first_task.save()
        
        execution.save()
        
        messages.warning(request, f'⚠️ Checklist "{execution.assignment.template.name}" rejeitado e retornado para correção.')
        return redirect('checklists:admin_approvals')
    
    return redirect('checklists:admin_approvals')
    return redirect('checklists:admin_templates')