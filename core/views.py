from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from tickets.models import Ticket, Category
from .middleware import log_action
import json


@login_required
def marketplace(request):
    """Marketplace de recompensas C$"""
    context = {
        'user_balance': request.user.balance_cs,
        'featured_items': [],  # Placeholder para itens em destaque
        'categories': [],      # Placeholder para categorias
    }
    return render(request, 'marketplace/index.html', context)


@login_required  
def training_module(request):
    """Módulo de treinamentos"""
    context = {
        'courses': [],         # Placeholder para cursos
        'user_progress': {},   # Placeholder para progresso do usuário
    }
    return render(request, 'training/index.html', context)


@login_required
def dashboard(request):
    """Dashboard analítico"""
    from django.db.models import Count, Q
    from datetime import datetime, timedelta
    from users.models import User
    from communications.models import Communication
    
    # Estatísticas de tickets
    total_tickets = Ticket.objects.count()
    open_tickets = Ticket.objects.filter(status='ABERTO').count()
    closed_tickets = Ticket.objects.filter(status='FECHADO').count()
    pending_tickets = Ticket.objects.filter(status='EM_ANDAMENTO').count()
    
    # Estatísticas de usuários
    total_users = User.objects.count()
    active_users = User.objects.filter(is_active=True).count()
    
    # Estatísticas de comunicações
    total_communications = Communication.objects.count()
    recent_communications = Communication.objects.filter(
        created_at__gte=datetime.now() - timedelta(days=7)
    ).count()
    
    # Comunicados fixados na dashboard
    pinned_communications = Communication.objects.filter(
        is_pinned=True,
        send_to_all=True
    ).order_by('-created_at')[:3]
    
    # Tickets por categoria
    tickets_by_category = Ticket.objects.values('category__name').annotate(
        count=Count('id')
    ).order_by('-count')[:5]
    
    # Tickets recentes
    recent_tickets = Ticket.objects.order_by('-created_at')[:5]
    
    # Comunicações recentes
    recent_comms = Communication.objects.order_by('-created_at')[:5]
    
    context = {
        'stats': {
            'total_tickets': total_tickets,
            'open_tickets': open_tickets,
            'closed_tickets': closed_tickets,
            'pending_tickets': pending_tickets,
            'total_users': total_users,
            'active_users': active_users,
            'total_communications': total_communications,
            'recent_communications': recent_communications,
        },
        'pinned_communications': pinned_communications,
        'tickets_by_category': tickets_by_category,
        'recent_tickets': recent_tickets,
        'recent_communications': recent_comms,
        'charts_data': {
            'categories': [item['category__name'] for item in tickets_by_category],
            'values': [item['count'] for item in tickets_by_category],
        }
    }
    return render(request, 'dashboard.html', context)


@login_required
def anonymous_report(request):
    """Canal de denúncias anônimas"""
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        urgency = request.POST.get('urgency', 'NORMAL')
        
        try:
            # Criar ticket anônimo
            anonymous_category, created = Category.objects.get_or_create(
                name='Denúncia Anônima',
                defaults={'description': 'Denúncias enviadas anonimamente'}
            )
            
            ticket = Ticket.objects.create(
                title=f"[ANÔNIMO] {title}",
                description=description,
                category=anonymous_category,
                created_by=request.user,  # Sistema registra quem criou, mas é tratado como anônimo
                priority='ALTA' if urgency == 'URGENT' else 'NORMAL',
                status='ABERTO',
                is_anonymous=True  # Flag para identificar denúncias anônimas
            )
            
            messages.success(request, 'Denúncia enviada com sucesso! Será tratada com total confidencialidade.')
            return redirect('anonymous_report')
            
        except Exception as e:
            messages.error(request, f'Erro ao enviar denúncia: {str(e)}')
    
    context = {
        'urgency_choices': [
            ('NORMAL', 'Normal'),
            ('URGENT', 'Urgente'),
        ]
    }
    return render(request, 'core/anonymous_report.html', context)


