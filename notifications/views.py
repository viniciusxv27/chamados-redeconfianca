from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q, Count
from django.core.paginator import Paginator
import json

from .models import PushNotification, UserNotification, DeviceToken, NotificationCategory
from users.models import User, Sector


@login_required
def notifications_dashboard(request):
    """Dashboard de notificações do usuário"""
    user = request.user
    
    # Notificações do usuário (últimas 50)
    user_notifications = UserNotification.objects.filter(
        user=user
    ).select_related('notification', 'notification__category').order_by('-created_at')[:50]
    
    # Estatísticas
    total_notifications = UserNotification.objects.filter(user=user).count()
    unread_count = UserNotification.objects.filter(user=user, is_read=False).count()
    
    context = {
        'user_notifications': user_notifications,
        'total_notifications': total_notifications,
        'unread_count': unread_count,
    }
    
    return render(request, 'notifications/dashboard.html', context)


@login_required
def manage_notifications(request):
    """Gerenciar notificações (apenas SUPERADMINs)"""
    if request.user.hierarchy != 'SUPERADMIN':
        messages.error(request, 'Apenas SUPERADMINs podem gerenciar notificações.')
        return redirect('dashboard')
    
    # Filtros
    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('type', '')
    search = request.GET.get('search', '')
    
    # Query base
    notifications = PushNotification.objects.all().select_related('created_by', 'category').annotate(
        read_count=Count('user_notifications', filter=Q(user_notifications__is_read=True))
    )
    
    # Aplicar filtros
    if status_filter == 'sent':
        notifications = notifications.filter(is_sent=True)
    elif status_filter == 'pending':
        notifications = notifications.filter(is_sent=False)
    
    if type_filter:
        notifications = notifications.filter(notification_type=type_filter)
    
    if search:
        notifications = notifications.filter(
            Q(title__icontains=search) |
            Q(message__icontains=search)
        )
    
    # Ordenação e paginação
    notifications = notifications.order_by('-created_at')
    paginator = Paginator(notifications, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estatísticas
    total_notifications = PushNotification.objects.count()
    sent_notifications = PushNotification.objects.filter(is_sent=True).count()
    pending_notifications = total_notifications - sent_notifications
    
    context = {
        'page_obj': page_obj,
        'total_notifications': total_notifications,
        'sent_notifications': sent_notifications,
        'pending_notifications': pending_notifications,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'search': search,
        'TYPE_CHOICES': PushNotification.TYPE_CHOICES,
    }
    
    return render(request, 'notifications/manage.html', context)


@login_required
def create_notification(request):
    """Criar nova notificação (apenas SUPERADMINs)"""
    if request.user.hierarchy != 'SUPERADMIN':
        messages.error(request, 'Apenas SUPERADMINs podem criar notificações.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        title = request.POST.get('title')
        message = request.POST.get('message')
        notification_type = request.POST.get('notification_type', 'CUSTOM')
        priority = request.POST.get('priority', 'NORMAL')
        icon = request.POST.get('icon', 'fas fa-bell')
        action_url = request.POST.get('action_url', '')
        action_text = request.POST.get('action_text', '')
        send_to_all = request.POST.get('send_to_all') == 'on'
        category_id = request.POST.get('category')
        schedule_for = request.POST.get('schedule_for')
        
        # Validações
        if not title or not message:
            messages.error(request, 'Título e mensagem são obrigatórios!')
            return redirect('notifications:create')
        
        try:
            # Categoria
            category = None
            if category_id:
                category = NotificationCategory.objects.get(id=category_id)
            
            # Data de agendamento
            schedule_datetime = None
            if schedule_for:
                from datetime import datetime
                schedule_datetime = datetime.fromisoformat(schedule_for.replace('T', ' '))
            
            # Criar notificação
            notification = PushNotification.objects.create(
                title=title,
                message=message,
                category=category,
                notification_type=notification_type,
                priority=priority,
                icon=icon,
                action_url=action_url,
                action_text=action_text,
                send_to_all=send_to_all,
                schedule_for=schedule_datetime,
                is_scheduled=bool(schedule_datetime),
                created_by=request.user
            )
            
            # Setores e usuários alvo (se não for para todos)
            if not send_to_all:
                sector_ids = request.POST.getlist('target_sectors')
                user_ids = request.POST.getlist('target_users')
                
                if sector_ids:
                    sectors = Sector.objects.filter(id__in=sector_ids)
                    notification.target_sectors.set(sectors)
                
                if user_ids:
                    users = User.objects.filter(id__in=user_ids)
                    notification.target_users.set(users)
            
            # Enviar imediatamente se não for agendada
            if not schedule_datetime:
                notification.send_notification()
                messages.success(request, f'Notificação "{title}" criada e enviada com sucesso!')
            else:
                messages.success(request, f'Notificação "{title}" agendada para {schedule_datetime.strftime("%d/%m/%Y %H:%M")}!')
            
            return redirect('notifications:manage')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar notificação: {str(e)}')
    
    # Contexto para o formulário
    categories = NotificationCategory.objects.filter(is_active=True)
    sectors = Sector.objects.all().order_by('name')
    users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    
    context = {
        'categories': categories,
        'sectors': sectors,
        'users': users,
        'TYPE_CHOICES': PushNotification.TYPE_CHOICES,
        'PRIORITY_CHOICES': PushNotification.PRIORITY_CHOICES,
    }
    
    return render(request, 'notifications/create.html', context)


@login_required
@require_POST
def mark_notification_read(request, notification_id):
    """Marcar notificação como lida"""
    try:
        user_notification = get_object_or_404(
            UserNotification,
            id=notification_id,
            user=request.user
        )
        user_notification.mark_as_read()
        
        return JsonResponse({
            'success': True,
            'message': 'Notificação marcada como lida'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@login_required
@require_POST
def mark_all_notifications_read(request):
    """Marcar todas as notificações como lidas"""
    try:
        unread_notifications = UserNotification.objects.filter(
            user=request.user,
            is_read=False
        )
        
        # Atualizar em massa
        unread_notifications.update(
            is_read=True,
            read_at=timezone.now()
        )
        
        count = unread_notifications.count()
        
        return JsonResponse({
            'success': True,
            'message': f'{count} notificações marcadas como lidas'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@login_required
def get_notifications_count(request):
    """Retorna o número de notificações não lidas"""
    unread_count = UserNotification.objects.filter(
        user=request.user,
        is_read=False
    ).count()
    
    return JsonResponse({
        'unread_count': unread_count
    })


@csrf_exempt
@require_POST
def register_device_token(request):
    """Registrar token do dispositivo para push notifications"""
    if not request.user.is_authenticated:
        return JsonResponse({
            'success': False,
            'error': 'Usuário não autenticado'
        }, status=401)
    
    try:
        data = json.loads(request.body)
        token = data.get('token')
        device_type = data.get('device_type', 'WEB')
        device_info = data.get('device_info', {})
        
        if not token:
            return JsonResponse({
                'success': False,
                'error': 'Token é obrigatório'
            }, status=400)
        
        # Criar ou atualizar token
        device_token, created = DeviceToken.objects.get_or_create(
            user=request.user,
            token=token,
            defaults={
                'device_type': device_type,
                'device_info': device_info,
                'is_active': True
            }
        )
        
        if not created:
            device_token.device_info = device_info
            device_token.is_active = True
            device_token.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Token registrado com sucesso'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@login_required
@require_POST 
def send_notification_now(request, notification_id):
    """Enviar notificação agendada imediatamente (apenas SUPERADMINs)"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({
            'success': False,
            'error': 'Permissão negada'
        }, status=403)
    
    try:
        notification = get_object_or_404(PushNotification, id=notification_id)
        
        if notification.is_sent:
            return JsonResponse({
                'success': False,
                'error': 'Notificação já foi enviada'
            }, status=400)
        
        success = notification.send_notification()
        
        if success:
            return JsonResponse({
                'success': True,
                'message': 'Notificação enviada com sucesso'
            })
        else:
            return JsonResponse({
                'success': False,
                'error': 'Erro ao enviar notificação'
            }, status=400)
            
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)