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
from users.models import Sector, User

# Categorias de atividades
ACTIVITY_CATEGORIES = [
    ('desenvolvimento', 'Desenvolvimento'),
    ('design', 'Design'),
    ('marketing', 'Marketing'),
    ('vendas', 'Vendas'),
    ('suporte', 'Suporte'),
    ('administracao', 'Administração'),
    ('outros', 'Outros'),
]


def get_user_sectors(user):
    """Retorna todos os setores do usuário"""
    sectors = []
    if user.sector:
        sectors.append(user.sector)
    sectors.extend(user.sectors.all())
    return sectors


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
    # Apenas SUPERADMINs podem gerenciar todos os projetos
    if user.is_superuser or user.hierarchy == 'SUPERADMIN':
        return True
    
    return False


@login_required
def project_list(request):
    """Lista todos os projetos acessíveis ao usuário"""
    if not user_can_access_projects(request.user):
        return HttpResponseForbidden("Você não tem permissão para acessar esta área.")
    
    # Filtros baseados nas permissões
    # Apenas SUPERADMINs podem ver todos os projetos
    if request.user.hierarchy == 'SUPERADMIN':
        projects = Project.objects.all()
    else:
        # Outros usuários só veem projetos do seu setor
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
    if not (request.user.hierarchy == 'SUPERADMIN' or 
            project.sector == request.user.sector or
            project.created_by == request.user or
            project.responsible_user == request.user):
        return HttpResponseForbidden("Você não tem permissão para ver este projeto.")
    
    # Verificar se deve exibir em modo Kanban
    view_mode = request.GET.get('view', 'hierarchy')
    
    if view_mode == 'kanban':
        # Organizar atividades por status para visão Kanban (apenas atividades raiz, sem subtarefas)
        kanban_activities = {
            'NAO_INICIADA': project.activities.filter(status='NAO_INICIADA', parent_activity__isnull=True).select_related('responsible_user'),
            'EM_ANDAMENTO': project.activities.filter(status='EM_ANDAMENTO', parent_activity__isnull=True).select_related('responsible_user'),
            'CONCLUIDA': project.activities.filter(status='CONCLUIDA', parent_activity__isnull=True).select_related('responsible_user'),
            'CANCELADA': project.activities.filter(status='CANCELADA', parent_activity__isnull=True).select_related('responsible_user'),
        }
        
        # Organizar também por categoria se tiver (apenas atividades raiz)
        categories = project.activities.filter(parent_activity__isnull=True).exclude(category='').values_list('category', flat=True).distinct()
        activities_by_category = {}
        for category in categories:
            if category:
                activities_by_category[category] = {
                    'NAO_INICIADA': project.activities.filter(category=category, status='NAO_INICIADA', parent_activity__isnull=True).select_related('responsible_user'),
                    'EM_ANDAMENTO': project.activities.filter(category=category, status='EM_ANDAMENTO', parent_activity__isnull=True).select_related('responsible_user'),
                    'CONCLUIDA': project.activities.filter(category=category, status='CONCLUIDA', parent_activity__isnull=True).select_related('responsible_user'),
                    'CANCELADA': project.activities.filter(category=category, status='CANCELADA', parent_activity__isnull=True).select_related('responsible_user'),
                }
    
    # Atividades organizadas hierarquicamente - apenas as atividades raiz
    root_activities = project.activities.filter(
        parent_activity__isnull=True
    ).select_related(
        'responsible_user', 'created_by'
    ).prefetch_related(
        'sub_activities__responsible_user',
        'sub_activities__sub_activities__responsible_user'
    ).order_by('order', 'deadline')
    
    # Todas as atividades para estatísticas
    all_activities = project.activities.all()
    
    # Anexos
    attachments = project.attachments.select_related('uploaded_by').order_by('-uploaded_at')
    
    # Estatísticas do projeto
    total_activities = all_activities.count()
    completed_activities = all_activities.filter(status='CONCLUIDA').count()
    in_progress_activities = all_activities.filter(status='EM_ANDAMENTO').count()
    remaining_activities = total_activities - completed_activities
    
    # Calcular progresso
    if total_activities > 0:
        progress_percentage = (completed_activities / total_activities) * 100
    else:
        progress_percentage = 0
    
    activity_stats = {
        'total': total_activities,
        'nao_iniciadas': all_activities.filter(status='NAO_INICIADA').count(),
        'em_andamento': in_progress_activities,
        'concluidas': completed_activities,
        'atrasadas': sum(1 for a in all_activities if a.is_overdue),
    }
    
    context = {
        'project': project,
        'root_activities': root_activities,
        'activities': root_activities,  # Alias for compatibility
        'all_activities': all_activities,
        'attachments': attachments,
        'activity_stats': activity_stats,
        'total_activities': total_activities,
        'completed_activities': completed_activities,
        'in_progress_activities': in_progress_activities,
        'remaining_activities': remaining_activities,
        'progress_percentage': round(progress_percentage, 1),
        'view_mode': view_mode,
        'can_edit': (
            user_can_manage_all_projects(request.user) or
            project.created_by == request.user or
            project.responsible_user == request.user
        ),
    }
    
    # Adicionar dados do Kanban se for o modo selecionado
    if view_mode == 'kanban':
        context.update({
            'kanban_activities': kanban_activities,
            'activities_by_category': activities_by_category if 'activities_by_category' in locals() else {},
            'categories': list(categories) if 'categories' in locals() else [],
        })
    
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
        # Usuário pode ver setores onde tem acesso direto ou é membro
        user_sectors = []
        if request.user.sector:
            user_sectors.append(request.user.sector.id)
        user_sectors.extend(request.user.sectors.values_list('id', flat=True))
        
        if user_sectors:
            sectors = Sector.objects.filter(id__in=user_sectors)
        else:
            # Se usuário não tem nenhum setor, mostrar todos (fallback)
            sectors = Sector.objects.all()
    
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
    if request.user.hierarchy == 'SUPERADMIN':
        projects = Project.objects.all()
    else:
        # Usuários não-SUPERADMIN só veem projetos do seu setor
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
    
    # Projetos por status específicos (cards principais)
    standby_projects = projects.filter(status='STANDBY')
    in_progress_projects = projects.filter(status='EM_ANDAMENTO')
    completed_projects = projects.filter(status='CONCLUIDO')
    
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
        'standby_projects': standby_projects,
        'in_progress_projects': in_progress_projects,
        'completed_projects': completed_projects,
        'recent_projects': recent_projects,
        'upcoming_deadlines': upcoming_deadlines,
        'overdue_projects': overdue_projects,
        'can_create': user_can_create_projects(request.user),
    }
    
    return render(request, 'projects/dashboard.html', context)


