from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from tickets.models import Ticket, Category
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
