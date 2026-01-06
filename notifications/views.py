from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, Count
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from .models import PushNotification, NotificationCategory, UserNotification, DeviceToken
from users.models import Sector, User
import json
import logging

logger = logging.getLogger(__name__)


def is_superuser_or_admin(user):
    """Verifica se o usuário é superuser ou admin"""
    return user.is_superuser or user.can_view_all_tickets()


@login_required
def api_unread_count(request):
    """API para retornar contagem de notificações não lidas"""
    
    unread_count = UserNotification.objects.filter(
        user=request.user,
        is_read=False
    ).count()
    
    return JsonResponse({
        'unread_count': unread_count,
        'success': True
    })


@login_required
def api_recent_notifications(request):
    """API para retornar notificações recentes do usuário"""
    
    recent_notifications = UserNotification.objects.filter(
        user=request.user
    ).select_related(
        'notification', 'notification__category'
    ).order_by('-created_at')[:10]
    
    notifications_data = []
    for user_notification in recent_notifications:
        notification = user_notification.notification
        notifications_data.append({
            'id': user_notification.id,
            'title': notification.title,
            'message': notification.message,  # Não truncar - será tratado no frontend
            'message_preview': notification.message[:100] + ('...' if len(notification.message) > 100 else ''),  # Preview para dropdown
            'type': {
                'name': notification.get_notification_type_display(),
                'icon': notification.category.icon if notification.category else notification.icon,
                'color': notification.category.color if notification.category else 'blue',
            },
            'priority': notification.get_priority_display(),
            'is_read': user_notification.is_read,
            'sent_at': user_notification.created_at.isoformat(),
            'action_url': notification.action_url,
            'action_text': notification.action_text,
        })
    
    return JsonResponse({
        'notifications': notifications_data,
        'success': True
    })


@login_required
def api_mark_as_read(request, notification_id):
    """API para marcar notificação como lida"""
    
    if request.method == 'POST':
        try:
            user_notification = UserNotification.objects.get(
                id=notification_id,
                user=request.user
            )
            user_notification.mark_as_read()
            return JsonResponse({'success': True})
        except UserNotification.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Notificação não encontrada'})
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})


