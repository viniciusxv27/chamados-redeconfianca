from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.utils import timezone
from tickets.models import Ticket, TicketComment, Category
from users.models import User
from core.middleware import log_action


@login_required
def create_report_view(request):
    """Criar nova denúncia anônima"""
    return redirect('anonymous_report')


@login_required
def reports_list_view(request):
    """Lista de denúncias públicas"""
    return render(request, 'reports/list.html')


@login_required
def manage_reports(request):
    """Gerenciar denúncias - apenas superadmins"""
    if request.user.hierarchy != 'SUPERADMIN':
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    # Filtros
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    search_query = request.GET.get('search', '')
    
    # Buscar apenas tickets anônimos (denúncias)
    reports = Ticket.objects.filter(is_anonymous=True).select_related(
        'category', 'sector', 'assigned_to'
    ).order_by('-created_at')
    
    # Aplicar filtros
    if status_filter:
        reports = reports.filter(status=status_filter)
    
    if priority_filter:
        reports = reports.filter(priority=priority_filter)
    
    if search_query:
        reports = reports.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query)
        )
    
    # Paginação
    paginator = Paginator(reports, 15)
    page_number = request.GET.get('page')
    reports = paginator.get_page(page_number)
    
    # Estatísticas
    stats = {
        'total': Ticket.objects.filter(is_anonymous=True).count(),
        'pending': Ticket.objects.filter(is_anonymous=True, status='ABERTO').count(),
        'in_progress': Ticket.objects.filter(is_anonymous=True, status='EM_ANDAMENTO').count(),
        'resolved': Ticket.objects.filter(is_anonymous=True, status='RESOLVIDO').count(),
    }
    
    context = {
        'reports': reports,
        'stats': stats,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'search_query': search_query,
        'status_choices': Ticket.STATUS_CHOICES,
        'priority_choices': Ticket.PRIORITY_CHOICES,
    }
    
    return render(request, 'reports/manage.html', context)


@login_required
def report_detail(request, report_id):
    """Detalhes de uma denúncia - apenas superadmins"""
    if request.user.hierarchy != 'SUPERADMIN':
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    report = get_object_or_404(Ticket, id=report_id, is_anonymous=True)
    comments = TicketComment.objects.filter(ticket=report).order_by('created_at')
    
    # Buscar usuários para atribuição
    users = User.objects.filter(
        hierarchy__in=['SUPERADMIN', 'ADMINISTRATIVO', 'SUPERVISOR']
    ).order_by('first_name', 'last_name')
    
    context = {
        'report': report,
        'comments': comments,
        'users': users,
        'status_choices': Ticket.STATUS_CHOICES,
        'priority_choices': Ticket.PRIORITY_CHOICES,
    }
    
    return render(request, 'reports/detail.html', context)


@login_required
def update_report_status(request, report_id):
    """Atualizar status de uma denúncia"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({'success': False, 'error': 'Sem permissão'})
    
    if request.method == 'POST':
        report = get_object_or_404(Ticket, id=report_id, is_anonymous=True)
        
        new_status = request.POST.get('status')
        new_priority = request.POST.get('priority')
        assigned_to_id = request.POST.get('assigned_to')
        
        if new_status and new_status in dict(Ticket.STATUS_CHOICES):
            old_status = report.get_status_display()
            report.status = new_status
            report.save()
            
            log_action(
                request.user,
                'REPORT_STATUS_UPDATE',
                f'Status da denúncia #{report.id} alterado de {old_status} para {report.get_status_display()}',
                request
            )
            
            messages.success(request, f'Status atualizado para: {report.get_status_display()}')
        
        if new_priority and new_priority in dict(Ticket.PRIORITY_CHOICES):
            old_priority = report.get_priority_display()
            report.priority = new_priority
            report.save()
            
            log_action(
                request.user,
                'REPORT_PRIORITY_UPDATE',
                f'Prioridade da denúncia #{report.id} alterada de {old_priority} para {report.get_priority_display()}',
                request
            )
            
            messages.success(request, f'Prioridade atualizada para: {report.get_priority_display()}')
        
        if assigned_to_id:
            try:
                assigned_user = User.objects.get(id=assigned_to_id)
                report.assigned_to = assigned_user
                report.save()
                
                log_action(
                    request.user,
                    'REPORT_ASSIGNMENT',
                    f'Denúncia #{report.id} atribuída a {assigned_user.full_name}',
                    request
                )
                
                messages.success(request, f'Denúncia atribuída a: {assigned_user.full_name}')
            except User.DoesNotExist:
                messages.error(request, 'Usuário não encontrado.')
        
        return redirect('reports:report_detail', report_id=report.id)
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})


@login_required
def add_report_comment(request, report_id):
    """Adicionar comentário a uma denúncia"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({'success': False, 'error': 'Sem permissão'})
    
    if request.method == 'POST':
        report = get_object_or_404(Ticket, id=report_id, is_anonymous=True)
        comment_text = request.POST.get('comment', '').strip()
        
        if comment_text:
            comment = TicketComment.objects.create(
                ticket=report,
                user=request.user,
                comment=comment_text
            )
            
            log_action(
                request.user,
                'REPORT_COMMENT_ADD',
                f'Comentário adicionado à denúncia #{report.id}',
                request
            )
            
            messages.success(request, 'Comentário adicionado com sucesso!')
        else:
            messages.error(request, 'Comentário não pode estar vazio.')
        
        return redirect('reports:report_detail', report_id=report.id)
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})