@login_required
def manage_reports(request):
    """Visualizar e gerenciar denúncias - Apenas Superadmins"""
    if request.user.hierarchy != 'SUPERADMIN':
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    from tickets.models import Ticket
    from django.db.models import Q
    from django.core.paginator import Paginator
    
    # Buscar tickets anônimos (denúncias)
    reports = Ticket.objects.filter(is_anonymous=True).select_related(
        'category', 'created_by', 'assigned_to', 'sector'
    ).order_by('-created_at')
    
    # Filtros
    status_filter = request.GET.get('status')
    priority_filter = request.GET.get('priority')
    search = request.GET.get('search')
    
    if status_filter and status_filter != 'all':
        reports = reports.filter(status=status_filter)
    
    if priority_filter and priority_filter != 'all':
        reports = reports.filter(priority=priority_filter)
    
    if search:
        reports = reports.filter(
            Q(title__icontains=search) | 
            Q(description__icontains=search)
        )
    
    # Paginação
    paginator = Paginator(reports, 10)
    page_number = request.GET.get('page')
    reports = paginator.get_page(page_number)
    
    # Estatísticas
    stats = {
        'total': Ticket.objects.filter(is_anonymous=True).count(),
        'pending': Ticket.objects.filter(is_anonymous=True, status='ABERTO').count(),
        'in_progress': Ticket.objects.filter(is_anonymous=True, status='EM_ANDAMENTO').count(),
        'resolved': Ticket.objects.filter(is_anonymous=True, status='RESOLVIDO').count(),
        'urgent': Ticket.objects.filter(is_anonymous=True, priority='ALTA').count(),
    }
    
    context = {
        'reports': reports,
        'stats': stats,
        'status_choices': Ticket.STATUS_CHOICES,
        'priority_choices': Ticket.PRIORITY_CHOICES,
        'current_status': status_filter,
        'current_priority': priority_filter,
        'search': search,
    }
    return render(request, 'core/manage_reports.html', context)


@login_required  
def report_detail(request, report_id):
    """Ver detalhes de uma denúncia - Apenas Superadmins"""
    if request.user.hierarchy != 'SUPERADMIN':
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    from tickets.models import Ticket
    from django.shortcuts import get_object_or_404
    
    report = get_object_or_404(Ticket, id=report_id, is_anonymous=True)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'update_status':
            new_status = request.POST.get('status')
            old_status = report.status
            report.status = new_status
            report.save()
            
            log_action(
                request.user,
                'REPORT_STATUS_UPDATE', 
                f'Status da denúncia #{report.id} alterado de {old_status} para {new_status}',
                request
            )
            
            messages.success(request, f'Status da denúncia atualizado para {report.get_status_display()}')
            
        elif action == 'assign':
            from users.models import User
            assigned_to_id = request.POST.get('assigned_to')
            if assigned_to_id:
                assigned_user = User.objects.get(id=assigned_to_id)
                report.assigned_to = assigned_user
                report.status = 'EM_ANDAMENTO'
                report.save()
                
                log_action(
                    request.user,
                    'REPORT_ASSIGNMENT',
                    f'Denúncia #{report.id} atribuída para {assigned_user.full_name}',
                    request
                )
                
                messages.success(request, f'Denúncia atribuída para {assigned_user.full_name}')
        
        elif action == 'add_note':
            from tickets.models import TicketComment
            note = request.POST.get('note')
            if note.strip():
                TicketComment.objects.create(
                    ticket=report,
                    author=request.user,
                    comment=f"[NOTA ADMINISTRATIVA] {note}",
                    is_internal=True
                )
                
                log_action(
                    request.user,
                    'REPORT_NOTE_ADDED',
                    f'Nota administrativa adicionada à denúncia #{report.id}',
                    request
                )
                
                messages.success(request, 'Nota administrativa adicionada com sucesso')
        
        return redirect('report_detail', report_id=report.id)
    
    # Buscar usuários que podem ser responsáveis
    from users.models import User
    assignable_users = User.objects.filter(
        hierarchy__in=['SUPERADMIN', 'ADMINISTRATIVO', 'SUPERVISOR']
    ).order_by('first_name')
    
    # Buscar comentários/notas
    comments = report.comments.all().order_by('created_at')
    
    context = {
        'report': report,
        'assignable_users': assignable_users,
        'comments': comments,
        'status_choices': Ticket.STATUS_CHOICES,
    }
    return render(request, 'core/report_detail.html', context)