@login_required
def api_mark_all_as_read(request):
    """API para marcar todas as notificações como lidas"""
    
    if request.method == 'POST':
        try:
            updated = UserNotification.objects.filter(
                user=request.user,
                is_read=False
            ).update(
                is_read=True,
                read_at=timezone.now()
            )
            
            return JsonResponse({
                'success': True,
                'updated_count': updated
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': str(e)
            })
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'})


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
        subscription = data.get('subscription')
        device_info = data.get('device_info', {})
        
        if not subscription:
            return JsonResponse({
                'success': False,
                'error': 'Subscription é obrigatório'
            }, status=400)
        
        # Converter subscription para string JSON
        token = json.dumps(subscription)
        
        # Criar ou atualizar token
        device_token, created = DeviceToken.objects.get_or_create(
            user=request.user,
            token=token,
            defaults={
                'device_type': 'WEB',
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


@csrf_exempt
@require_POST
def delete_device_token(request, token_id):
    """Deletar token do dispositivo"""
    if not request.user.is_authenticated:
        return JsonResponse({
            'success': False,
            'error': 'Usuário não autenticado'
        }, status=401)
    
    try:
        device_token = DeviceToken.objects.get(
            id=token_id,
            user=request.user
        )
        device_token.delete()
        
        return JsonResponse({
            'success': True,
            'message': 'Dispositivo removido com sucesso'
        })
        
    except DeviceToken.DoesNotExist:
        return JsonResponse({
            'success': False,
            'error': 'Dispositivo não encontrado'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@login_required
def notification_settings(request):
    """Configurações de notificações do usuário"""
    from django.conf import settings
    from .models import NotificationPreference
    
    # Obter ou criar preferências do usuário
    preferences, created = NotificationPreference.objects.get_or_create(user=request.user)
    
    if request.method == 'POST':
        # Atualizar preferências
        preferences.in_app_enabled = request.POST.get('in_app_enabled') == 'on'
        preferences.push_enabled = request.POST.get('push_enabled') == 'on'
        preferences.email_enabled = request.POST.get('email_enabled') == 'on'
        
        preferences.ticket_created = request.POST.get('ticket_created') == 'on'
        preferences.ticket_assigned = request.POST.get('ticket_assigned') == 'on'
        preferences.ticket_status_changed = request.POST.get('ticket_status_changed') == 'on'
        preferences.ticket_comment = request.POST.get('ticket_comment') == 'on'
        preferences.communication_new = request.POST.get('communication_new') == 'on'
        
        preferences.quiet_hours_enabled = request.POST.get('quiet_hours_enabled') == 'on'
        
        quiet_start = request.POST.get('quiet_hours_start')
        quiet_end = request.POST.get('quiet_hours_end')
        
        if quiet_start:
            from datetime import datetime
            preferences.quiet_hours_start = datetime.strptime(quiet_start, '%H:%M').time()
        if quiet_end:
            from datetime import datetime
            preferences.quiet_hours_end = datetime.strptime(quiet_end, '%H:%M').time()
        
        preferences.save()
        messages.success(request, 'Preferências de notificação salvas com sucesso!')
        return redirect('notifications:settings')
    
    context = {
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
        'user_tokens': DeviceToken.objects.filter(user=request.user, is_active=True),
        'user': request.user,
        'preferences': preferences
    }
    
    return render(request, 'notifications/settings_simple.html', context)


@login_required
def test_push_page(request):
    """Página de teste para push notifications"""
    from django.conf import settings
    
    context = {
        'vapid_public_key': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
    }
    
    return render(request, 'notifications/test_push.html', context)


@csrf_exempt
@login_required
def test_push_notification(request):
    """Enviar notificação de teste para o usuário atual"""
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'error': 'Método não permitido'
        }, status=405)
        
    try:
        # Debug headers
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Test push request from user: {request.user.id}")
        logger.info(f"Request method: {request.method}")
        logger.info(f"Content type: {request.content_type}")
        
        # Handle FormData from the frontend
        title = request.POST.get('title', 'Teste de Notificação')
        message = request.POST.get('message', 'Esta é uma notificação de teste!')
        
        # Verificar se o usuário tem tokens ativos
        device_tokens = DeviceToken.objects.filter(user=request.user, is_active=True)
        logger.info(f"Found {device_tokens.count()} device tokens for user {request.user.id}")
        
        if not device_tokens.exists():
            return JsonResponse({
                'success': False,
                'error': 'Nenhum dispositivo registrado para notificações push. Registre um dispositivo primeiro clicando em "Ativar Notificações Push".',
                'need_registration': True
            }, status=200)  # Retornar 200 em vez de 400 para melhor UX
        
        # Enviar push notification diretamente
        from .push_utils import send_push_notification_to_user
        
        result = send_push_notification_to_user(
            request.user,
            title,
            message,
            notification_id=None,
            action_url='/',
            icon='/static/images/logo.png'
        )
        
        if result['success'] or result['sent_count'] > 0:
            return JsonResponse({
                'success': True,
                'message': f'Notificação enviada para {result["sent_count"]} dispositivo(s)'
            })
        else:
            return JsonResponse({
                'success': False,
                'error': result.get('message', 'Erro ao enviar notificação')
            }, status=400)
            
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

@csrf_exempt
def api_vapid_key(request):
    """API endpoint para obter chave VAPID pública"""
    try:
        from django.conf import settings
        vapid_public_key = getattr(settings, 'VAPID_PUBLIC_KEY', 'BP8QQHATvKzPC7VGShrzb6BPdroXgHj_TGJHo7jqr-hOQn5xg6q0VkQyajx7wEwHvBaS7kYiwF4oDm7X5VjFgSg')
        
        return JsonResponse({
            'success': True,
            'vapid_public_key': vapid_public_key
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=400)


@csrf_exempt
def api_subscribe_push(request):
    """API endpoint para subscrever às notificações push"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'error': 'User not authenticated'}, status=401)
    
    try:
        data = json.loads(request.body)
        subscription = data.get('subscription', {})
        device_type = data.get('device_type', 'WEB')
        
        if not subscription:
            return JsonResponse({
                'success': False,
                'error': 'Subscription data required'
            }, status=400)
        
        if not subscription.get('endpoint'):
            return JsonResponse({
                'success': False,
                'error': 'Invalid subscription format - missing endpoint'
            }, status=400)
        
        token_str = json.dumps(subscription)
        
        device_token, created = DeviceToken.objects.get_or_create(
            user=request.user,
            token=token_str,
            defaults={
                'device_type': device_type,
                'is_active': True,
                'device_info': data.get('device_info', {})
            }
        )
        
        if not created:
            device_token.device_type = device_type
            device_token.is_active = True
            device_token.device_info = data.get('device_info', {})
            device_token.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Push notifications enabled successfully'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


# =============================================================================
# ONESIGNAL INTEGRATION VIEWS
# =============================================================================

@login_required
def onesignal_dashboard(request):
    """Dashboard de configuração do OneSignal (apenas SUPERADMINs)"""
    if request.user.hierarchy != 'SUPERADMIN':
        messages.error(request, 'Apenas SUPERADMINs podem acessar esta página.')
        return redirect('dashboard')
    
    from .onesignal_service import onesignal_service
    from .models import OneSignalPlayer, OneSignalNotificationLog
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    # Verificar configuração
    is_configured = onesignal_service.enabled
    
    # Obter estatísticas se configurado
    stats = None
    player_count = 0
    segments = []
    recent_notifications = []
    
    # Estatísticas de usuários
    total_active_users = User.objects.filter(is_active=True).count()
    
    # Consultas separadas para evitar erro de slice
    onesignal_players_qs = OneSignalPlayer.objects.select_related('user', 'user__sector').filter(user__isnull=False)
    registered_users = onesignal_players_qs.exclude(player_id__startswith='pending_').count()
    pending_users = onesignal_players_qs.filter(player_id__startswith='pending_').count()
    total_synced = onesignal_players_qs.count()
    onesignal_players = onesignal_players_qs.order_by('-created_at')[:100]  # Mostrar últimos 100 na lista
    
    # Estatísticas de notificações
    total_notifications = OneSignalNotificationLog.objects.count()
    successful_notifications = OneSignalNotificationLog.objects.filter(success=True).count()
    success_rate = round((successful_notifications / total_notifications * 100) if total_notifications > 0 else 0)
    
    if is_configured:
        # Obter contagem de players
        count_result = onesignal_service.get_player_count()
        if count_result.get('success'):
            player_count = count_result.get('count', 0)
        
        # Obter segmentos
        segments_result = onesignal_service.get_segments()
        if segments_result.get('success'):
            segments = segments_result.get('segments', [])
        
        # Obter notificações recentes
        notifications_result = onesignal_service.get_notifications(limit=10)
        if notifications_result.get('success'):
            recent_notifications = notifications_result.get('notifications', [])
    
    context = {
        'is_configured': is_configured,
        'stats': stats,
        'player_count': player_count,
        'segments': segments,
        'recent_notifications': recent_notifications,
        'onesignal_app_id': onesignal_service.app_id if is_configured else '',
        'total_active_users': total_active_users,
        'onesignal_players': onesignal_players,
        'registered_users': registered_users,
        'pending_users': pending_users,
        'total_synced': total_synced,
        'total_notifications': total_notifications,
        'success_rate': success_rate,
    }
    
    return render(request, 'notifications/onesignal_dashboard.html', context)


@login_required
@require_POST
def onesignal_send_notification(request):
    """API para enviar notificação via OneSignal (apenas SUPERADMINs)"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({
            'success': False,
            'error': 'Permissão negada'
        }, status=403)
    
    try:
        from .onesignal_service import onesignal_service
        
        data = json.loads(request.body)
        
        title = data.get('title', '')
        message = data.get('message', '')
        url = data.get('url', '/')
        segment = data.get('segment', 'Subscribed Users')
        icon = data.get('icon')
        image = data.get('image')
        
        if not title or not message:
            return JsonResponse({
                'success': False,
                'error': 'Título e mensagem são obrigatórios'
            }, status=400)
        
        # Enviar notificação
        result = onesignal_service.send_notification(
            title=title,
            message=message,
            url=url,
            segment=segment,
            icon=icon,
            image=image,
            sent_by=request.user
        )
        
        if result.get('success'):
            # Registrar a notificação no sistema local também
            try:
                category, _ = NotificationCategory.objects.get_or_create(
                    name='OneSignal',
                    defaults={'icon': 'fas fa-broadcast-tower', 'color': 'purple'}
                )
                
                notification = PushNotification.objects.create(
                    title=title,
                    message=message,
                    category=category,
                    notification_type='CUSTOM',
                    priority='NORMAL',
                    icon='fas fa-broadcast-tower',
                    action_url=url,
                    created_by=request.user,
                    send_to_all=True,
                    is_sent=True,
                    sent_at=timezone.now(),
                    extra_data={'onesignal': True, 'notification_id': result.get('notification_id'), 'recipients': result.get('recipients', 0)}
                )
            except Exception as e:
                logger.error(f"Erro ao registrar notificação OneSignal localmente: {e}")
            
            return JsonResponse({
                'success': True,
                'message': 'Notificação enviada com sucesso via OneSignal',
                'sent_count': result.get('recipients', 0),
                'notification_id': result.get('notification_id')
            })
        else:
            return JsonResponse({
                'success': False,
                'error': result.get('error', 'Erro ao enviar notificação')
            }, status=400)
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'JSON inválido'
        }, status=400)
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def onesignal_stats(request):
    """API para obter estatísticas do OneSignal"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({
            'success': False,
            'error': 'Permissão negada'
        }, status=403)
    
    from .onesignal_service import onesignal_service
    
    result = onesignal_service.get_app_info()
    
    return JsonResponse(result)


@login_required
def onesignal_player_count(request):
    """API para obter contagem de players OneSignal"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({
            'success': False,
            'error': 'Permissão negada'
        }, status=403)
    
    from .onesignal_service import onesignal_service
    
    result = onesignal_service.get_player_count()
    
    return JsonResponse(result)


