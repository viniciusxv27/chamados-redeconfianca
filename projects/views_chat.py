from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db.models import Q
from django.contrib import messages
from django.db import models

from .models import Activity
from .models_chat import TaskChat, TaskChatMessage, SupportChat, SupportChatMessage, SupportAgent, SupportCategory, SupportChatRating, SupportChatFile, SupportChatFile
from users.models import User


@login_required
def get_task_chat(request, activity_id):
    """Buscar mensagens do chat de uma tarefa"""
    activity = get_object_or_404(Activity, id=activity_id)
    
    # Verificar se o usuário pode acessar esta tarefa
    if not (activity.created_by == request.user or 
            activity.responsible_user == request.user or 
            request.user.hierarchy in ['ADMIN', 'SUPERADMIN']):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    # Criar chat se não existir
    chat, created = TaskChat.objects.get_or_create(activity=activity)
    
    # Buscar mensagens
    messages = TaskChatMessage.objects.filter(chat=chat).select_related('user')
    
    messages_data = []
    for msg in messages:
        messages_data.append({
            'id': msg.id,
            'user': {
                'id': msg.user.id,
                'name': msg.user.get_full_name(),
                'avatar': msg.user.first_name[0].upper() if msg.user.first_name else 'U'
            },
            'message': msg.message,
            'created_at': msg.created_at.strftime('%d/%m/%Y %H:%M'),
            'is_own': msg.user == request.user
        })
    
    return JsonResponse({
        'success': True,
        'messages': messages_data,
        'activity': {
            'id': activity.id,
            'name': activity.name,
            'created_by': activity.created_by.get_full_name(),
            'responsible': activity.responsible_user.get_full_name() if activity.responsible_user else None
        }
    })


@login_required
@require_POST
def send_task_message(request, activity_id):
    """Enviar mensagem no chat de uma tarefa"""
    activity = get_object_or_404(Activity, id=activity_id)
    
    # Verificar se o usuário pode acessar esta tarefa
    if not (activity.created_by == request.user or 
            activity.responsible_user == request.user or 
            request.user.hierarchy in ['ADMIN', 'SUPERADMIN']):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    message_text = request.POST.get('message', '').strip()
    if not message_text:
        return JsonResponse({'success': False, 'error': 'Mensagem não pode estar vazia'})
    
    # Criar chat se não existir
    chat, created = TaskChat.objects.get_or_create(activity=activity)
    
    # Criar mensagem
    message = TaskChatMessage.objects.create(
        chat=chat,
        user=request.user,
        message=message_text
    )
    
    return JsonResponse({
        'success': True,
        'message': {
            'id': message.id,
            'user': {
                'id': request.user.id,
                'name': request.user.get_full_name(),
                'avatar': request.user.first_name[0].upper() if request.user.first_name else 'U'
            },
            'message': message.message,
            'created_at': message.created_at.strftime('%d/%m/%Y %H:%M'),
            'is_own': True
        }
    })


@login_required
def support_chat_list(request):
    """Listar chats de suporte do usuário"""
    user_chats = SupportChat.objects.filter(user=request.user).order_by('-updated_at')
    
    # Se for agente de suporte, mostrar todos os chats
    is_support_agent = hasattr(request.user, 'support_agent') and request.user.support_agent.is_active
    if is_support_agent or request.user.hierarchy == 'SUPERADMIN':
        all_chats = SupportChat.objects.all().order_by('-updated_at')
    else:
        all_chats = user_chats
    
    chats_data = []
    for chat in all_chats[:20]:  # Limitar a 20 chats
        last_message = chat.messages.last()
        chats_data.append({
            'id': chat.id,
            'title': chat.title,
            'status': chat.get_status_display(),
            'status_code': chat.status,
            'priority': chat.get_priority_display(),
            'user': chat.user.get_full_name(),
            'assigned_to': chat.assigned_to.get_full_name() if chat.assigned_to else None,
            'last_message': last_message.message[:100] if last_message else 'Sem mensagens',
            'updated_at': chat.updated_at.strftime('%d/%m/%Y %H:%M'),
            'is_own': chat.user == request.user
        })
    
    return JsonResponse({
        'success': True,
        'chats': chats_data,
        'is_support_agent': is_support_agent
    })


