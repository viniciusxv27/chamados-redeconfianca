from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.utils import timezone
from .models import Report, ReportComment
from users.models import User
from core.middleware import log_action


@login_required
def create_report_view(request):
    """Criar nova denúncia"""
    users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('first_name', 'last_name')
    
    if request.method == 'POST':
        report_type = request.POST.get('report_type')
        title = request.POST.get('title')
        description = request.POST.get('description')
        reported_user_id = request.POST.get('reported_user')
        is_anonymous = request.POST.get('is_anonymous') == 'on'
        evidence = request.FILES.get('evidence')
        
        try:
            reported_user = None
            if reported_user_id:
                reported_user = User.objects.get(id=reported_user_id)
            
            report = Report.objects.create(
                reporter=request.user,
                reported_user=reported_user,
                report_type=report_type,
                title=title,
                description=description,
                is_anonymous=is_anonymous,
                evidence=evidence,
                ip_address=request.META.get('REMOTE_ADDR')
            )
            
            log_action(
                request.user,
                'ADMIN_ACTION',
                f'Denúncia criada: {report.title}',
                request
            )
            
            messages.success(request, 'Denúncia criada com sucesso! Nossa equipe analisará em breve.')
            return redirect('reports_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar denúncia: {str(e)}')
    
    context = {
        'users': users,
        'report_types': Report.REPORT_TYPES,
        'user': request.user,
    }
    return render(request, 'reports/create.html', context)


@login_required
def reports_list_view(request):
    """Lista de denúncias - diferentes visualizações por tipo de usuário"""
    # Superadmins veem todas as denúncias
    if request.user.hierarchy == 'SUPERADMIN':
        reports = Report.objects.all()
        show_all = True
    else:
        # Usuários comuns veem apenas suas próprias denúncias
        reports = Report.objects.filter(reporter=request.user)
        show_all = False
    
    # Filtros
    status_filter = request.GET.get('status')
    priority_filter = request.GET.get('priority')
    type_filter = request.GET.get('type')
    search = request.GET.get('search')
    
    if status_filter:
        reports = reports.filter(status=status_filter)
    
    if priority_filter:
        reports = reports.filter(priority=priority_filter)
        
    if type_filter:
        reports = reports.filter(report_type=type_filter)
    
    if search:
        reports = reports.filter(
            Q(title__icontains=search) |
            Q(description__icontains=search) |
            Q(reporter__first_name__icontains=search) |
            Q(reporter__last_name__icontains=search)
        )
    
    # Estatísticas para superadmins
    stats = None
    if show_all:
        stats = {
            'total': Report.objects.count(),
            'pending': Report.objects.filter(status='PENDING').count(),
            'under_review': Report.objects.filter(status='UNDER_REVIEW').count(),
            'resolved': Report.objects.filter(status='RESOLVED').count(),
            'urgent': Report.objects.filter(priority__in=['HIGH', 'CRITICAL']).count(),
        }
    
    # Paginação
    paginator = Paginator(reports, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'reports': page_obj,
        'show_all': show_all,
        'stats': stats,
        'status_choices': Report.STATUS_CHOICES,
        'priority_choices': Report.PRIORITY_CHOICES,
        'report_types': Report.REPORT_TYPES,
        'current_filters': {
            'status': status_filter,
            'priority': priority_filter,
            'type': type_filter,
            'search': search,
        },
        'user': request.user,
    }
    return render(request, 'reports/list.html', context)


@login_required
def report_detail_view(request, report_id):
    """Detalhes de uma denúncia"""
    report = get_object_or_404(Report, id=report_id)
    
    # Verificar permissões
    if not report.can_be_viewed_by(request.user):
        messages.error(request, 'Você não tem permissão para ver esta denúncia.')
        return redirect('reports_list')
    
    comments = report.comments.all()
    if request.user.hierarchy not in ['SUPERADMIN', 'ADMINISTRATIVO']:
        # Usuários comuns não veem comentários internos
        comments = comments.filter(is_internal=False)
    
    context = {
        'report': report,
        'comments': comments,
        'can_manage': report.can_be_managed_by(request.user),
        'user': request.user,
        'status_choices': Report.STATUS_CHOICES,
        'priority_choices': Report.PRIORITY_CHOICES,
    }
    return render(request, 'reports/detail.html', context)


@login_required
def update_report_status(request, report_id):
    """Atualizar status de uma denúncia (apenas admins)"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido'})
    
    report = get_object_or_404(Report, id=report_id)
    
    if not report.can_be_managed_by(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissão'})
    
    try:
        new_status = request.POST.get('status')
        new_priority = request.POST.get('priority')
        assigned_to_id = request.POST.get('assigned_to')
        admin_notes = request.POST.get('admin_notes')
        resolution = request.POST.get('resolution')
        
        if new_status:
            report.status = new_status
            
        if new_priority:
            report.priority = new_priority
            
        if assigned_to_id:
            report.assigned_to = User.objects.get(id=assigned_to_id)
            
        if admin_notes:
            report.admin_notes = admin_notes
            
        if resolution:
            report.resolution = resolution
        
        report.save()
        
        log_action(
            request.user,
            'ADMIN_ACTION',
            f'Denúncia #{report.id} atualizada para {report.get_status_display()}',
            request
        )
        
        messages.success(request, 'Denúncia atualizada com sucesso!')
        return redirect('report_detail', report_id=report.id)
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def add_report_comment(request, report_id):
    """Adicionar comentário a uma denúncia"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido'})
    
    report = get_object_or_404(Report, id=report_id)
    
    if not report.can_be_viewed_by(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissão'})
    
    try:
        comment_text = request.POST.get('comment')
        is_internal = request.POST.get('is_internal') == 'true'
        
        # Apenas admins podem fazer comentários internos
        if is_internal and not report.can_be_managed_by(request.user):
            is_internal = False
        
        comment = ReportComment.objects.create(
            report=report,
            user=request.user,
            comment=comment_text,
            is_internal=is_internal
        )
        
        messages.success(request, 'Comentário adicionado com sucesso!')
        return redirect('report_detail', report_id=report.id)
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required 
def reports_dashboard_view(request):
    """Dashboard de denúncias (apenas para superadmins)"""
    if request.user.hierarchy != 'SUPERADMIN':
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')
    
    # Estatísticas gerais
    total_reports = Report.objects.count()
    pending_reports = Report.objects.filter(status='PENDING').count()
    urgent_reports = Report.objects.filter(priority__in=['HIGH', 'CRITICAL']).count()
    
    # Relatórios recentes
    recent_reports = Report.objects.order_by('-created_at')[:10]
    
    # Relatórios por tipo
    reports_by_type = Report.objects.values('report_type').annotate(
        count=Count('id')
    ).order_by('-count')
    
    # Relatórios por status
    reports_by_status = Report.objects.values('status').annotate(
        count=Count('id')
    ).order_by('-count')
    
    context = {
        'total_reports': total_reports,
        'pending_reports': pending_reports,
        'urgent_reports': urgent_reports,
        'recent_reports': recent_reports,
        'reports_by_type': reports_by_type,
        'reports_by_status': reports_by_status,
        'user': request.user,
    }
    return render(request, 'reports/dashboard.html', context)