@login_required
def onesignal_segments(request):
    """API para listar segmentos do OneSignal"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({
            'success': False,
            'error': 'Permissão negada'
        }, status=403)
    
    from .onesignal_service import onesignal_service
    
    result = onesignal_service.get_segments()
    
    return JsonResponse(result)


@login_required
def onesignal_debug(request):
    """Debug endpoint para verificar status do OneSignal"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({'error': 'Permissão negada'}, status=403)
    
    from .onesignal_service import onesignal_service
    import requests
    from django.conf import settings
    
    app_id = getattr(settings, 'ONESIGNAL_APP_ID', '')
    api_key = getattr(settings, 'ONESIGNAL_REST_API_KEY', '')
    
    debug_info = {
        'app_id': app_id[:10] + '...' if app_id else 'Não configurado',
        'api_key': api_key[:15] + '...' if api_key else 'Não configurado',
        'service_enabled': onesignal_service.enabled,
    }
    
    # Testar API do OneSignal
    try:
        headers = {
            'Authorization': f'Basic {api_key}',
            'Content-Type': 'application/json'
        }
        
        # Obter info do app
        app_response = requests.get(
            f'https://onesignal.com/api/v1/apps/{app_id}',
            headers=headers,
            timeout=10
        )
        debug_info['app_api_status'] = app_response.status_code
        
        if app_response.status_code == 200:
            app_data = app_response.json()
            debug_info['total_players'] = app_data.get('players', 0)
            debug_info['messageable_players'] = app_data.get('messageable_players', 0)
            debug_info['app_name'] = app_data.get('name', 'N/A')
        else:
            debug_info['app_api_error'] = app_response.text[:500]
        
        # Testar envio de notificação de teste (sem enviar)
        test_payload = {
            'app_id': app_id,
            'headings': {'en': 'Teste'},
            'contents': {'en': 'Mensagem de teste'},
            'included_segments': ['Subscribed Users'],
            # dry_run não existe no OneSignal, mas podemos verificar a resposta
        }
        
        # Verificar quantos usuários receberiam
        debug_info['test_segment'] = 'Subscribed Users'
        
    except Exception as e:
        debug_info['error'] = str(e)
    
    return JsonResponse(debug_info)