@login_required
def get_support_chat(request, chat_id):
    """Buscar mensagens de um chat de suporte"""
    chat = get_object_or_404(SupportChat, id=chat_id)
    
    # Verificar se o usuário pode acessar este chat
    is_support_agent = hasattr(request.user, 'support_agent') and request.user.support_agent.is_active
    if not (chat.user == request.user or is_support_agent or request.user.hierarchy == 'SUPERADMIN'):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    # Buscar mensagens
    messages = SupportChatMessage.objects.filter(chat=chat).select_related('user')
    
    # Filtrar mensagens internas se não for agente de suporte
    if not (is_support_agent or request.user.hierarchy == 'SUPERADMIN'):
        messages = messages.filter(is_internal=False)
    
    messages_data = []
    for msg in messages:
        messages_data.append({
            'id': msg.id,
            'user': {
                'id': msg.user.id,
                'name': msg.user.get_full_name(),
                'avatar': msg.user.first_name[0].upper() if msg.user.first_name else 'U',
                'is_support': hasattr(msg.user, 'support_agent')
            },
            'message': msg.message,
            'is_internal': msg.is_internal,
            'created_at': msg.created_at.strftime('%d/%m/%Y %H:%M'),
            'is_own': msg.user == request.user
        })
    
    return JsonResponse({
        'success': True,
        'messages': messages_data,
        'chat': {
            'id': chat.id,
            'title': chat.title,
            'status': chat.get_status_display(),
            'status_code': chat.status,
            'priority': chat.get_priority_display(),
            'user': chat.user.get_full_name(),
            'assigned_to': chat.assigned_to.get_full_name() if chat.assigned_to else None,
            'is_own': chat.user == request.user
        },
        'is_support_agent': is_support_agent
    })


@login_required
@require_POST
def create_support_chat(request):
    """Criar novo chat de suporte"""
    title = request.POST.get('title', '').strip()
    message = request.POST.get('message', '').strip()
    sector_id = request.POST.get('sector_id')
    category_id = request.POST.get('category_id')
    
    if not title or not message:
        return JsonResponse({'success': False, 'error': 'Título e mensagem são obrigatórios'})
    
    # Importar modelos necessários
    from users.models import Sector
    
    # Criar chat
    chat_data = {
        'user': request.user,
        'title': title,
    }
    
    if sector_id:
        try:
            sector = Sector.objects.get(id=sector_id)
            chat_data['sector'] = sector
        except Sector.DoesNotExist:
            pass
    
    if category_id:
        try:
            category = SupportCategory.objects.get(id=category_id)
            chat_data['category'] = category
        except SupportCategory.DoesNotExist:
            pass
    
    chat = SupportChat.objects.create(**chat_data)
    
    # Criar primeira mensagem
    SupportChatMessage.objects.create(
        chat=chat,
        user=request.user,
        message=message
    )
    
    return JsonResponse({
        'success': True,
        'chat_id': chat.id,
        'message': 'Chat de suporte criado com sucesso!'
    })


@login_required
@require_POST
def send_support_message(request, chat_id):
    """Enviar mensagem no chat de suporte"""
    chat = get_object_or_404(SupportChat, id=chat_id)
    
    # Verificar se o usuário pode acessar este chat
    is_support_agent = hasattr(request.user, 'support_agent') and request.user.support_agent.is_active
    if not (chat.user == request.user or is_support_agent or request.user.hierarchy == 'SUPERADMIN'):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    message_text = request.POST.get('message', '').strip()
    is_internal = request.POST.get('is_internal') == 'true' and (is_support_agent or request.user.hierarchy == 'SUPERADMIN')
    
    if not message_text:
        return JsonResponse({'success': False, 'error': 'Mensagem não pode estar vazia'})
    
    # Criar mensagem
    message = SupportChatMessage.objects.create(
        chat=chat,
        user=request.user,
        message=message_text,
        is_internal=is_internal
    )
    
    # Atualizar status do chat se necessário
    if chat.status == 'ABERTO' and (is_support_agent or request.user.hierarchy == 'SUPERADMIN'):
        chat.status = 'EM_ANDAMENTO'
        chat.assigned_to = request.user
        chat.save()
    
    return JsonResponse({
        'success': True,
        'message': {
            'id': message.id,
            'user': {
                'id': request.user.id,
                'name': request.user.get_full_name(),
                'avatar': request.user.first_name[0].upper() if request.user.first_name else 'U',
                'is_support': hasattr(request.user, 'support_agent')
            },
            'message': message.message,
            'is_internal': message.is_internal,
            'created_at': message.created_at.strftime('%d/%m/%Y %H:%M'),
            'is_own': True
        }
    })


@login_required
def get_sectors(request):
    """Buscar todos os setores para o formulário de suporte"""
    from users.models import Sector
    
    sectors = Sector.objects.all().order_by('name')
    sectors_data = [{'id': s.id, 'name': s.name} for s in sectors]
    
    return JsonResponse({'success': True, 'sectors': sectors_data})


