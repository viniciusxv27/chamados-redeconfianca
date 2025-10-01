from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db.models import Q
from django.contrib import messages

from .models import Activity
from .models_chat import TaskChat, TaskChatMessage, SupportChat, SupportChatMessage, SupportAgent
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
    priority = request.POST.get('priority', 'MEDIA')
    
    if not title or not message:
        return JsonResponse({'success': False, 'error': 'Título e mensagem são obrigatórios'})
    
    # Criar chat
    chat = SupportChat.objects.create(
        user=request.user,
        title=title,
        priority=priority
    )
    
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