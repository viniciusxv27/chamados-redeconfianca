from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponseForbidden
from django.db import transaction
from django.core.paginator import Paginator
from django.db.models import Q, Count, Avg
from django.utils import timezone
from decimal import Decimal
import json

from .models import (
    Project, Activity, ProjectAttachment, ActivityComment, 
    ProjectSectorAccess
)
from users.models import Sector


def user_can_access_projects(user):
    """Verifica se o usuário pode acessar o sistema de projetos"""
    if user.is_superuser:
        return True
    
    try:
        access = ProjectSectorAccess.objects.get(sector=user.sector)
        return access.can_view_projects
    except (ProjectSectorAccess.DoesNotExist, AttributeError):
        return False


def user_can_create_projects(user):
    """Verifica se o usuário pode criar projetos"""
    if user.is_superuser:
        return True
    
    try:
        access = ProjectSectorAccess.objects.get(sector=user.sector)
        return access.can_create_projects
    except (ProjectSectorAccess.DoesNotExist, AttributeError):
        return False


def user_can_manage_all_projects(user):
    """Verifica se o usuário pode gerenciar todos os projetos"""
    if user.is_superuser:
        return True
    
    try:
        access = ProjectSectorAccess.objects.get(sector=user.sector)
        return access.can_manage_all_projects
    except (ProjectSectorAccess.DoesNotExist, AttributeError):
        return False


@login_required
def project_list(request):
    """Lista todos os projetos acessíveis ao usuário"""
    if not user_can_access_projects(request.user):
        return HttpResponseForbidden("Você não tem permissão para acessar esta área.")
    
    # Filtros baseados nas permissões
    if user_can_manage_all_projects(request.user):
        projects = Project.objects.all()
    else:
        projects = Project.objects.filter(
            Q(sector=request.user.sector) | 
            Q(created_by=request.user) |
            Q(responsible_user=request.user)
        )
    
    # Filtros da interface
    status_filter = request.GET.get('status')
    priority_filter = request.GET.get('priority')
    search = request.GET.get('search')
    
    if status_filter:
        projects = projects.filter(status=status_filter)
    
    if priority_filter:
        projects = projects.filter(priority=priority_filter)
    
    if search:
        projects = projects.filter(
            Q(name__icontains=search) |
            Q(description__icontains=search)
        )
    
    # Ordenação e paginação
    projects = projects.select_related(
        'created_by', 'responsible_user', 'sector'
    ).order_by('-created_at')
    
    paginator = Paginator(projects, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estatísticas
    stats = {
        'total': projects.count(),
        'em_andamento': projects.filter(status='EM_ANDAMENTO').count(),
        'concluidos': projects.filter(status='CONCLUIDO').count(),
        'atrasados': sum(1 for p in projects if p.is_overdue),
    }
    
    context = {
        'page_obj': page_obj,
        'stats': stats,
        'status_choices': Project.STATUS_CHOICES,
        'priority_choices': Project.PRIORITY_CHOICES,
        'filters': {
            'status': status_filter,
            'priority': priority_filter,
            'search': search,
        },
        'can_create': user_can_create_projects(request.user),
    }
    
    return render(request, 'projects/project_list.html', context)


@login_required
def project_detail(request, project_id):
    """Detalha um projeto específico"""
    if not user_can_access_projects(request.user):
        return HttpResponseForbidden("Você não tem permissão para acessar esta área.")
    
    project = get_object_or_404(Project, id=project_id)
    
    # Verificar permissão para ver este projeto
    if not (user_can_manage_all_projects(request.user) or 
            project.sector == request.user.sector or
            project.created_by == request.user or
            project.responsible_user == request.user):
        return HttpResponseForbidden("Você não tem permissão para ver este projeto.")
    
    # Atividades organizadas hierarquicamente
    activities = project.activities.select_related(
        'responsible_user', 'created_by', 'parent_activity'
    ).order_by('order', 'deadline')
    
    # Anexos
    attachments = project.attachments.select_related('uploaded_by').order_by('-uploaded_at')
    
    # Estatísticas do projeto
    activity_stats = {
        'total': activities.count(),
        'nao_iniciadas': activities.filter(status='NAO_INICIADA').count(),
        'em_andamento': activities.filter(status='EM_ANDAMENTO').count(),
        'concluidas': activities.filter(status='CONCLUIDA').count(),
        'atrasadas': sum(1 for a in activities if a.is_overdue),
    }
    
    context = {
        'project': project,
        'activities': activities,
        'attachments': attachments,
        'activity_stats': activity_stats,
        'can_edit': (
            user_can_manage_all_projects(request.user) or
            project.created_by == request.user or
            project.responsible_user == request.user
        ),
    }
    
    return render(request, 'projects/project_detail.html', context)


@login_required
def project_create(request):
    """Cria um novo projeto"""
    if not user_can_create_projects(request.user):
        return HttpResponseForbidden("Você não tem permissão para criar projetos.")
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                project = Project.objects.create(
                    name=request.POST['name'],
                    description=request.POST['description'],
                    scope=request.POST['scope'],
                    reason=request.POST['reason'],
                    deadline=request.POST['deadline'],
                    priority=request.POST['priority'],
                    sector_id=request.POST['sector'],
                    created_by=request.user
                )
                
                # Responsável principal (opcional)
                if request.POST.get('responsible_user'):
                    project.responsible_user_id = request.POST['responsible_user']
                    project.save()
                
                messages.success(request, 'Projeto criado com sucesso!')
                return redirect('projects:project_detail', project_id=project.id)
                
        except Exception as e:
            messages.error(request, f'Erro ao criar projeto: {str(e)}')
    
    # Setores disponíveis
    if user_can_manage_all_projects(request.user):
        sectors = Sector.objects.all()
    else:
        sectors = Sector.objects.filter(id=request.user.sector.id)
    
    context = {
        'sectors': sectors,
        'priority_choices': Project.PRIORITY_CHOICES,
    }
    
    return render(request, 'projects/project_create.html', context)


@login_required
def activity_create(request, project_id):
    """Cria uma nova atividade para um projeto"""
    if not user_can_access_projects(request.user):
        return HttpResponseForbidden("Você não tem permissão para acessar esta área.")
    
    project = get_object_or_404(Project, id=project_id)
    
    # Verificar permissão para editar este projeto
    if not (user_can_manage_all_projects(request.user) or 
            project.created_by == request.user or
            project.responsible_user == request.user):
        return HttpResponseForbidden("Você não tem permissão para editar este projeto.")
    
    if request.method == 'POST':
        try:
            activity = Activity.objects.create(
                project=project,
                name=request.POST['name'],
                description=request.POST['description'],
                deadline=request.POST['deadline'],
                priority=request.POST['priority'],
                created_by=request.user
            )
            
            # Atividade pai (para sub-atividades)
            if request.POST.get('parent_activity'):
                activity.parent_activity_id = request.POST['parent_activity']
            
            # Responsável (opcional)
            if request.POST.get('responsible_user'):
                activity.responsible_user_id = request.POST['responsible_user']
            
            activity.save()
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': 'Atividade criada com sucesso!',
                    'activity_id': activity.id
                })
            
            messages.success(request, 'Atividade criada com sucesso!')
            return redirect('projects:project_detail', project_id=project.id)
            
        except Exception as e:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'message': f'Erro ao criar atividade: {str(e)}'
                })
            messages.error(request, f'Erro ao criar atividade: {str(e)}')
    
    # Atividades pai possíveis (apenas atividades principais)
    parent_activities = project.activities.filter(parent_activity__isnull=True)
    
    context = {
        'project': project,
        'parent_activities': parent_activities,
        'priority_choices': Activity.PRIORITY_CHOICES,
    }
    
    return render(request, 'projects/activity_create.html', context)