@login_required
def get_sector_categories(request, sector_id):
    """Buscar categorias de um setor específico"""
    from .models_chat import SupportCategory
    
    categories = SupportCategory.objects.filter(
        sector_id=sector_id, 
        is_active=True
    ).order_by('name')
    
    categories_data = [{'id': c.id, 'name': c.name, 'description': c.description} for c in categories]
    
    return JsonResponse({'success': True, 'categories': categories_data})


@login_required
@require_POST
def upload_chat_file(request, chat_id):
    """Upload de arquivos no chat de suporte"""
    import json
    from .models_chat import SupportChatFile
    
    chat = get_object_or_404(SupportChat, id=chat_id)
    
    # Verificar se pode enviar arquivos neste chat
    if chat.user != request.user and not hasattr(request.user, 'support_agent'):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    if 'file' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'Nenhum arquivo enviado'})
    
    uploaded_file = request.FILES['file']
    
    # Determinar tipo do arquivo
    file_type = 'DOCUMENT'
    if uploaded_file.content_type.startswith('image/'):
        file_type = 'IMAGE'
    elif uploaded_file.content_type.startswith('video/'):
        file_type = 'VIDEO'
    elif uploaded_file.content_type.startswith('audio/'):
        file_type = 'AUDIO'
    
    # Criar mensagem primeiro
    message_text = request.POST.get('message', f'Arquivo enviado: {uploaded_file.name}')
    message = SupportChatMessage.objects.create(
        chat=chat,
        user=request.user,
        message=message_text
    )
    
    # Criar arquivo
    chat_file = SupportChatFile.objects.create(
        chat=chat,
        message=message,
        file=uploaded_file,
        file_type=file_type,
        original_name=uploaded_file.name,
        file_size=uploaded_file.size
    )
    
    return JsonResponse({
        'success': True,
        'file': {
            'id': chat_file.id,
            'type': file_type,
            'name': chat_file.original_name,
            'size': chat_file.file_size,
            'url': chat_file.file.url
        }
    })


@login_required
@require_POST
def rate_support_chat(request, chat_id):
    """Avaliar atendimento de suporte"""
    import json
    from .models_chat import SupportChatRating
    
    chat = get_object_or_404(SupportChat, id=chat_id)
    
    # Apenas o dono do chat pode avaliar
    if chat.user != request.user:
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    data = json.loads(request.body)
    rating_value = data.get('rating')
    feedback = data.get('feedback', '')
    
    if not rating_value or rating_value not in [1, 2, 3, 4, 5]:
        return JsonResponse({'success': False, 'error': 'Avaliação inválida'})
    
    # Criar ou atualizar avaliação
    rating, created = SupportChatRating.objects.get_or_create(
        chat=chat,
        defaults={'rating': rating_value, 'feedback': feedback}
    )
    
    if not created:
        rating.rating = rating_value
        rating.feedback = feedback
        rating.save()
    
    # Se a avaliação for ruim (1 ou 2), reabrir o chat
    if rating_value <= 2:
        chat.status = 'ABERTO'
        chat.assigned_to = None
        chat.save()
        
        # Criar mensagem automática
        SupportChatMessage.objects.create(
            chat=chat,
            user=request.user,
            message=f"Chat reaberto devido à avaliação negativa. Feedback: {feedback}" if feedback else "Chat reaberto devido à avaliação negativa."
        )
        
        return JsonResponse({
            'success': True, 
            'message': 'Avaliação registrada. O chat foi reaberto para nova análise.',
            'reopened': True
        })
    else:
        chat.status = 'FECHADO'
        chat.closed_at = timezone.now()
        chat.save()
        
        return JsonResponse({
            'success': True, 
            'message': 'Obrigado pela sua avaliação! O chat foi finalizado.',
            'closed': True
        })


@login_required
def support_admin_dashboard(request):
    """Dashboard administrativo do suporte"""
    # Verificar permissões de admin
    if not (request.user.hierarchy in ['ADMIN', 'SUPERADMIN'] or hasattr(request.user, 'support_agent')):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    from django.db.models import Count, Q, Avg
    from .models_chat import SupportChat, SupportChatRating
    
    # Estatísticas
    total_chats = SupportChat.objects.count()
    open_chats = SupportChat.objects.filter(status='ABERTO').count()
    in_progress_chats = SupportChat.objects.filter(status='EM_ANDAMENTO').count()
    resolved_chats = SupportChat.objects.filter(status='RESOLVIDO').count()
    
    # Chats por prioridade
    priority_stats = SupportChat.objects.values('priority').annotate(count=Count('id'))
    
    # Avaliação média
    avg_rating = SupportChatRating.objects.aggregate(avg=Avg('rating'))['avg'] or 0
    
    # Chats recentes
    recent_chats = SupportChat.objects.select_related('user', 'assigned_to', 'sector').order_by('-created_at')[:20]
    
    # Agentes de suporte
    agents = SupportAgent.objects.filter(is_active=True).select_related('user')
    
    context = {
        'stats': {
            'total': total_chats,
            'open': open_chats,
            'in_progress': in_progress_chats,
            'resolved': resolved_chats,
            'avg_rating': round(avg_rating, 1)
        },
        'priority_stats': list(priority_stats),
        'recent_chats': recent_chats,
        'agents': agents
    }
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse(context)
    
    return render(request, 'support/admin_dashboard.html', context)


