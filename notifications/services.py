"""
Serviço Unificado de Notificações
Suporta múltiplos canais: In-App, Push/WebPush, Email, Browser
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, List, Dict, Any, Optional, Union
from django.conf import settings
from django.core.mail import send_mail, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone
from django.contrib.auth import get_user_model

if TYPE_CHECKING:
    from users.models import User as UserType

User = get_user_model()
logger = logging.getLogger(__name__)


class NotificationChannel:
    """Enum para canais de notificação"""
    IN_APP = 'in_app'
    PUSH = 'push'
    EMAIL = 'email'
    BROWSER = 'browser'
    ONESIGNAL = 'onesignal'
    TRUEPUSH = 'truepush'  # Legacy - use ONESIGNAL
    ALL = 'all'


class NotificationType:
    """Tipos de notificação"""
    TICKET_CREATED = 'ticket_created'
    TICKET_ASSIGNED = 'ticket_assigned'
    TICKET_STATUS_CHANGED = 'ticket_status_changed'
    TICKET_COMMENT = 'ticket_comment'
    TICKET_RESOLVED = 'ticket_resolved'
    COMMUNICATION_NEW = 'communication_new'
    SYSTEM = 'system'
    CUSTOM = 'custom'


class NotificationService:
    """
    Serviço principal para gerenciar notificações em todos os canais.
    Centraliza a lógica de envio para In-App, Push, Email, Browser e OneSignal.
    """
    
    def __init__(self):
        self.email_enabled = self._check_email_config()
        self.push_enabled = self._check_push_config()
        self.onesignal_enabled = self._check_onesignal_config()
        self.truepush_enabled = self._check_truepush_config()  # Legacy
        
    def _check_email_config(self) -> bool:
        """Verifica se email está configurado"""
        return bool(getattr(settings, 'EMAIL_HOST', '')) and \
               getattr(settings, 'EMAIL_BACKEND', '') != 'django.core.mail.backends.console.EmailBackend'
    
    def _check_push_config(self) -> bool:
        """Verifica se push está configurado"""
        return bool(getattr(settings, 'VAPID_PRIVATE_KEY', '')) and \
               bool(getattr(settings, 'VAPID_PUBLIC_KEY', ''))
    
    def _check_onesignal_config(self) -> bool:
        """Verifica se OneSignal está configurado"""
        return bool(getattr(settings, 'ONESIGNAL_APP_ID', '')) and \
               bool(getattr(settings, 'ONESIGNAL_REST_API_KEY', ''))
    
    def _check_truepush_config(self) -> bool:
        """Verifica se Truepush está configurado (Legacy - use OneSignal)"""
        return bool(getattr(settings, 'TRUEPUSH_API_KEY', '')) and \
               bool(getattr(settings, 'TRUEPUSH_PROJECT_ID', ''))
    
    def _filter_by_preferences(self, recipients: List[User], notification_type: str) -> List[User]:
        """Filtra usuários com base nas preferências de tipo de notificação"""
        from .models import NotificationPreference
        
        filtered = []
        for user in recipients:
            try:
                prefs = NotificationPreference.objects.get(user=user)
                if prefs.is_type_enabled(notification_type) and not prefs.is_quiet_hours():
                    filtered.append(user)
            except NotificationPreference.DoesNotExist:
                # Sem preferências = aceita tudo
                filtered.append(user)
        
        return filtered
    
    def _filter_by_channel_preference(self, recipients: List[User], channel: str) -> List[User]:
        """Filtra usuários com base nas preferências de canal"""
        from .models import NotificationPreference
        
        filtered = []
        for user in recipients:
            try:
                prefs = NotificationPreference.objects.get(user=user)
                if prefs.is_channel_enabled(channel):
                    filtered.append(user)
            except NotificationPreference.DoesNotExist:
                # Sem preferências = aceita tudo
                filtered.append(user)
        
        return filtered
    
    def send_notification(
        self,
        recipients: Union[User, List[User]],
        title: str,
        message: str,
        notification_type: str = NotificationType.CUSTOM,
        channels: List[str] = None,
        action_url: str = '/',
        priority: str = 'NORMAL',
        icon: str = 'fas fa-bell',
        extra_data: Dict = None,
        created_by: User = None,
        email_template: str = None,
        email_context: Dict = None,
        respect_preferences: bool = True
    ) -> Dict[str, Any]:
        """
        Envia notificação por múltiplos canais.
        
        Args:
            recipients: Usuário(s) que receberão a notificação
            title: Título da notificação
            message: Corpo da mensagem
            notification_type: Tipo da notificação (ver NotificationType)
            channels: Lista de canais (in_app, push, email, browser). Se None, usa todos.
            action_url: URL para redirecionar ao clicar
            priority: BAIXA, NORMAL, ALTA, URGENTE
            icon: Ícone Font Awesome
            extra_data: Dados extras em JSON
            created_by: Usuário que criou a notificação
            email_template: Template customizado para email
            email_context: Contexto adicional para o email
            respect_preferences: Se True, respeita as preferências de cada usuário
            
        Returns:
            Dict com resultado de cada canal
        """
        # Normalizar recipients para lista
        if isinstance(recipients, User):
            recipients = [recipients]
        
        # Filtrar apenas usuários ativos
        recipients = [u for u in recipients if u.is_active]
        
        if not recipients:
            return {'success': True, 'message': 'Nenhum destinatário ativo', 'results': {}}
        
        # Filtrar por preferências se necessário
        if respect_preferences:
            recipients = self._filter_by_preferences(recipients, notification_type)
            
            if not recipients:
                return {'success': True, 'message': 'Todos destinatários desabilitaram este tipo de notificação', 'results': {}}
        
        # Definir canais padrão
        if channels is None:
            channels = [NotificationChannel.IN_APP, NotificationChannel.PUSH]
            # Adicionar OneSignal como canal padrão para notificações push direcionadas
            if self.onesignal_enabled:
                channels.append(NotificationChannel.ONESIGNAL)
            # Adicionar email apenas para notificações importantes
            if priority in ['ALTA', 'URGENTE']:
                channels.append(NotificationChannel.EMAIL)
        
        results = {}
        
        # Separar usuários por preferência de canal
        if respect_preferences:
            in_app_recipients = self._filter_by_channel_preference(recipients, 'in_app')
            push_recipients = self._filter_by_channel_preference(recipients, 'push')
            email_recipients = self._filter_by_channel_preference(recipients, 'email')
        else:
            in_app_recipients = recipients
            push_recipients = recipients
            email_recipients = recipients
        
        # 1. Notificação In-App (sempre)
        if NotificationChannel.IN_APP in channels or NotificationChannel.ALL in channels:
            if in_app_recipients:
                results['in_app'] = self._send_in_app(
                    recipients=in_app_recipients,
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    action_url=action_url,
                    priority=priority,
                    icon=icon,
                    extra_data=extra_data,
                    created_by=created_by
                )
            else:
                results['in_app'] = {'success': True, 'sent_count': 0, 'message': 'Nenhum destinatário habilitado para in-app'}
        
        # 2. Push Notification (WebPush/PWA)
        if (NotificationChannel.PUSH in channels or 
            NotificationChannel.BROWSER in channels or 
            NotificationChannel.ALL in channels):
            if push_recipients:
                results['push'] = self._send_push(
                    recipients=push_recipients,
                    title=title,
                    message=message,
                    action_url=action_url,
                    icon=icon,
                    extra_data=extra_data
                )
            else:
                results['push'] = {'success': True, 'sent_count': 0, 'message': 'Nenhum destinatário habilitado para push'}
        
        # 3. Email
        if NotificationChannel.EMAIL in channels or NotificationChannel.ALL in channels:
            if email_recipients:
                results['email'] = self._send_email(
                    recipients=email_recipients,
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    action_url=action_url,
                    template=email_template,
                    context=email_context
                )
            else:
                results['email'] = {'success': True, 'sent_count': 0, 'message': 'Nenhum destinatário habilitado para email'}
        
        # 4. OneSignal (Web/Mobile Push via OneSignal Service)
        if NotificationChannel.ONESIGNAL in channels or NotificationChannel.ALL in channels:
            if self.onesignal_enabled:
                results['onesignal'] = self._send_onesignal(
                    title=title,
                    message=message,
                    action_url=action_url,
                    icon=icon,
                    extra_data=extra_data,
                    recipients=recipients
                )
            else:
                results['onesignal'] = {'success': False, 'sent_count': 0, 'message': 'OneSignal não configurado'}
        
        # 4b. Truepush Legacy (fallback se OneSignal não configurado)
        if NotificationChannel.TRUEPUSH in channels:
            if self.truepush_enabled and not self.onesignal_enabled:
                results['truepush'] = self._send_truepush(
                    title=title,
                    message=message,
                    action_url=action_url,
                    icon=icon,
                    extra_data=extra_data
                )
            else:
                results['truepush'] = {'success': False, 'sent_count': 0, 'message': 'Use OneSignal ao invés de Truepush'}
        
        # Calcular sucesso geral
        overall_success = any(r.get('success', False) for r in results.values())
        
        return {
            'success': overall_success,
            'message': 'Notificação enviada',
            'results': results,
            'recipients_count': len(recipients)
        }
    
    def _send_in_app(
        self,
        recipients: List[User],
        title: str,
        message: str,
        notification_type: str,
        action_url: str,
        priority: str,
        icon: str,
        extra_data: Dict,
        created_by: User
    ) -> Dict[str, Any]:
        """Envia notificação in-app (salva no banco de dados)"""
        try:
            from .models import PushNotification, UserNotification, NotificationCategory
            
            # Mapear tipo para tipo do modelo
            type_mapping = {
                NotificationType.TICKET_CREATED: 'TICKET',
                NotificationType.TICKET_ASSIGNED: 'TICKET',
                NotificationType.TICKET_STATUS_CHANGED: 'TICKET',
                NotificationType.TICKET_COMMENT: 'TICKET',
                NotificationType.TICKET_RESOLVED: 'TICKET',
                NotificationType.COMMUNICATION_NEW: 'COMMUNICATION',
                NotificationType.SYSTEM: 'SYSTEM',
                NotificationType.CUSTOM: 'CUSTOM',
            }
            
            # Buscar ou criar categoria
            category_name = 'Chamados' if 'ticket' in notification_type else 'Sistema'
            if notification_type == NotificationType.COMMUNICATION_NEW:
                category_name = 'Comunicados'
            
            category, _ = NotificationCategory.objects.get_or_create(
                name=category_name,
                defaults={'icon': icon, 'color': 'blue'}
            )
            
            # Criar notificação principal
            notification = PushNotification.objects.create(
                title=title,
                message=message,
                category=category,
                notification_type=type_mapping.get(notification_type, 'CUSTOM'),
                priority=priority,
                icon=icon,
                action_url=action_url,
                action_text='Ver Detalhes',
                created_by=created_by or recipients[0],
                extra_data=extra_data or {},
                is_sent=True,
                sent_at=timezone.now()
            )
            
            # Criar registros para cada usuário
            user_notifications = [
                UserNotification(
                    notification=notification,
                    user=user,
                    is_read=False
                )
                for user in recipients
            ]
            
            UserNotification.objects.bulk_create(user_notifications, ignore_conflicts=True)
            
            logger.info(f"In-app notification created for {len(recipients)} users")
            
            return {
                'success': True,
                'sent_count': len(recipients),
                'notification_id': notification.id
            }
            
        except Exception as e:
            logger.error(f"Error creating in-app notification: {e}")
            return {
                'success': False,
                'error': str(e),
                'sent_count': 0
            }
    
    def _send_push(
        self,
        recipients: List[User],
        title: str,
        message: str,
        action_url: str,
        icon: str,
        extra_data: Dict
    ) -> Dict[str, Any]:
        """Envia push notification via WebPush"""
        try:
            from .push_utils import send_push_notification_to_users
            
            result = send_push_notification_to_users(
                users=recipients,
                title=title,
                message=message,
                action_url=action_url,
                icon=icon or '/static/images/logo.png',
                extra_data=extra_data
            )
            
            return result
            
        except Exception as e:
            logger.error(f"Error sending push notification: {e}")
            return {
                'success': False,
                'error': str(e),
                'sent_count': 0,
                'failed_count': len(recipients)
            }
    
    def _send_email(
        self,
        recipients: List[User],
        title: str,
        message: str,
        notification_type: str,
        action_url: str,
        template: str = None,
        context: Dict = None
    ) -> Dict[str, Any]:
        """Envia notificação por email"""
        if not self.email_enabled:
            logger.warning("Email not configured, skipping email notification")
            return {
                'success': False,
                'error': 'Email not configured',
                'sent_count': 0
            }
        
        sent_count = 0
        failed_count = 0
        errors = []
        
        # Preparar contexto base
        base_context = {
            'title': title,
            'message': message,
            'action_url': action_url,
            'notification_type': notification_type,
            'site_name': 'Rede Confiança',
            'base_url': getattr(settings, 'BASE_URL', 'https://chamados.redeconfianca.com.br'),
            **(context or {})
        }
        
        # Determinar template
        if template is None:
            template = 'emails/notification_base.html'
        
        for user in recipients:
            if not user.email:
                logger.warning(f"User {user.id} has no email, skipping")
                continue
            
            try:
                # Adicionar dados do usuário ao contexto
                user_context = {
                    **base_context,
                    'user': user,
                    'user_name': user.first_name or user.username
                }
                
                # Renderizar HTML
                try:
                    html_content = render_to_string(template, user_context)
                    text_content = strip_tags(html_content)
                except Exception:
                    # Fallback para email simples
                    html_content = f"""
                    <html>
                    <body style="font-family: Arial, sans-serif; padding: 20px;">
                        <h2 style="color: #FF6B35;">{title}</h2>
                        <p>{message}</p>
                        <p><a href="{base_context['base_url']}{action_url}" 
                              style="background: #FF6B35; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                            Ver Detalhes
                        </a></p>
                        <hr>
                        <p style="color: #666; font-size: 12px;">
                            Esta é uma notificação automática do sistema Rede Confiança.
                        </p>
                    </body>
                    </html>
                    """
                    text_content = f"{title}\n\n{message}\n\nAcesse: {base_context['base_url']}{action_url}"
                
                # Enviar email
                email = EmailMultiAlternatives(
                    subject=f"[Rede Confiança] {title}",
                    body=text_content,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[user.email]
                )
                email.attach_alternative(html_content, "text/html")
                email.send(fail_silently=False)
                
                sent_count += 1
                logger.info(f"Email sent to {user.email}")
                
            except Exception as e:
                failed_count += 1
                errors.append(f"{user.email}: {str(e)}")
                logger.error(f"Error sending email to {user.email}: {e}")
        
        return {
            'success': sent_count > 0,
            'sent_count': sent_count,
            'failed_count': failed_count,
            'errors': errors if errors else None
        }
    
    def _send_onesignal(
        self,
        title: str,
        message: str,
        action_url: str,
        icon: str,
        extra_data: Dict,
        recipients: List[User] = None
    ) -> Dict[str, Any]:
        """
        Envia notificação via OneSignal para TODOS os subscribers.
        
        No plano gratuito do OneSignal, as notificações são enviadas para todos
        os usuários inscritos (Subscribed Users) ao mesmo tempo.
        
        O parâmetro 'recipients' é mantido para compatibilidade mas não é usado
        para filtrar destinatários no envio - apenas para logging.
        """
        try:
            from .onesignal_service import onesignal_service
            
            if not onesignal_service.enabled:
                logger.warning("OneSignal não configurado")
                return {
                    'success': False,
                    'error': 'OneSignal não configurado',
                    'sent_count': 0
                }
            
            # Preparar URL completa
            base_url = getattr(settings, 'BASE_URL', 'https://chamados.redeconfianca.com.br')
            full_url = action_url if action_url.startswith('http') else f"{base_url}{action_url}"
            
            # Preparar ícone
            icon_url = icon if icon and icon.startswith('http') else f"{base_url}/static/images/logo.png"
            
            # PLANO GRATUITO: Enviar para TODOS os subscribers
            # Usar send_to_all que envia para o segmento 'Subscribed Users'
            result = onesignal_service.send_to_all(
                title=title,
                message=message,
                url=full_url,
                icon=icon_url,
                data=extra_data
            )
            
            if result.get('success'):
                recipients_count = len(recipients) if recipients else 0
                logger.info(f"OneSignal: Notificação enviada para todos os subscribers - {title} (destinatários previstos: {recipients_count})")
            else:
                logger.error(f"OneSignal: Erro ao enviar - {result.get('error', 'Erro desconhecido')}")
            
            return result
            
        except Exception as e:
            logger.error(f"OneSignal: Erro inesperado - {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'sent_count': 0
            }
    
    def _send_truepush(
        self,
        title: str,
        message: str,
        action_url: str,
        icon: str,
        extra_data: Dict
    ) -> Dict[str, Any]:
        """Envia notificação via Truepush para todos os assinantes web/mobile"""
        try:
            from .truepush_service import truepush_service
            
            if not truepush_service.enabled:
                logger.warning("Truepush não configurado")
                return {
                    'success': False,
                    'error': 'Truepush não configurado',
                    'sent_count': 0
                }
            
            # Preparar URL completa
            base_url = getattr(settings, 'BASE_URL', 'https://chamados.redeconfianca.com.br')
            full_url = action_url if action_url.startswith('http') else f"{base_url}{action_url}"
            
            # Preparar ícone
            icon_url = icon if icon and icon.startswith('http') else f"{base_url}/static/images/logo.png"
            
            # Enviar via Truepush
            result = truepush_service.send_to_all(
                title=title,
                message=message,
                url=full_url,
                icon=icon_url,
                data=extra_data
            )
            
            if result.get('success'):
                logger.info(f"Truepush: Notificação enviada - {title}")
            else:
                logger.error(f"Truepush: Erro ao enviar - {result.get('error', 'Erro desconhecido')}")
            
            return result
            
        except Exception as e:
            logger.error(f"Truepush: Erro inesperado - {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'sent_count': 0
            }
    
    # ==========================================
    # Métodos de conveniência para tipos comuns
    # ==========================================
    
    def notify_ticket_created(
        self,
        ticket,
        notify_sector: bool = True,
        notify_admins: bool = True
    ) -> Dict[str, Any]:
        """Notifica sobre criação de ticket"""
        from users.models import User
        
        recipients = set()
        
        # Notificar usuários do setor
        if notify_sector and ticket.sector:
            sector_users = User.objects.filter(
                sector=ticket.sector,
                is_active=True,
                hierarchy__in=['ADMIN', 'SUPERVISOR', 'MANAGER', 'SUPERADMIN']
            ).exclude(id=ticket.created_by.id)
            recipients.update(sector_users)
        
        # Notificar administradores
        if notify_admins:
            admins = User.objects.filter(
                is_active=True,
                hierarchy='SUPERADMIN'
            ).exclude(id=ticket.created_by.id)
            recipients.update(admins)
        
        if not recipients:
            return {'success': True, 'message': 'Nenhum destinatário'}
        
        return self.send_notification(
            recipients=list(recipients),
            title=f"Novo Chamado #{ticket.id}",
            message=f"{ticket.title}\nSetor: {ticket.sector.name if ticket.sector else 'N/A'}\nPrioridade: {ticket.get_priority_display()}",
            notification_type=NotificationType.TICKET_CREATED,
            action_url=f"/tickets/{ticket.id}/",
            priority='NORMAL' if ticket.priority != 'CRITICA' else 'ALTA',
            icon='fas fa-ticket-alt',
            extra_data={
                'ticket_id': ticket.id,
                'sector_id': ticket.sector.id if ticket.sector else None,
                'category': ticket.category.name if ticket.category else None
            },
            created_by=ticket.created_by,
            email_context={
                'ticket': ticket,
                'action_text': 'Ver Chamado'
            }
        )
    
    def notify_ticket_assigned(
        self,
        ticket,
        assigned_user: User,
        assigned_by: User
    ) -> Dict[str, Any]:
        """Notifica quando alguém é atribuído a um ticket"""
        
        # Canais de notificação - incluir OneSignal para push para todos
        channels = [NotificationChannel.IN_APP, NotificationChannel.PUSH, NotificationChannel.EMAIL]
        if self.onesignal_enabled:
            channels.append(NotificationChannel.ONESIGNAL)
        
        return self.send_notification(
            recipients=assigned_user,
            title=f"🎫 Chamado Atribuído: #{ticket.id}",
            message=f"Você foi atribuído ao chamado '{ticket.title}' por {assigned_by.full_name}.",
            notification_type=NotificationType.TICKET_ASSIGNED,
            channels=channels,
            action_url=f"/tickets/{ticket.id}/",
            priority='ALTA',
            icon='fas fa-user-tag',
            extra_data={
                'ticket_id': ticket.id,
                'assigned_by': assigned_by.id
            },
            created_by=assigned_by,
            email_context={
                'ticket': ticket,
                'assigned_by': assigned_by,
                'action_text': 'Ver Chamado'
            }
        )
    
    def notify_ticket_status_changed(
        self,
        ticket,
        old_status: str,
        new_status: str,
        changed_by: User
    ) -> Dict[str, Any]:
        """Notifica sobre mudança de status do ticket"""
        
        recipients = set()
        
        # Notificar criador
        if ticket.created_by and ticket.created_by != changed_by:
            recipients.add(ticket.created_by)
        
        # Notificar responsável
        if ticket.assigned_to and ticket.assigned_to != changed_by:
            recipients.add(ticket.assigned_to)
        
        # Notificar usuários adicionais atribuídos
        for assignment in ticket.additional_assignments.filter(is_active=True):
            if assignment.user != changed_by:
                recipients.add(assignment.user)
        
        if not recipients:
            return {'success': True, 'message': 'Nenhum destinatário'}
        
        status_display = dict(ticket.STATUS_CHOICES).get(new_status, new_status)
        priority = 'ALTA' if new_status in ['RESOLVIDO', 'FECHADO'] else 'NORMAL'
        
        return self.send_notification(
            recipients=list(recipients),
            title=f"Chamado #{ticket.id} - {status_display}",
            message=f"O chamado '{ticket.title}' teve seu status alterado para {status_display}.",
            notification_type=NotificationType.TICKET_STATUS_CHANGED,
            action_url=f"/tickets/{ticket.id}/",
            priority=priority,
            icon='fas fa-exchange-alt',
            extra_data={
                'ticket_id': ticket.id,
                'old_status': old_status,
                'new_status': new_status
            },
            created_by=changed_by,
            email_context={
                'ticket': ticket,
                'old_status': old_status,
                'new_status': new_status,
                'changed_by': changed_by,
                'action_text': 'Ver Chamado'
            }
        )
    
    def notify_ticket_comment(
        self,
        ticket,
        comment,
        comment_by: User
    ) -> Dict[str, Any]:
        """Notifica sobre novo comentário no ticket"""
        
        recipients = set()
        
        # Notificar criador
        if ticket.created_by and ticket.created_by != comment_by:
            recipients.add(ticket.created_by)
        
        # Notificar responsável
        if ticket.assigned_to and ticket.assigned_to != comment_by:
            recipients.add(ticket.assigned_to)
        
        # Notificar usuários atribuídos
        for assignment in ticket.additional_assignments.filter(is_active=True):
            if assignment.user != comment_by:
                recipients.add(assignment.user)
        
        if not recipients:
            return {'success': True, 'message': 'Nenhum destinatário'}
        
        # Truncar comentário para preview
        comment_preview = comment.comment[:100] + '...' if len(comment.comment) > 100 else comment.comment
        
        return self.send_notification(
            recipients=list(recipients),
            title=f"Novo comentário no Chamado #{ticket.id}",
            message=f"{comment_by.full_name}: {comment_preview}",
            notification_type=NotificationType.TICKET_COMMENT,
            action_url=f"/tickets/{ticket.id}/",
            priority='NORMAL',
            icon='fas fa-comment',
            extra_data={
                'ticket_id': ticket.id,
                'comment_id': comment.id
            },
            created_by=comment_by
        )
    
    def notify_communication_created(
        self,
        communication
    ) -> Dict[str, Any]:
        """
        Notifica sobre novo comunicado.
        
        Se o comunicado for para todos (send_to_all=True), envia notificação
        push via OneSignal para todos os subscribers.
        """
        from users.models import User
        
        # Determinar destinatários
        if communication.send_to_all:
            recipients = list(User.objects.filter(is_active=True).exclude(id=communication.sender.id))
        else:
            recipients = list(communication.recipients.filter(is_active=True).exclude(id=communication.sender.id))
        
        if not recipients:
            return {'success': True, 'message': 'Nenhum destinatário'}
        
        # Determinar prioridade baseado nas flags
        priority = 'ALTA' if communication.is_pinned else 'NORMAL'
        
        # Truncar mensagem
        message_preview = communication.message[:150] + '...' if len(communication.message) > 150 else communication.message
        
        # Definir canais de notificação
        # IMPORTANTE: Comunicados para todos incluem OneSignal para enviar push para todos
        channels = [NotificationChannel.IN_APP, NotificationChannel.PUSH]
        
        # Adicionar OneSignal para comunicados (envia para todos os subscribers)
        if self.onesignal_enabled:
            channels.append(NotificationChannel.ONESIGNAL)
        
        # Incluir email para comunicados fixados ou importantes
        if communication.is_pinned:
            channels.append(NotificationChannel.EMAIL)
        
        return self.send_notification(
            recipients=recipients,
            title=f"📢 Novo Comunicado: {communication.title}",
            message=message_preview,
            notification_type=NotificationType.COMMUNICATION_NEW,
            channels=channels,
            action_url=f"/communications/{communication.id}/",
            priority=priority,
            icon='fas fa-bullhorn',
            extra_data={
                'communication_id': communication.id,
                'is_pinned': communication.is_pinned,
                'is_popup': communication.is_popup,
                'send_to_all': communication.send_to_all
            },
            created_by=communication.sender,
            email_context={
                'communication': communication,
                'action_text': 'Ver Comunicado'
            }
        )


# Instância global do serviço
notification_service = NotificationService()


# Funções utilitárias para uso direto
def send_notification(*args, **kwargs):
    """Wrapper para notification_service.send_notification"""
    return notification_service.send_notification(*args, **kwargs)


def notify_ticket_created(ticket, **kwargs):
    """Wrapper para notification_service.notify_ticket_created"""
    return notification_service.notify_ticket_created(ticket, **kwargs)


def notify_ticket_assigned(ticket, assigned_user, assigned_by):
    """Wrapper para notification_service.notify_ticket_assigned"""
    return notification_service.notify_ticket_assigned(ticket, assigned_user, assigned_by)


def notify_ticket_status_changed(ticket, old_status, new_status, changed_by):
    """Wrapper para notification_service.notify_ticket_status_changed"""
    return notification_service.notify_ticket_status_changed(ticket, old_status, new_status, changed_by)


def notify_ticket_comment(ticket, comment, comment_by):
    """Wrapper para notification_service.notify_ticket_comment"""
    return notification_service.notify_ticket_comment(ticket, comment, comment_by)


def notify_communication_created(communication):
    """Wrapper para notification_service.notify_communication_created"""
    return notification_service.notify_communication_created(communication)


def send_truepush_notification(title: str, message: str, url: str = '/', **kwargs):
    """
    Envia notificação diretamente via Truepush para todos os assinantes.
    Útil para notificações broadcast importantes.
    
    Args:
        title: Título da notificação
        message: Mensagem da notificação
        url: URL de destino ao clicar
        **kwargs: Argumentos adicionais (icon, image, data, etc.)
    """
    from .truepush_service import truepush_service
    return truepush_service.send_to_all(title=title, message=message, url=url, **kwargs)


def send_notification_all_channels(
    title: str,
    message: str,
    url: str = '/',
    recipients=None,
    **kwargs
):
    """
    Envia notificação por todos os canais disponíveis:
    - In-App (se recipients fornecido)
    - WebPush/VAPID (se recipients fornecido e configurado)
    - Truepush (para todos os assinantes web/mobile)
    
    Útil para notificações broadcast importantes que devem atingir todos os usuários.
    """
    results = {}
    
    # Enviar via Truepush (para todos os assinantes)
    from .truepush_service import truepush_service
    if truepush_service.enabled:
        results['truepush'] = truepush_service.send_to_all(
            title=title,
            message=message,
            url=url,
            **kwargs
        )
    
    # Enviar via sistema interno se recipients fornecido
    if recipients:
        results['internal'] = notification_service.send_notification(
            recipients=recipients,
            title=title,
            message=message,
            action_url=url,
            channels=[NotificationChannel.IN_APP, NotificationChannel.PUSH],
            **kwargs
        )
    
    return {
        'success': any(r.get('success', False) for r in results.values()),
        'results': results
    }

