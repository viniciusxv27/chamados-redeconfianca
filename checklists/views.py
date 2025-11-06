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
    is_superadmin = user.hierarchy == 'SUPERADMIN' or user.is_superuser
    
    # Checklists atribuídos ao usuário
    my_assignments = ChecklistAssignment.objects.filter(
        assigned_to=user,
        is_active=True
    ).select_related('template', 'assigned_by')
    
    # Para supervisores: mostrar checklists dos seus setores
    # Para superadmin: mostrar todos
    sector_assignments = ChecklistAssignment.objects.none()
    if is_superadmin:
        # SUPERADMIN vê todos os checklists
        sector_assignments = ChecklistAssignment.objects.filter(
            is_active=True
        ).exclude(
            assigned_to=user  # Não duplicar os que já estão em my_assignments
        ).select_related('template', 'assigned_to', 'assigned_by')
    elif is_supervisor:
        # SUPERVISOR vê checklists cujo TEMPLATE é do seu setor
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        
        if user_sectors:
            sector_assignments = ChecklistAssignment.objects.filter(
                template__sector__in=user_sectors,
                is_active=True
            ).exclude(
                assigned_to=user  # Não duplicar
            ).select_related('template', 'assigned_to', 'assigned_by')
    
    # Execuções pendentes de hoje
    today = timezone.now().date()
    today_executions = ChecklistExecution.objects.filter(
        assignment__assigned_to=user,
        execution_date=today
    ).select_related('assignment__template')
    
    # Para supervisores: incluir execuções dos checklists cujo TEMPLATE é do seu setor
    # Para superadmin: incluir todas as execuções
    if is_superadmin:
        supervisor_executions = ChecklistExecution.objects.filter(
            execution_date=today
        ).exclude(
            assignment__assigned_to=user  # Não duplicar
        ).select_related('assignment__template', 'assignment__assigned_to')
        
        # Combinar as querysets
        from itertools import chain
        today_executions = list(chain(today_executions, supervisor_executions))
    elif is_supervisor:
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        
        if user_sectors:
            supervisor_executions = ChecklistExecution.objects.filter(
                assignment__template__sector__in=user_sectors,
                execution_date=today
            ).exclude(
                assignment__assigned_to=user  # Não duplicar
            ).select_related('assignment__template', 'assignment__assigned_to')
            
            # Combinar as querysets
            from itertools import chain
            today_executions = list(chain(today_executions, supervisor_executions))
    
    # Separar por status apenas (combinando manhã e tarde)
    if isinstance(today_executions, list):
        # Se é lista (combinada), filtrar manualmente
        pending_checklists = [e for e in today_executions if e.status in ['pending', 'in_progress']]
        completed_checklists = [e for e in today_executions if e.status in ['completed', 'awaiting_approval']]
    else:
        # Se é queryset, usar filter
        pending_checklists = today_executions.filter(
            status__in=['pending', 'in_progress']
        )
        completed_checklists = today_executions.filter(
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
        'total_assignments': my_assignments.count() + (sector_assignments.count() if is_supervisor else 0),
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
    
    # Para supervisores: incluir execuções dos checklists cujo TEMPLATE é do seu setor
    # Para superadmin: incluir todas as execuções
    if is_superadmin:
        supervisor_calendar_executions = ChecklistExecution.objects.filter(
            execution_date__gte=calendar_start,
            execution_date__lte=calendar_end
        ).exclude(
            assignment__assigned_to=user  # Não duplicar
        ).select_related('assignment__template', 'assignment__assigned_to').order_by('execution_date')
        
        from itertools import chain
        calendar_executions = list(chain(calendar_executions, supervisor_calendar_executions))
        calendar_executions.sort(key=lambda x: x.execution_date)
    elif is_supervisor:
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        
        if user_sectors:
            supervisor_calendar_executions = ChecklistExecution.objects.filter(
                assignment__template__sector__in=user_sectors,
                execution_date__gte=calendar_start,
                execution_date__lte=calendar_end
            ).exclude(
                assignment__assigned_to=user  # Não duplicar
            ).select_related('assignment__template', 'assignment__assigned_to').order_by('execution_date')
            
            from itertools import chain
            calendar_executions = list(chain(calendar_executions, supervisor_calendar_executions))
            calendar_executions.sort(key=lambda x: x.execution_date)
    
    context = {
        'my_assignments': my_assignments[:5],  # Primeiros 5
        'sector_assignments': sector_assignments[:5] if is_supervisor else [],  # Primeiros 5 do setor
        'today_executions': today_executions,
        'pending_checklists': pending_checklists,
        'completed_checklists': completed_checklists,
        'stats': stats,
        'available_templates': available_templates,
        'calendar_executions': calendar_executions,
        'is_supervisor': is_supervisor,
        'is_superadmin': is_superadmin,
    }
    return render(request, 'checklists/dashboard.html', context)


@login_required
def create_assignment(request):
    """Criar nova atribuição de checklist"""
    if request.method == 'POST':
        # Mudado para aceitar múltiplos templates
        template_ids = request.POST.getlist('template_ids')  # Lista de IDs
        assignment_type = request.POST.get('assignment_type', 'user')  # 'user' ou 'group'
        assigned_to_id = request.POST.get('assigned_to')
        group_id = request.POST.get('group_id')
        schedule_type = request.POST.get('schedule_type')
        period = request.POST.get('period', 'both')
        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        custom_dates_str = request.POST.get('custom_dates', '[]')
        
        # Validações básicas
        if not template_ids or not schedule_type:
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
            # Buscar todos os templates selecionados
            templates = ChecklistTemplate.objects.filter(id__in=template_ids)
            
            if not templates.exists():
                messages.error(request, 'Nenhum template válido foi selecionado.')
                return redirect('checklists:create_assignment')
            
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
            
            # Verificar permissão para todos os templates
            user_sectors = list(request.user.sectors.all())
            if request.user.sector:
                user_sectors.append(request.user.sector)
            
            for template in templates:
                if template.sector not in user_sectors:
                    if not has_checklist_admin_permission(request.user):
                        messages.error(request, f'Você não tem permissão para atribuir o checklist "{template.name}".')
                        return redirect('checklists:dashboard')
            
            # Processar datas personalizadas
            custom_dates = []
            if schedule_type == 'custom':
                try:
                    custom_dates = json.loads(custom_dates_str)
                except (json.JSONDecodeError, TypeError):
                    custom_dates = []
            
            # Criar atribuições para cada combinação de template e usuário
            assignments_created = 0
            for template in templates:
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
            
            # Mensagem de sucesso personalizada
            template_count = len(template_ids)
            user_count = len(users_to_assign)
            
            if assignment_type == 'group':
                messages.success(request, f'✅ {template_count} checklist(s) atribuído(s) para {user_count} usuário(s) do grupo com sucesso!')
            else:
                messages.success(request, f'✅ {template_count} checklist(s) atribuído(s) para {users_to_assign[0].get_full_name()} com sucesso!')
                
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
            
            # Criar task_executions se não existirem
            # Verificar se há tarefas no template
            template_tasks = assignment.template.tasks.all()
            if template_tasks.exists():
                # Verificar quais tasks já têm execução
                existing_task_ids = set(
                    ChecklistTaskExecution.objects.filter(
                        execution=execution
                    ).values_list('task_id', flat=True)
                )
                
                # Criar task_executions para tarefas que ainda não têm
                for task in template_tasks:
                    if task.id not in existing_task_ids:
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
        'task_executions__task__instruction_media'
    ).order_by('period', 'assignment__template__name')
    
    # IMPORTANTE: Garantir que todas as execuções tenham suas task_executions criadas
    # Isso corrige um bug onde execuções eram criadas sem task_executions
    for execution in today_executions:
        template_tasks = execution.assignment.template.tasks.all()
        if template_tasks.exists():
            existing_task_ids = set(
                execution.task_executions.values_list('task_id', flat=True)
            )
            
            # Criar task_executions para tarefas que ainda não têm
            for task in template_tasks:
                if task.id not in existing_task_ids:
                    ChecklistTaskExecution.objects.create(
                        execution=execution,
                        task=task
                    )
    
    # Recarregar execuções para pegar as task_executions recém-criadas
    if any(execution.task_executions.count() == 0 for execution in today_executions):
        today_executions = ChecklistExecution.objects.filter(
            assignment__assigned_to=user,
            execution_date=today,
            status__in=['pending', 'in_progress']
        ).select_related(
            'assignment__template'
        ).prefetch_related(
            'task_executions__task__instruction_media'
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
                notes = request.POST.get(notes_key, '').strip()
                evidence_image = request.FILES.get(image_key)
                evidence_video = request.FILES.get(video_key)
                
                # VALIDAÇÃO: Se marcado como completo, deve ter descrição OU evidência
                if is_completed:
                    has_notes = bool(notes)
                    has_evidence = bool(evidence_image or evidence_video or task_exec.evidence_image or task_exec.evidence_video)
                    
                    if not has_notes and not has_evidence:
                        messages.error(
                            request,
                            f'❌ Tarefa "{task_exec.task.title}" do checklist "{execution.assignment.template.name}": '
                            f'você deve preencher a descrição OU anexar alguma evidência (imagem/vídeo).'
                        )
                        return redirect('checklists:today_checklists')
                
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
@login_required
def view_execution(request, execution_id):
    """Visualizar execução específica de checklist com opção de aprovar"""
    # Buscar execução
    execution = get_object_or_404(
        ChecklistExecution.objects.select_related(
            'assignment__template',
            'assignment__assigned_to',
            'assignment__assigned_by'
        ).prefetch_related(
            'task_executions__task__instruction_media'
        ),
        id=execution_id
    )
    
    # Verificar se o usuário tem permissão para ver
    user = request.user
    can_view = (
        user == execution.assignment.assigned_to or  # É o executor
        user.is_superuser or  # É superuser
        (hasattr(user, 'hierarchy') and user.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO'])  # É supervisor+
    )
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para visualizar esta execução.')
        return redirect('checklists:dashboard')
    
    # Verificar se pode aprovar (supervisor+ e não é o executor)
    can_approve = (
        user != execution.assignment.assigned_to and
        (user.is_superuser or (hasattr(user, 'hierarchy') and user.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO']))
    )
    
    context = {
        'execution': execution,
        'can_approve': can_approve,
        'assignment': execution.assignment,
        'template': execution.assignment.template,
        'task_executions': execution.task_executions.all(),
    }
    return render(request, 'checklists/view_execution.html', context)


@login_required
def execute_checklist(request, assignment_id):
    """Executar/visualizar checklist individual por assignment_id e period"""
    from datetime import date
    
    # Pegar o período da query string
    period = request.GET.get('period', 'morning')
    
    # Buscar o assignment
    assignment = get_object_or_404(
        ChecklistAssignment.objects.select_related('template', 'assigned_to', 'assigned_by'),
        id=assignment_id
    )
    
    # Verificar permissão de visualização
    user = request.user
    can_view = (
        user == assignment.assigned_to or  # É o executor
        user.is_superuser or  # É superuser
        (hasattr(user, 'hierarchy') and user.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO'])  # É supervisor+
    )
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para visualizar esta atribuição.')
        return redirect('checklists:dashboard')
    
    # Buscar ou criar a execução para a data e período especificados
    # Verificar se há data específica na query string, senão usar hoje
    date_str = request.GET.get('date')
    if date_str:
        try:
            from datetime import datetime
            execution_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            execution_date = date.today()
    else:
        execution_date = date.today()
    
    execution, created = ChecklistExecution.objects.get_or_create(
        assignment=assignment,
        date=execution_date,
        period=period,
        defaults={'status': 'pending'}
    )
    
    # Certificar que todas as task_executions existem
    existing_tasks = set(execution.task_executions.values_list('task_id', flat=True))
    template_tasks = assignment.template.tasks.all()
    
    for task in template_tasks:
        if task.id not in existing_tasks:
            ChecklistTaskExecution.objects.create(
                execution=execution,
                task=task,
                is_completed=False
            )
    
    # Se o usuário for o executor e o status for pending, redirecionar para today_checklists para executar
    if user == assignment.assigned_to and execution.status == 'pending' and execution_date == date.today():
        messages.info(request, f'📋 Execute seu checklist "{assignment.template.name}" abaixo.')
        return redirect('checklists:today_checklists')
    
    # Caso contrário, redirecionar para a view de visualização
    return redirect('checklists:view_execution', execution_id=execution.id)


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


@login_required
def api_get_day_checklists(request):
    """API para buscar todos os checklists de um dia específico (supervisor ou maior)"""
    user = request.user
    
    # Verificar se é supervisor ou hierarquia maior
    is_supervisor = user.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO'] or user.is_superuser
    is_superadmin = user.hierarchy == 'SUPERADMIN' or user.is_superuser
    
    if not is_supervisor:
        return JsonResponse({'error': 'Sem permissão'}, status=403)
    
    date_str = request.GET.get('date')
    if not date_str:
        return JsonResponse({'error': 'Data não informada'}, status=400)
    
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Formato de data inválido'}, status=400)
    
    # Buscar execuções baseadas no nível de permissão
    if is_superadmin:
        # SUPERADMIN vê todas as execuções do dia
        executions = ChecklistExecution.objects.filter(
            execution_date=target_date
        ).select_related(
            'assignment__template',
            'assignment__assigned_to',
            'assignment__assigned_by'
        ).prefetch_related(
            'task_executions__task'
        ).order_by('period', 'assignment__template__name')
    else:
        # SUPERVISOR vê execuções dos checklists cujo TEMPLATE é do seu setor + suas próprias
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        
        if user_sectors:
            executions = ChecklistExecution.objects.filter(
                Q(assignment__assigned_to=user) | Q(assignment__template__sector__in=user_sectors),
                execution_date=target_date
            ).select_related(
                'assignment__template',
                'assignment__assigned_to',
                'assignment__assigned_by'
            ).prefetch_related(
                'task_executions__task'
            ).order_by('period', 'assignment__template__name')
        else:
            # Se não tem setores, vê apenas os próprios
            executions = ChecklistExecution.objects.filter(
                assignment__assigned_to=user,
                execution_date=target_date
            ).select_related(
                'assignment__template',
                'assignment__assigned_to',
                'assignment__assigned_by'
            ).prefetch_related(
                'task_executions__task'
            ).order_by('period', 'assignment__template__name')
    
    checklists_data = []
    for execution in executions:
        # Calcular progresso
        total_tasks = execution.task_executions.count()
        completed_tasks = execution.task_executions.filter(is_completed=True).count()
        progress = round((completed_tasks / total_tasks * 100)) if total_tasks > 0 else 0
        
        checklists_data.append({
            'id': execution.id,
            'assignment_id': execution.assignment.id,
            'name': execution.assignment.template.name,
            'description': execution.assignment.template.description,
            'assigned_to': execution.assignment.assigned_to.get_full_name(),
            'assigned_to_id': execution.assignment.assigned_to.id,
            'assigned_by': execution.assignment.assigned_by.get_full_name() if execution.assignment.assigned_by else '',
            'status': execution.status,
            'period': execution.period,
            'progress': progress,
            'can_unassign': execution.assignment.assigned_by == user,
            'url': f'/checklists/execute/{execution.assignment.id}/?period={execution.period}'
        })
    
    return JsonResponse({'checklists': checklists_data})


@login_required
def api_unassign_checklist(request, assignment_id):
    """API para desatribuir um checklist"""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    user = request.user
    
    try:
        assignment = get_object_or_404(ChecklistAssignment, id=assignment_id)
        
        # Verificar se o usuário tem permissão para desatribuir
        # Pode desatribuir se foi quem atribuiu OU se é SUPERADMIN
        can_unassign = (
            assignment.assigned_by == user or 
            user.hierarchy == 'SUPERADMIN' or 
            user.is_superuser
        )
        
        if not can_unassign:
            return JsonResponse({'error': 'Você não tem permissão para desatribuir este checklist'}, status=403)
        
        # Marcar como inativo ao invés de deletar (preservar histórico)
        assignment.is_active = False
        assignment.save()
        
        return JsonResponse({
            'success': True,
            'message': f'Checklist "{assignment.template.name}" desatribuído de {assignment.assigned_to.get_full_name()}'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


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
            
            for i, title in enumerate(task_titles):
                if title.strip():
                    task = ChecklistTask.objects.create(
                        template=template,
                        title=title.strip(),
                        description=task_descriptions[i].strip() if i < len(task_descriptions) else '',
                        is_required=str(i) in task_required,
                        order=i
                    )
                    
                    # Processar múltiplos arquivos de instrução para esta tarefa
                    # Imagens
                    task_images = request.FILES.getlist(f'task_images_{i}[]')
                    for order, image_file in enumerate(task_images):
                        if image_file and image_file.size > 0:
                            from checklists.models import ChecklistTaskInstructionMedia
                            ChecklistTaskInstructionMedia.objects.create(
                                task=task,
                                media_type='image',
                                file=image_file,
                                order=order
                            )
                    
                    # Vídeos
                    task_videos = request.FILES.getlist(f'task_videos_{i}[]')
                    for order, video_file in enumerate(task_videos):
                        if video_file and video_file.size > 0:
                            from checklists.models import ChecklistTaskInstructionMedia
                            ChecklistTaskInstructionMedia.objects.create(
                                task=task,
                                media_type='video',
                                file=video_file,
                                order=order
                            )
                    
                    # Documentos
                    task_documents = request.FILES.getlist(f'task_documents_{i}[]')
                    for order, doc_file in enumerate(task_documents):
                        if doc_file and doc_file.size > 0:
                            from checklists.models import ChecklistTaskInstructionMedia
                            ChecklistTaskInstructionMedia.objects.create(
                                task=task,
                                media_type='document',
                                file=doc_file,
                                order=order
                            )
            
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
                    
                    # Processar múltiplos arquivos de instrução para esta tarefa
                    # Imagens
                    task_images = request.FILES.getlist(f'task_images_{i}[]')
                    for order, image_file in enumerate(task_images):
                        if image_file and image_file.size > 0:
                            from checklists.models import ChecklistTaskInstructionMedia
                            ChecklistTaskInstructionMedia.objects.create(
                                task=task,
                                media_type='image',
                                file=image_file,
                                order=order
                            )
                    
                    # Vídeos
                    task_videos = request.FILES.getlist(f'task_videos_{i}[]')
                    for order, video_file in enumerate(task_videos):
                        if video_file and video_file.size > 0:
                            from checklists.models import ChecklistTaskInstructionMedia
                            ChecklistTaskInstructionMedia.objects.create(
                                task=task,
                                media_type='video',
                                file=video_file,
                                order=order
                            )
                    
                    # Documentos
                    task_documents = request.FILES.getlist(f'task_documents_{i}[]')
                    for order, doc_file in enumerate(task_documents):
                        if doc_file and doc_file.size > 0:
                            from checklists.models import ChecklistTaskInstructionMedia
                            ChecklistTaskInstructionMedia.objects.create(
                                task=task,
                                media_type='document',
                                file=doc_file,
                                order=order
                            )
            
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
    """Área de aprovação de checklists para supervisores e hierarquias superiores"""
    # Verificar se o usuário tem permissão para acessar aprovações
    # SUPERVISOR, ADMIN, SUPERADMIN, ADMINISTRATIVO ou superuser
    is_authorized = (
        request.user.is_superuser or
        (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO'])
    )
    
    if not is_authorized:
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
    
    # Filtrar por setores do usuário (exceto superuser que vê tudo)
    if not request.user.is_superuser:
        user_sectors = list(request.user.sectors.all())
        if request.user.sector:
            user_sectors.append(request.user.sector)
        
        if user_sectors:
            executions = executions.filter(assignment__template__sector__in=user_sectors)
        else:
            # Se usuário não tem setores, não pode aprovar nada
            executions = executions.none()
    
    if status_filter:
        executions = executions.filter(status=status_filter)
    
    if sector_filter:
        executions = executions.filter(assignment__template__sector_id=sector_filter)
    
    if user_filter:
        executions = executions.filter(assignment__assigned_to_id=user_filter)
    
    if date_filter:
        executions = executions.filter(execution_date=date_filter)
    
    executions = executions.order_by('-submitted_at', 'execution_date')
    
    # Estatísticas (também filtradas por setor)
    stats_filter = {}
    if not request.user.is_superuser:
        user_sectors = list(request.user.sectors.all())
        if request.user.sector:
            user_sectors.append(request.user.sector)
        if user_sectors:
            stats_filter['assignment__template__sector__in'] = user_sectors
    
    stats = {
        'awaiting_approval': ChecklistExecution.objects.filter(status='awaiting_approval', **stats_filter).count(),
        'approved_today': ChecklistExecution.objects.filter(
            status='completed',
            completed_at__date=timezone.now().date(),
            **stats_filter
        ).count(),
    }
    
    # Setores para filtro (apenas os setores do usuário)
    from users.models import Sector
    if request.user.is_superuser:
        sectors = Sector.objects.all().order_by('name')
    else:
        user_sectors = list(request.user.sectors.all())
        if request.user.sector:
            user_sectors.append(request.user.sector)
        sectors = Sector.objects.filter(id__in=[s.id for s in user_sectors]).order_by('name')
    
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
    is_authorized = (
        request.user.is_superuser or
        (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO'])
    )
    
    if not is_authorized:
        messages.error(request, 'Você não tem permissão para aprovar checklists.')
        return redirect('checklists:dashboard')
    
    execution = get_object_or_404(ChecklistExecution, id=execution_id)
    
    # Verificar se o checklist é de um setor do usuário
    if not request.user.is_superuser:
        user_sectors = list(request.user.sectors.all())
        if request.user.sector:
            user_sectors.append(request.user.sector)
        
        if execution.assignment.template.sector not in user_sectors:
            messages.error(request, 'Você só pode aprovar checklists dos seus setores.')
            return redirect('checklists:admin_approvals')
    
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
    is_authorized = (
        request.user.is_superuser or
        (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO'])
    )
    
    if not is_authorized:
        messages.error(request, 'Você não tem permissão para rejeitar checklists.')
        return redirect('checklists:dashboard')
    
    execution = get_object_or_404(ChecklistExecution, id=execution_id)
    
    # Verificar se o checklist é de um setor do usuário
    if not request.user.is_superuser:
        user_sectors = list(request.user.sectors.all())
        if request.user.sector:
            user_sectors.append(request.user.sector)
        
        if execution.assignment.template.sector not in user_sectors:
            messages.error(request, 'Você só pode rejeitar checklists dos seus setores.')
            return redirect('checklists:admin_approvals')
    
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