@login_required
def manage_support_categories(request):
    """Gerenciar categorias de suporte"""
    # Verificar permissões de admin
    if not (request.user.hierarchy in ['ADMIN', 'SUPERADMIN']):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    from .models_chat import SupportCategory
    from core.models import Sector
    
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        
        if data.get('action') == 'create':
            category = SupportCategory.objects.create(
                name=data['name'],
                sector_id=data['sector_id'],
                description=data.get('description', '')
            )
            return JsonResponse({'success': True, 'category_id': category.id})
        
        elif data.get('action') == 'update':
            category = get_object_or_404(SupportCategory, id=data['category_id'])
            category.name = data['name']
            category.description = data.get('description', '')
            category.save()
            return JsonResponse({'success': True})
        
        elif data.get('action') == 'delete':
            category = get_object_or_404(SupportCategory, id=data['category_id'])
            category.delete()
            return JsonResponse({'success': True})
    
    categories = SupportCategory.objects.select_related('sector').order_by('sector__name', 'name')
    sectors = Sector.objects.filter(is_active=True).order_by('name')
    
    return render(request, 'support/manage_categories.html', {
        'categories': categories,
        'sectors': sectors
    })


@login_required
def manage_support_agents(request):
    """Gerenciar agentes de suporte"""
    # Verificar permissões de admin
    if not (request.user.hierarchy in ['ADMIN', 'SUPERADMIN']):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    from .models_chat import SupportAgent
    
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        
        if data.get('action') == 'create':
            user = get_object_or_404(User, id=data['user_id'])
            agent, created = SupportAgent.objects.get_or_create(
                user=user,
                defaults={'can_assign_tickets': data.get('can_assign_tickets', False)}
            )
            return JsonResponse({'success': True, 'created': created})
        
        elif data.get('action') == 'update':
            agent = get_object_or_404(SupportAgent, id=data['agent_id'])
            agent.can_assign_tickets = data.get('can_assign_tickets', False)
            agent.is_active = data.get('is_active', True)
            agent.save()
            return JsonResponse({'success': True})
        
        elif data.get('action') == 'delete':
            agent = get_object_or_404(SupportAgent, id=data['agent_id'])
            agent.delete()
            return JsonResponse({'success': True})
    
    agents = SupportAgent.objects.select_related('user').order_by('user__first_name')
    available_users = User.objects.filter(
        is_active=True,
        hierarchy__in=['ADMIN', 'SUPERVISOR', 'FUNCIONARIO']
    ).exclude(support_agent__isnull=False).order_by('first_name')
    
    return render(request, 'support/manage_agents.html', {
        'agents': agents,
        'available_users': available_users
    })


def support_admin_template(request):
    """Template do dashboard administrativo"""
    if not request.user.is_staff:
        return redirect('core:home')
    
    # Estatísticas básicas para o template
    stats = {
        'total': SupportChat.objects.count(),
        'open': SupportChat.objects.filter(status='ABERTO').count(),
        'in_progress': SupportChat.objects.filter(status='EM_ANDAMENTO').count(),
        'resolved': SupportChat.objects.filter(status='RESOLVIDO').count(),
        'avg_rating': round(SupportChatRating.objects.aggregate(
            avg_rating=models.Avg('rating')
        )['avg_rating'] or 0, 1)
    }
    
    return render(request, 'support/admin_dashboard.html', {
        'stats': stats
    })


def assign_chat_to_agent(request, chat_id):
    """Atribui um chat a um agente"""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    if request.method == 'POST':
        try:
            chat = SupportChat.objects.get(id=chat_id)
            chat.assigned_to = request.user
            chat.status = 'EM_ANDAMENTO'
            chat.save()
            
            return JsonResponse({'success': True})
        except SupportChat.DoesNotExist:
            return JsonResponse({'error': 'Chat não encontrado'}, status=404)
    
    return JsonResponse({'error': 'Método inválido'}, status=405)