@login_required
def project_edit(request, project_id):
    """View para editar um projeto"""
    project = get_object_or_404(Project, id=project_id)
    
    # Verificar permissões
    if not (request.user.hierarchy == 'SUPERADMIN' or 
            project.created_by == request.user or 
            project.responsible_user == request.user):
        return HttpResponseForbidden("Você não tem permissão para editar este projeto.")
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                project.name = request.POST.get('name')
                project.description = request.POST.get('description')
                project.status = request.POST.get('status')
                project.priority = request.POST.get('priority')
                project.deadline = request.POST.get('deadline')
                
                # Se o usuário é SUPERADMIN, permitir mudança de responsável
                if request.user.hierarchy == 'SUPERADMIN':
                    responsible_id = request.POST.get('responsible_user')
                    if responsible_id:
                        from django.contrib.auth import get_user_model
                        User = get_user_model()
                        project.responsible_user = get_object_or_404(User, id=responsible_id)
                    else:
                        project.responsible_user = None
                
                project.save()
                messages.success(request, 'Projeto atualizado com sucesso!')
                return redirect('projects:project_detail', project_id=project.id)
        except Exception as e:
            messages.error(request, f'Erro ao atualizar projeto: {str(e)}')
    
    # Buscar usuários para seleção de responsável
    from django.contrib.auth import get_user_model
    User = get_user_model()
    users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    
    context = {
        'project': project,
        'users': users,
        'can_manage_all': user_can_manage_all_projects(request.user),
    }
    
    return render(request, 'projects/project_edit.html', context)


@login_required
def project_delete(request, project_id):
    """View para excluir um projeto (apenas superadmin)"""
    project = get_object_or_404(Project, id=project_id)
    
    # Apenas superadmin pode excluir projetos
    if not request.user.is_superuser:
        return HttpResponseForbidden("Apenas superadministradores podem excluir projetos.")
    
    if request.method == 'POST':
        try:
            project_name = project.name
            project.delete()
            messages.success(request, f'Projeto "{project_name}" foi excluído com sucesso!')
            return redirect('projects:project_list')
        except Exception as e:
            messages.error(request, f'Erro ao excluir projeto: {str(e)}')
            return redirect('projects:project_detail', project_id=project.id)
    
    context = {
        'project': project,
    }
    
    return render(request, 'projects/project_delete.html', context)