@login_required
def activity_update_status(request, activity_id):
    """Atualiza o status de uma atividade via AJAX"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Método não permitido'})
    
    if not user_can_access_projects(request.user):
        return JsonResponse({'success': False, 'message': 'Sem permissão'})
    
    activity = get_object_or_404(Activity, id=activity_id)
    
    # Verificar permissão
    if not (user_can_manage_all_projects(request.user) or 
            activity.project.created_by == request.user or
            activity.project.responsible_user == request.user or
            activity.responsible_user == request.user):
        return JsonResponse({'success': False, 'message': 'Sem permissão para editar'})
    
    try:
        data = json.loads(request.body)
        new_status = data.get('status')
        
        if new_status not in dict(Activity.STATUS_CHOICES):
            return JsonResponse({'success': False, 'message': 'Status inválido'})
        
        activity.status = new_status
        
        # Atualizar data de conclusão se necessário
        if new_status == 'CONCLUIDA':
            activity.completion_date = timezone.now().date()
        else:
            activity.completion_date = None
        
        activity.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Status atualizado com sucesso!',
            'new_progress': float(activity.project.progress_percentage)
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})


@login_required
def project_dashboard(request):
    """Dashboard com estatísticas gerais dos projetos"""
    if not user_can_access_projects(request.user):
        return HttpResponseForbidden("Você não tem permissão para acessar esta área.")
    
    # Projetos acessíveis
    if user_can_manage_all_projects(request.user):
        projects = Project.objects.all()
    else:
        projects = Project.objects.filter(
            Q(sector=request.user.sector) | 
            Q(created_by=request.user) |
            Q(responsible_user=request.user)
        )
    
    # Estatísticas gerais
    total_projects = projects.count()
    projects_by_status = {}
    for status, label in Project.STATUS_CHOICES:
        projects_by_status[status] = {
            'label': label,
            'count': projects.filter(status=status).count()
        }
    
    # Projetos por prioridade
    projects_by_priority = {}
    for priority, label in Project.PRIORITY_CHOICES:
        projects_by_priority[priority] = {
            'label': label,
            'count': projects.filter(priority=priority).count()
        }
    
    # Projetos recentes
    recent_projects = projects.order_by('-created_at')[:5]
    
    # Projetos com prazo próximo
    upcoming_deadlines = projects.filter(
        deadline__gte=timezone.now().date(),
        deadline__lte=timezone.now().date() + timezone.timedelta(days=7),
        status__in=['STANDBY', 'EM_ANDAMENTO']
    ).order_by('deadline')[:5]
    
    # Projetos atrasados
    overdue_projects = [p for p in projects if p.is_overdue][:5]
    
    context = {
        'total_projects': total_projects,
        'projects_by_status': projects_by_status,
        'projects_by_priority': projects_by_priority,
        'recent_projects': recent_projects,
        'upcoming_deadlines': upcoming_deadlines,
        'overdue_projects': overdue_projects,
        'can_create': user_can_create_projects(request.user),
    }
    
    return render(request, 'projects/dashboard.html', context)