def support_metrics(request):
    """Métricas de suporte para supervisores"""
    if not (request.user.is_superuser or request.user.hierarchy == 'SUPERADMIN'):
        return redirect('core:home')
    
    from datetime import datetime, timedelta
    from django.db.models import Count, Avg, Q
    import json
    
    # Período selecionado (padrão: 30 dias)
    period_days = int(request.GET.get('period', 30))
    start_date = datetime.now() - timedelta(days=period_days)
    
    # Estatísticas principais
    total_tickets = SupportChat.objects.filter(created_at__gte=start_date).count()
    resolved_tickets = SupportChat.objects.filter(
        created_at__gte=start_date, 
        status='FECHADO'
    ).count()
    
    resolution_rate = round((resolved_tickets / total_tickets * 100) if total_tickets > 0 else 0, 1)
    
    # Tempo médio de resposta (simulado - seria calculado com base nas mensagens)
    avg_response_time = round(4.5, 1)  # Em horas
    
    # Avaliação média
    avg_rating = round(SupportChatRating.objects.filter(
        chat__created_at__gte=start_date
    ).aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0, 1)
    
    # Dados para gráficos (simulados para demonstração)
    daily_labels = []
    daily_tickets = []
    daily_resolved = []
    
    for i in range(7):  # Últimos 7 dias
        date = datetime.now() - timedelta(days=i)
        daily_labels.insert(0, date.strftime('%d/%m'))
        daily_tickets.insert(0, SupportChat.objects.filter(
            created_at__date=date.date()
        ).count())
        daily_resolved.insert(0, SupportChat.objects.filter(
            created_at__date=date.date(),
            status='FECHADO'
        ).count())
    
    # Distribuição por status
    status_data = [
        SupportChat.objects.filter(status='ABERTO').count(),
        SupportChat.objects.filter(status='EM_ANDAMENTO').count(),
        SupportChat.objects.filter(status='RESOLVIDO').count(),
        SupportChat.objects.filter(status='FECHADO').count(),
    ]
    
    # Top categorias
    top_categories = SupportCategory.objects.annotate(
        count=Count('supportchat')
    ).order_by('-count')[:5]
    
    categories_data = []
    total_cats = sum(cat.count for cat in top_categories)
    for cat in top_categories:
        percentage = round((cat.count / total_cats * 100) if total_cats > 0 else 0, 1)
        categories_data.append({
            'name': cat.name,
            'count': cat.count,
            'percentage': percentage
        })
    
    # Performance dos agentes (simulado)
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    agents = User.objects.filter(is_staff=True)[:5]
    agent_performance = []
    for agent in agents:
        tickets_resolved = SupportChat.objects.filter(
            assigned_to=agent,
            status='FECHADO',
            created_at__gte=start_date
        ).count()
        
        avg_agent_rating = SupportChatRating.objects.filter(
            chat__assigned_to=agent,
            chat__created_at__gte=start_date
        ).aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0
        
        agent_performance.append({
            'name': agent.get_full_name(),
            'tickets_resolved': tickets_resolved,
            'avg_rating': round(avg_agent_rating, 1),
            'avg_time': round(3.2 + (agent.id % 3), 1)  # Simulado
        })
    
    # Tickets recentes
    recent_tickets = SupportChat.objects.select_related(
        'user', 'assigned_to'
    ).order_by('-created_at')[:10]
    
    metrics = {
        'total_tickets': total_tickets,
        'tickets_growth': 15,  # Simulado
        'avg_response_time': avg_response_time,
        'time_improvement': 8,  # Simulado
        'resolution_rate': resolution_rate,
        'resolution_improvement': 12,  # Simulado
        'avg_rating': avg_rating,
        'daily_labels': json.dumps(daily_labels),
        'daily_tickets': json.dumps(daily_tickets),
        'daily_resolved': json.dumps(daily_resolved),
        'status_labels': json.dumps(['Abertos', 'Em Andamento', 'Resolvidos', 'Fechados']),
        'status_data': json.dumps(status_data),
        'top_categories': categories_data,
        'agent_performance': agent_performance,
        'recent_tickets': recent_tickets
    }
    
    if request.headers.get('Accept') == 'application/json':
        return JsonResponse(metrics)
    
    return render(request, 'support/metrics_dashboard.html', {
        'metrics': metrics
    })


def export_metrics_report(request):
    """Exporta relatório de métricas em PDF/Excel"""
    if not (request.user.is_superuser or request.user.hierarchy == 'SUPERADMIN'):
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    # Implementar exportação
    return JsonResponse({'message': 'Exportação não implementada ainda'})