@login_required
def activity_detail_api(request, activity_id):
    """API para detalhes da atividade (para modal)"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    try:
        activity = get_object_or_404(Activity, id=activity_id)
        
        # Verificar permissão de acesso
        if request.user.hierarchy != 'SUPERADMIN':
            if (activity.project.sector != request.user.sector and 
                activity.project.created_by != request.user and 
                activity.project.responsible_user != request.user):
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        # Buscar comentários
        comments = []
        for comment in activity.comments.select_related('user').order_by('created_at'):
            comments.append({
                'id': comment.id,
                'text': comment.text,
                'author_name': comment.user.get_full_name() or comment.user.username,
                'created_at': comment.created_at.strftime('%d/%m/%Y às %H:%M')
            })
        
        # Buscar subtarefas (simulado - você pode implementar um modelo real)
        subtasks = []
        for sub_activity in activity.sub_activities.all():
            subtasks.append({
                'id': sub_activity.id,
                'title': sub_activity.name,
                'completed': sub_activity.status == 'CONCLUIDA'
            })
        
        data = {
            'id': activity.id,
            'title': activity.name,
            'description': activity.description,
            'status': activity.status,
            'status_display': activity.get_status_display(),
            'priority': activity.priority,
            'priority_display': activity.get_priority_display(),
            'category_name': activity.category if activity.category else None,
            'responsible_name': activity.responsible_user.get_full_name() if activity.responsible_user else None,
            'deadline': activity.deadline.strftime('%d/%m/%Y') if activity.deadline else None,
            'progress': 100 if activity.status == 'CONCLUIDA' else (50 if activity.status == 'EM_ANDAMENTO' else 0),
            'created_at': activity.created_at.strftime('%d/%m/%Y às %H:%M'),
            'updated_at': activity.updated_at.strftime('%d/%m/%Y às %H:%M'),
            'created_by': activity.created_by.get_full_name() or activity.created_by.username,
            'comments': comments,
            'subtasks': subtasks
        }
        
        return JsonResponse(data)
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required 
def activity_add_comment(request, activity_id):
    """Adicionar comentário à atividade"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    try:
        activity = get_object_or_404(Activity, id=activity_id)
        
        # Verificar permissão de acesso
        if not user_can_manage_all_projects(request.user):
            if activity.project.sector != request.user.sector:
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        data = json.loads(request.body)
        text = data.get('text', '').strip()
        
        if not text:
            return JsonResponse({'error': 'Comentário não pode estar vazio'}, status=400)
        
        comment = ActivityComment.objects.create(
            activity=activity,
            user=request.user,
            text=text
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Comentário adicionado com sucesso',
            'comment': {
                'id': comment.id,
                'text': comment.text,
                'author_name': comment.user.get_full_name() or comment.user.username,
                'created_at': comment.created_at.strftime('%d/%m/%Y às %H:%M')
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def activity_add_subtask(request, activity_id):
    """Adicionar subtarefa à atividade"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    try:
        activity = get_object_or_404(Activity, id=activity_id)
        
        # Verificar permissão de acesso
        if not user_can_manage_all_projects(request.user):
            if activity.project.sector != request.user.sector:
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        data = json.loads(request.body)
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        deadline = data.get('deadline', '')
        responsible_id = data.get('responsible_id', '')
        
        if not title:
            return JsonResponse({'error': 'Título da subtarefa não pode estar vazio'}, status=400)
        
        # Se não informou deadline, usar o deadline da atividade pai
        if deadline:
            from datetime import datetime
            try:
                deadline_date = datetime.strptime(deadline, '%Y-%m-%d').date()
            except ValueError:
                return JsonResponse({'error': 'Formato de data inválido. Use YYYY-MM-DD'}, status=400)
        else:
            deadline_date = activity.deadline

        # Definir responsável da subtarefa
        responsible_user = activity.responsible_user  # Por padrão, mesmo da atividade pai
        if responsible_id:
            try:
                responsible_user = User.objects.get(id=responsible_id)
                # Verificar se o usuário pertence ao setor do projeto
                if not user_can_manage_all_projects(request.user):
                    if responsible_user.sector != activity.project.sector:
                        return JsonResponse({'error': 'Usuário não pertence ao setor do projeto'}, status=400)
            except User.DoesNotExist:
                return JsonResponse({'error': 'Usuário responsável não encontrado'}, status=400)
        
        # Criar uma nova atividade como sub-atividade
        subtask = Activity.objects.create(
            project=activity.project,
            name=title,
            description=description if description else f'Subtarefa de: {activity.name}',
            deadline=deadline_date,
            parent_activity=activity,
            responsible_user=responsible_user,
            created_by=request.user,
            priority=data.get('priority', 'MEDIA'),
            status='NAO_INICIADA'
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Subtarefa criada com sucesso',
            'subtask': {
                'id': subtask.id,
                'title': subtask.name,
                'description': subtask.description,
                'deadline': subtask.deadline.strftime('%Y-%m-%d'),
                'responsible': {
                    'id': subtask.responsible_user.id if subtask.responsible_user else None,
                    'name': subtask.responsible_user.get_full_name() if subtask.responsible_user else None
                },
                'priority': subtask.get_priority_display(),
                'status': subtask.get_status_display(),
                'completed': subtask.status == 'CONCLUIDA'
            }
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def get_project_users(request, project_id):
    """Buscar usuários disponíveis para atribuir como responsável"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    try:
        project = get_object_or_404(Project, id=project_id)
        
        # Verificar permissão de acesso
        if not user_can_manage_all_projects(request.user):
            if project.sector != request.user.sector:
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        # Buscar usuários do setor do projeto
        users = User.objects.filter(
            sector=project.sector,
            is_active=True
        ).order_by('first_name', 'last_name')
        
        users_data = [
            {
                'id': user.id,
                'name': user.get_full_name(),
                'username': user.username,
                'hierarchy': user.get_hierarchy_display()
            }
            for user in users
        ]
        
        return JsonResponse({
            'success': True,
            'users': users_data
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def subtask_detail_api(request, activity_id):
    """Buscar detalhes completos de uma subtarefa"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    try:
        subtask = get_object_or_404(Activity, id=activity_id)
        
        # Verificar se é realmente uma subtarefa
        if not subtask.parent_activity:
            return JsonResponse({'error': 'Atividade não é uma subtarefa'}, status=400)
        
        # Verificar permissão de acesso
        if not user_can_manage_all_projects(request.user):
            if subtask.project.sector != request.user.sector:
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        # Montar dados da subtarefa
        subtask_data = {
            'id': subtask.id,
            'title': subtask.name,
            'description': subtask.description,
            'priority': subtask.priority,
            'status': subtask.status,
            'status_display': subtask.get_status_display(),
            'deadline': subtask.deadline.isoformat() if subtask.deadline else None,
            'parent_activity': subtask.parent_activity.name,
            'created_at': subtask.created_at.isoformat(),
            'updated_at': subtask.updated_at.isoformat(),
            'responsible': None
        }
        
        # Adicionar dados do responsável se existir
        if subtask.responsible_user:
            subtask_data['responsible'] = {
                'id': subtask.responsible_user.id,
                'first_name': subtask.responsible_user.first_name,
                'last_name': subtask.responsible_user.last_name,
                'email': subtask.responsible_user.email,
                'username': subtask.responsible_user.username
            }
        
        return JsonResponse({
            'success': True,
            'subtask': subtask_data
        })
        
    except Activity.DoesNotExist:
        return JsonResponse({'error': 'Subtarefa não encontrada'}, status=404)
    except Exception as e:
        import traceback
        print(f"Erro na view subtask_detail_api: {str(e)}")
        print(traceback.format_exc())
        return JsonResponse({'error': f'Erro interno: {str(e)}'}, status=500)


@login_required
def subtask_toggle(request, subtask_id):
    """Alternar status de completude da subtarefa"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    try:
        subtask = get_object_or_404(Activity, id=subtask_id)
        
        # Verificar permissão de acesso
        if not user_can_manage_all_projects(request.user):
            if subtask.project.sector != request.user.sector:
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        # Alternar status
        if subtask.status == 'CONCLUIDA':
            subtask.status = 'EM_ANDAMENTO'
        else:
            subtask.status = 'CONCLUIDA'
        
        subtask.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Status da subtarefa atualizado',
            'completed': subtask.status == 'CONCLUIDA'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def activity_duplicate(request, activity_id):
    """Duplicar atividade"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    try:
        activity = get_object_or_404(Activity, id=activity_id)
        
        # Verificar permissão de acesso
        if not user_can_manage_all_projects(request.user):
            if activity.project.sector != request.user.sector:
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        # Duplicar atividade
        new_activity = Activity.objects.create(
            project=activity.project,
            name=f"{activity.name} (Cópia)",
            description=activity.description,
            priority=activity.priority,
            responsible_user=activity.responsible_user,
            deadline=activity.deadline,
            category=activity.category,
            created_by=request.user,
            status='NAO_INICIADA'
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Atividade duplicada com sucesso',
            'new_activity_id': new_activity.id
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def activity_archive(request, activity_id):
    """Arquivar atividade"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    try:
        activity = get_object_or_404(Activity, id=activity_id)
        
        # Verificar permissão de acesso
        if not user_can_manage_all_projects(request.user):
            if activity.project.sector != request.user.sector:
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        # Arquivar atividade (mudar status para cancelled ou criar campo archived)
        activity.status = 'CANCELADA'
        activity.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Atividade arquivada com sucesso'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def activity_edit(request, activity_id):
    """Editar uma atividade"""
    activity = get_object_or_404(Activity, id=activity_id)
    
    # Verificar permissão
    if not user_can_access_projects(request.user):
        return HttpResponseForbidden("Você não tem permissão para acessar esta área.")
    
    if not (user_can_manage_all_projects(request.user) or 
            activity.project.created_by == request.user or
            activity.project.responsible_user == request.user or
            activity.responsible_user == request.user):
        messages.error(request, 'Você não tem permissão para editar esta atividade.')
        return redirect('projects:project_detail', project_id=activity.project.id)
    
    if request.method == 'POST':
        try:
            # Atualizar dados da atividade
            activity.name = request.POST.get('name', '').strip()
            activity.description = request.POST.get('description', '').strip()
            activity.status = request.POST.get('status', activity.status)
            activity.priority = request.POST.get('priority', activity.priority)
            activity.category = request.POST.get('category', '')
            
            # Atualizar deadline se fornecido
            deadline = request.POST.get('deadline')
            if deadline:
                try:
                    from datetime import datetime
                    activity.deadline = datetime.strptime(deadline, '%Y-%m-%d').date()
                except ValueError:
                    pass
            
            # Atualizar responsável se fornecido e permitido
            if user_can_manage_all_projects(request.user):
                responsible_user_id = request.POST.get('responsible_user')
                if responsible_user_id:
                    try:
                        responsible_user = User.objects.get(id=responsible_user_id)
                        activity.responsible_user = responsible_user
                    except User.DoesNotExist:
                        pass
            
            activity.save()
            
            messages.success(request, 'Atividade atualizada com sucesso!')
            
            # Se for AJAX, retornar JSON
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': 'Atividade atualizada com sucesso!'
                })
            
            return redirect('projects:project_detail', project_id=activity.project.id)
            
        except Exception as e:
            error_msg = f'Erro ao atualizar atividade: {str(e)}'
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': False,
                    'message': error_msg
                })
            
            messages.error(request, error_msg)
    
    # Buscar usuários disponíveis para atribuição
    if user_can_manage_all_projects(request.user):
        available_users = User.objects.filter(is_active=True).order_by('first_name', 'username')
    else:
        # Apenas usuários do mesmo setor
        user_sectors = get_user_sectors(request.user)
        available_users = User.objects.filter(
            Q(sector__in=user_sectors) | Q(sectors__in=user_sectors)
        ).distinct().order_by('first_name', 'username')
    
    context = {
        'activity': activity,
        'project': activity.project,
        'available_users': available_users,
        'can_manage': user_can_manage_all_projects(request.user),
        'categories': ACTIVITY_CATEGORIES,
    }
    
    return render(request, 'projects/activity_edit.html', context)


@login_required
def subtask_delete(request, subtask_id):
    """Deletar subtarefa definitivamente"""
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    if request.method != 'DELETE':
        return JsonResponse({'error': 'Método não permitido'}, status=405)
    
    try:
        subtask = get_object_or_404(Activity, id=subtask_id)
        
        # Verificar se é realmente uma subtarefa
        if not subtask.parent_activity:
            return JsonResponse({'error': 'Esta não é uma subtarefa'}, status=400)
        
        # Verificar permissão de acesso
        if not user_can_manage_all_projects(request.user):
            if subtask.project.sector != request.user.sector:
                return JsonResponse({'error': 'Acesso negado'}, status=403)
        
        # Armazenar informações antes de deletar
        parent_activity_id = subtask.parent_activity.id
        subtask_name = subtask.name
        
        # Deletar a subtarefa
        subtask.delete()
        
        return JsonResponse({
            'success': True, 
            'message': f'Subtarefa "{subtask_name}" foi excluída definitivamente',
            'parent_activity_id': parent_activity_id
        })
    
    except Activity.DoesNotExist:
        return JsonResponse({'error': 'Subtarefa não encontrada'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
