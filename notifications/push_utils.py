import json
import logging
from typing import List, Dict, Any, Optional
from pywebpush import webpush, WebPushException
from django.conf import settings
from django.contrib.auth import get_user_model
from .models import DeviceToken, UserNotification

User = get_user_model()
logger = logging.getLogger(__name__)


class PushNotificationService:
    """Serviço para envio de push notifications usando Web Push API"""
    
    def __init__(self):
        self.vapid_private_key = getattr(settings, 'VAPID_PRIVATE_KEY', None)
        self.vapid_public_key = getattr(settings, 'VAPID_PUBLIC_KEY', None)
        self.vapid_claims = getattr(settings, 'VAPID_CLAIMS', {"sub": "mailto:admin@redeconfianca.com"})
        
        if not self.vapid_private_key or not self.vapid_public_key:
            logger.warning("VAPID keys not configured. Push notifications will not work.")
    
    def send_to_user(self, user: User, title: str, message: str, **kwargs) -> Dict[str, Any]:
        """
        Envia push notification para um usuário específico
        
        Args:
            user: Usuário que receberá a notificação
            title: Título da notificação
            message: Mensagem da notificação
            **kwargs: Dados adicionais (icon, badge, action_url, etc.)
        
        Returns:
            Dict com resultado do envio
        """
        if not self.vapid_private_key or not self.vapid_public_key:
            return {
                'success': False,
                'error': 'VAPID keys not configured',
                'sent_count': 0,
                'failed_count': 0
            }
        
        # Buscar tokens ativos do usuário
        device_tokens = DeviceToken.objects.filter(
            user=user,
            is_active=True
        )
        
        if not device_tokens.exists():
            logger.info(f"No active device tokens found for user {user.id}")
            return {
                'success': True,
                'message': 'No devices to send notification',
                'sent_count': 0,
                'failed_count': 0
            }
        
        return self.send_to_tokens(device_tokens, title, message, **kwargs)
    
    def send_to_users(self, users: List[User], title: str, message: str, **kwargs) -> Dict[str, Any]:
        """
        Envia push notification para múltiplos usuários
        
        Args:
            users: Lista de usuários que receberão a notificação
            title: Título da notificação
            message: Mensagem da notificação
            **kwargs: Dados adicionais
        
        Returns:
            Dict com resultado do envio
        """
        if not users:
            return {
                'success': True,
                'message': 'No users provided',
                'sent_count': 0,
                'failed_count': 0
            }
        
        user_ids = [user.id for user in users]
        device_tokens = DeviceToken.objects.filter(
            user_id__in=user_ids,
            is_active=True
        )
        
        return self.send_to_tokens(device_tokens, title, message, **kwargs)
    
    def send_to_tokens(self, device_tokens, title: str, message: str, **kwargs) -> Dict[str, Any]:
        """
        Envia push notification para tokens específicos
        
        Args:
            device_tokens: QuerySet ou lista de DeviceToken
            title: Título da notificação
            message: Mensagem da notificação
            **kwargs: Dados adicionais
        
        Returns:
            Dict com resultado do envio
        """
        if not self.vapid_private_key or not self.vapid_public_key:
            return {
                'success': False,
                'error': 'VAPID keys not configured',
                'sent_count': 0,
                'failed_count': 0
            }
        
        sent_count = 0
        failed_count = 0
        failed_tokens = []
        
        # Preparar payload da notificação
        payload = self._prepare_payload(title, message, **kwargs)
        
        for token in device_tokens:
            try:
                # Converter token para formato subscription
                subscription_info = self._parse_token(token.token)
                
                if not subscription_info:
                    logger.error(f"Invalid token format for token {token.id}")
                    failed_count += 1
                    failed_tokens.append(token.id)
                    continue
                
                # Enviar push notification
                webpush(
                    subscription_info=subscription_info,
                    data=json.dumps(payload),
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims=self.vapid_claims
                )
                
                sent_count += 1
                logger.info(f"Push notification sent successfully to token {token.id}")
                
            except WebPushException as e:
                failed_count += 1
                failed_tokens.append(token.id)
                logger.error(f"WebPush error for token {token.id}: {str(e)}")
                
                # Desativar token se erro indicar que não é mais válido
                if e.response and e.response.status_code in [400, 404, 410]:
                    token.is_active = False
                    token.save()
                    logger.info(f"Deactivated invalid token {token.id}")
                    
            except Exception as e:
                failed_count += 1
                failed_tokens.append(token.id)
                logger.error(f"Unexpected error sending to token {token.id}: {str(e)}")
        
        return {
            'success': sent_count > 0 or failed_count == 0,
            'sent_count': sent_count,
            'failed_count': failed_count,
            'failed_tokens': failed_tokens,
            'message': f'Sent to {sent_count} devices, {failed_count} failed'
        }
    
    def _prepare_payload(self, title: str, message: str, **kwargs) -> Dict[str, Any]:
        """Prepara o payload da notificação"""
        payload = {
            'title': title,
            'body': message,
            'icon': kwargs.get('icon', '/static/images/logo.png'),
            'badge': kwargs.get('badge', '/static/images/logo.png'),
            'vibrate': kwargs.get('vibrate', [100, 50, 100]),
            'data': {
                'dateOfArrival': kwargs.get('timestamp'),
                'url': kwargs.get('action_url', '/'),
                'primaryKey': kwargs.get('notification_id'),
                'extra': kwargs.get('extra_data', {})
            },
            'requireInteraction': kwargs.get('require_interaction', False),
            'silent': kwargs.get('silent', False)
        }
        
        # Adicionar ações se fornecidas
        actions = kwargs.get('actions', [])
        if not actions and kwargs.get('action_url') and kwargs.get('action_text'):
            actions = [{
                'action': 'open',
                'title': kwargs.get('action_text', 'Ver mais'),
                'icon': '/static/images/logo.png'
            }]
        
        if actions:
            payload['actions'] = actions
        
        return payload
    
    def _parse_token(self, token_str: str) -> Optional[Dict[str, Any]]:
        """Converte string do token para formato de subscription"""
        try:
            # Tentar parsear como JSON (formato do Web Push)
            token_data = json.loads(token_str)
            
            if 'endpoint' in token_data:
                return token_data
            
            # Se não for um objeto válido, tentar outros formatos
            return None
            
        except (json.JSONDecodeError, ValueError):
            # Se não for JSON, pode ser um token simples
            logger.error(f"Could not parse token: {token_str[:50]}...")
            return None


# Instância global do serviço
push_service = PushNotificationService()


def send_push_notification_to_user(user: User, title: str, message: str, **kwargs) -> Dict[str, Any]:
    """
    Função utilitária para enviar push notification para um usuário
    
    Args:
        user: Usuário que receberá a notificação
        title: Título da notificação
        message: Mensagem da notificação
        **kwargs: Dados adicionais (notification_id, action_url, etc.)
    
    Returns:
        Dict com resultado do envio
    """
    return push_service.send_to_user(user, title, message, **kwargs)


def send_push_notification_to_users(users: List[User], title: str, message: str, **kwargs) -> Dict[str, Any]:
    """
    Função utilitária para enviar push notification para múltiplos usuários
    
    Args:
        users: Lista de usuários que receberão a notificação
        title: Título da notificação
        message: Mensagem da notificação
        **kwargs: Dados adicionais
    
    Returns:
        Dict com resultado do envio
    """
    return push_service.send_to_users(users, title, message, **kwargs)