@csrf_exempt
def api_onesignal_config(request):
    """API para obter configuração do OneSignal para o cliente"""
    from django.conf import settings
    
    app_id = getattr(settings, 'ONESIGNAL_APP_ID', '')
    
    if not app_id:
        return JsonResponse({
            'success': False,
            'enabled': False,
            'message': 'OneSignal não configurado'
        })
    
    return JsonResponse({
        'success': True,
        'enabled': True,
        'app_id': app_id,
        'safari_web_id': getattr(settings, 'ONESIGNAL_SAFARI_WEB_ID', '')
    })


@login_required
@require_POST
def api_onesignal_register_player(request):
    """
    API para registrar/atualizar o player_id do OneSignal vinculado ao usuário logado.
    Chamado pelo frontend quando o usuário se inscreve nas notificações push.
    
    Os dados registrados são:
    - player_id: ID único do dispositivo no OneSignal
    - external_user_id: ID do usuário no sistema (para notificações direcionadas)
    - email: Email do usuário (para identificação e notificações por email)
    - phone: Telefone do usuário (para SMS, se configurado)
    """
    from .models import OneSignalPlayer
    
    try:
        data = json.loads(request.body)
        player_id = data.get('player_id', '').strip()
        device_type = data.get('device_type', 'web')
        browser = data.get('browser', '')
        
        if not player_id:
            return JsonResponse({
                'success': False,
                'error': 'player_id é obrigatório'
            }, status=400)
        
        # Preparar dados do usuário
        user = request.user
        external_user_id = str(user.id)
        user_email = user.email or ''
        user_phone = user.phone or ''
        
        # Formatar telefone no padrão E.164 se disponível
        if user_phone:
            phone_digits = ''.join(filter(str.isdigit, user_phone))
            if len(phone_digits) >= 10:
                user_phone = f"+55{phone_digits}" if not phone_digits.startswith('55') else f"+{phone_digits}"
            else:
                user_phone = ''
        
        # Verificar se já existe um registro com este player_id
        existing_player = OneSignalPlayer.objects.filter(player_id=player_id).first()
        
        if existing_player:
            # Atualizar registro existente
            if existing_player.user != user:
                # Player_id mudou de dono - atualizar para o novo usuário
                existing_player.user = user
            existing_player.external_user_id = external_user_id
            existing_player.email = user_email
            existing_player.phone = user_phone
            existing_player.device_type = device_type
            existing_player.browser = browser
            existing_player.is_active = True
            existing_player.extra_data = {
                'registered_at': timezone.now().isoformat(),
                'user_name': user.get_full_name(),
                'hierarchy': user.hierarchy,
                'sector': user.sector.name if user.sector else None
            }
            existing_player.save()
            
            logger.info(f"OneSignal player updated: {player_id} for user {user.id} ({user.email})")
            
            return JsonResponse({
                'success': True,
                'message': 'Player atualizado com sucesso',
                'player_id': player_id,
                'external_user_id': external_user_id,
                'updated': True
            })
        else:
            # Verificar se o usuário já tem um registro pendente
            pending_player = OneSignalPlayer.objects.filter(
                user=user,
                player_id__startswith='pending_'
            ).first()
            
            if pending_player:
                # Atualizar o registro pendente com o player_id real
                pending_player.player_id = player_id
                pending_player.external_user_id = external_user_id
                pending_player.email = user_email
                pending_player.phone = user_phone
                pending_player.device_type = device_type
                pending_player.browser = browser
                pending_player.is_active = True
                pending_player.extra_data = {
                    'registered_at': timezone.now().isoformat(),
                    'user_name': user.get_full_name(),
                    'hierarchy': user.hierarchy,
                    'sector': user.sector.name if user.sector else None
                }
                pending_player.save()
                
                logger.info(f"OneSignal pending player activated: {player_id} for user {user.id} ({user.email})")
            else:
                # Criar novo registro
                OneSignalPlayer.objects.create(
                    user=user,
                    player_id=player_id,
                    external_user_id=external_user_id,
                    email=user_email,
                    phone=user_phone,
                    device_type=device_type,
                    browser=browser,
                    is_active=True,
                    extra_data={
                        'registered_at': timezone.now().isoformat(),
                        'user_name': user.get_full_name(),
                        'hierarchy': user.hierarchy,
                        'sector': user.sector.name if user.sector else None
                    }
                )
                
                logger.info(f"OneSignal player created: {player_id} for user {user.id} ({user.email})")
            
            return JsonResponse({
                'success': True,
                'message': 'Player registrado com sucesso',
                'player_id': player_id,
                'external_user_id': external_user_id,
                'created': True
            })
            
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'JSON inválido'
        }, status=400)
    except Exception as e:
        logger.error(f"Error registering OneSignal player: {e}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def onesignal_sync_users(request):
    """Sincroniza usuários ativos com OneSignal (cria registros pendentes)"""
    if request.user.hierarchy != 'SUPERADMIN':
        return JsonResponse({
            'success': False,
            'error': 'Permissão negada'
        }, status=403)
    
    from users.models import User
    from .models import OneSignalPlayer
    
    # Buscar todos os usuários ativos
    active_users = User.objects.filter(is_active=True)
    
    created_count = 0
    existing_count = 0
    
    for user in active_users:
        # Verificar se já existe registro para este usuário
        existing = OneSignalPlayer.objects.filter(user=user).first()
        
        if existing:
            existing_count += 1
        else:
            # Criar registro pendente (aguardando usuário se registrar no navegador)
            OneSignalPlayer.objects.create(
                user=user,
                player_id=f'pending_{user.id}',
                is_active=False  # Será ativado quando o usuário se registrar
            )
            created_count += 1
    
    return JsonResponse({
        'success': True,
        'message': f'Sincronização concluída',
        'created': created_count,
        'existing': existing_count,
        'total_users': active_users.count()
    })


# Legacy Truepush views (redirects to OneSignal)
@login_required
def truepush_dashboard(request):
    """Redirect para OneSignal dashboard"""
    return redirect('notifications:onesignal_dashboard')

@login_required
@require_POST
def truepush_send_notification(request):
    """Redirect para OneSignal"""
    return onesignal_send_notification(request)

@login_required
def truepush_stats(request):
    """Redirect para OneSignal"""
    return onesignal_stats(request)

@login_required
def truepush_subscriber_count(request):
    """Redirect para OneSignal"""
    return onesignal_player_count(request)

@login_required
def truepush_segments(request):
    """Redirect para OneSignal"""
    return onesignal_segments(request)

@csrf_exempt
def api_truepush_config(request):
    """Redirect para OneSignal config"""
    return api_onesignal_config(request)
