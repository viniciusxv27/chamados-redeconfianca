"""
Serviço de integração com OneSignal
Permite envio de notificações push via web e mobile através do serviço OneSignal

Documentação: https://documentation.onesignal.com/reference/
Plano gratuito: até 10.000 assinantes
"""
import requests
import json
import logging
from typing import List, Dict, Any, Optional, Union
from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)


class OneSignalService:
    """
    Serviço para integração com OneSignal API
    
    Configurações necessárias no settings.py:
        ONESIGNAL_APP_ID = 'seu_app_id'
        ONESIGNAL_REST_API_KEY = 'sua_rest_api_key'
    """
    
    BASE_URL = "https://onesignal.com/api/v1"
    
    def __init__(self):
        self.app_id = getattr(settings, 'ONESIGNAL_APP_ID', '')
        self.rest_api_key = getattr(settings, 'ONESIGNAL_REST_API_KEY', '')
        self.enabled = bool(self.app_id and self.rest_api_key)
        
        if not self.enabled:
            logger.warning("OneSignal não configurado. Defina ONESIGNAL_APP_ID e ONESIGNAL_REST_API_KEY no settings.py")
    
    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers para requisições à API do OneSignal"""
        return {
            'Authorization': f'Basic {self.rest_api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
    def _log_notification(
        self,
        title: str,
        message: str,
        url: str = '',
        segment: str = '',
        sent_to_all: bool = True,
        success: bool = False,
        sent_count: int = 0,
        response_data: Dict = None,
        error_message: str = '',
        sent_by=None
    ):
        """Registra log de notificação no banco de dados"""
        try:
            from .models import OneSignalNotificationLog
            OneSignalNotificationLog.objects.create(
                title=title,
                message=message,
                url=url,
                segment=segment or '',
                sent_to_all=sent_to_all,
                success=success,
                sent_count=sent_count,
                response_data=response_data or {},
                error_message=error_message,
                sent_by=sent_by
            )
        except Exception as e:
            logger.error(f"Erro ao registrar log de notificação OneSignal: {e}")
    
    def send_notification(
        self,
        title: str,
        message: str,
        url: str = '/',
        icon: str = None,
        image: str = None,
        segment: str = None,
        player_ids: List[str] = None,
        external_user_ids: List[str] = None,
        emails: List[str] = None,
        data: Dict = None,
        buttons: List[Dict] = None,
        ttl: int = 86400,
        priority: int = 10,
        chrome_web_icon: str = None,
        chrome_web_badge: str = None,
        sent_by=None,
        target_channel: str = 'push'
    ) -> Dict[str, Any]:
        """
        Envia notificação push via OneSignal
        
        Args:
            title: Título da notificação
            message: Corpo da mensagem
            url: URL para abrir ao clicar
            icon: URL do ícone (opcional)
            image: URL de imagem grande (opcional)
            segment: Segmento de usuários (ex: 'Total Subscriptions', 'Active Subscriptions')
            player_ids: Lista de Player IDs específicos (opcional)
            external_user_ids: Lista de External User IDs do sistema (opcional)
            emails: Lista de emails para envio direcionado (opcional)
            data: Dados extras para a notificação (opcional)
            buttons: Lista de botões de ação (opcional)
            ttl: Time to live em segundos (padrão: 24h)
            priority: Prioridade da notificação (1-10, padrão: 10)
            chrome_web_icon: Ícone específico para Chrome
            chrome_web_badge: Badge para Chrome
            sent_by: Usuário que enviou a notificação
            target_channel: Canal alvo ('push', 'email', ou None para ambos)
            
        Returns:
            Dict com resultado do envio
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'OneSignal não configurado',
                'sent_count': 0
            }
        
        try:
            # Preparar payload base
            payload = {
                'app_id': self.app_id,
                'headings': {'en': title, 'pt': title},
                'contents': {'en': message, 'pt': message},
                'url': url,
                'ttl': ttl,
                'priority': priority,
                'isAnyWeb': True,
                'isIos': False,
                'isAndroid': False,
                'isWP': False,
                'isAdm': False,
                'isChrome': True,
                'isChromeWeb': True,
                'isFirefox': True,
                'isSafari': True,
            }
            
            # Definir canal alvo
            if target_channel:
                payload['target_channel'] = target_channel
            
            # =====================================================
            # LÓGICA DE TARGETING - Ordem de prioridade:
            # 1. external_user_ids (IDs do sistema) - mais específico
            # 2. emails - envio por email
            # 3. player_ids - IDs do OneSignal
            # 4. segment - grupo de usuários
            # 5. Todos os inscritos - padrão
            # =====================================================
            
            targeting_method = 'segment'  # Padrão
            
            if external_user_ids and len(external_user_ids) > 0:
                # Usar include_aliases para enviar para external_user_ids específicos
                # Esta é a forma correta na API v16 do OneSignal
                payload['include_aliases'] = {
                    'external_id': [str(uid) for uid in external_user_ids]
                }
                payload['target_channel'] = 'push'
                targeting_method = 'external_user_ids'
                logger.info(f"OneSignal: Targeting {len(external_user_ids)} external_user_ids")
                
            elif emails and len(emails) > 0:
                # Enviar para emails específicos
                payload['include_aliases'] = {
                    'email': emails
                }
                targeting_method = 'emails'
                logger.info(f"OneSignal: Targeting {len(emails)} emails")
                
            elif player_ids and len(player_ids) > 0:
                # Enviar para player_ids específicos
                payload['include_player_ids'] = player_ids
                targeting_method = 'player_ids'
                logger.info(f"OneSignal: Targeting {len(player_ids)} player_ids")
                
            elif segment:
                # Enviar para um segmento específico
                payload['included_segments'] = [segment]
                targeting_method = 'segment'
                logger.info(f"OneSignal: Targeting segment '{segment}'")
                
            else:
                # Enviar para todos os inscritos (padrão)
                payload['included_segments'] = ['Total Subscriptions']
                targeting_method = 'all_subscribers'
                logger.info("OneSignal: Targeting all Total Subscriptions")
            
            # Configurar ícones
            base_url = getattr(settings, 'BASE_URL', 'https://chamados.redeconfianca.com.br')
            
            if icon:
                payload['chrome_web_icon'] = icon
                payload['firefox_icon'] = icon
            else:
                default_icon = f"{base_url}/static/images/logo.png"
                payload['chrome_web_icon'] = default_icon
                payload['firefox_icon'] = default_icon
            
            if chrome_web_badge:
                payload['chrome_web_badge'] = chrome_web_badge
            
            if image:
                payload['big_picture'] = image
                payload['chrome_web_image'] = image
            
            # Dados extras
            if data:
                payload['data'] = data
            
            # Botões de ação
            if buttons:
                payload['web_buttons'] = buttons
            
            # Fazer requisição
            response = requests.post(
                f"{self.BASE_URL}/notifications",
                headers=self._get_headers(),
                json=payload,
                timeout=30
            )
            
            result = response.json()
            
            if response.status_code in [200, 201] and not result.get('errors'):
                recipients = result.get('recipients', 0)
                logger.info(f"OneSignal: Notificação enviada com sucesso - {title} para {recipients} destinatários")
                
                # Registrar log
                self._log_notification(
                    title=title,
                    message=message,
                    url=url,
                    segment=segment or 'Total Subscriptions',
                    sent_to_all=not player_ids and not external_user_ids,
                    success=True,
                    sent_count=recipients,
                    response_data=result,
                    sent_by=sent_by
                )
                
                return {
                    'success': True,
                    'message': 'Notificação enviada via OneSignal',
                    'notification_id': result.get('id'),
                    'recipients': recipients,
                    'sent_count': recipients,
                    'response': result
                }
            else:
                errors = result.get('errors', [])
                error_msg = ', '.join(errors) if isinstance(errors, list) else str(errors)
                
                # Mensagem mais amigável para erros comuns
                if 'All included players are not subscribed' in error_msg:
                    error_msg = 'Nenhum usuário aceitou as notificações push ainda. Os usuários precisam clicar em "Permitir" no popup do navegador para receber notificações.'
                elif 'No subscribed players' in error_msg or 'recipients' in str(result) and result.get('recipients', 0) == 0:
                    error_msg = 'Não há usuários com push ativo. Os usuários precisam aceitar as notificações no navegador.'
                
                logger.error(f"OneSignal: Erro ao enviar notificação - {error_msg}")
                
                # Registrar log de erro
                self._log_notification(
                    title=title,
                    message=message,
                    url=url,
                    segment=segment or '',
                    sent_to_all=not player_ids and not external_user_ids,
                    success=False,
                    sent_count=0,
                    response_data=result,
                    error_message=error_msg,
                    sent_by=sent_by
                )
                
                return {
                    'success': False,
                    'error': error_msg,
                    'status_code': response.status_code,
                    'sent_count': 0
                }
                
        except requests.exceptions.Timeout:
            logger.error("OneSignal: Timeout ao enviar notificação")
            return {
                'success': False,
                'error': 'Timeout na requisição',
                'sent_count': 0
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"OneSignal: Erro de requisição - {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'sent_count': 0
            }
        except Exception as e:
            logger.error(f"OneSignal: Erro inesperado - {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'sent_count': 0
            }
    
    def send_to_segment(
        self,
        segment: str,
        title: str,
        message: str,
        url: str = '/',
        **kwargs
    ) -> Dict[str, Any]:
        """
        Envia notificação para um segmento específico
        
        Segmentos padrão do OneSignal:
        - 'Total Subscriptions': Todos os usuários inscritos
        - 'Active Users': Usuários ativos recentemente
        - 'Inactive Users': Usuários inativos
        """
        return self.send_notification(
            title=title,
            message=message,
            url=url,
            segment=segment,
            **kwargs
        )
    
    def send_to_players(
        self,
        player_ids: List[str],
        title: str,
        message: str,
        url: str = '/',
        **kwargs
    ) -> Dict[str, Any]:
        """
        Envia notificação para Player IDs específicos
        """
        return self.send_notification(
            title=title,
            message=message,
            url=url,
            player_ids=player_ids,
            **kwargs
        )
    
    def send_to_external_users(
        self,
        external_user_ids: List[str],
        title: str,
        message: str,
        url: str = '/',
        **kwargs
    ) -> Dict[str, Any]:
        """
        Envia notificação para External User IDs (IDs do seu sistema).
        Os external_user_ids devem corresponder aos IDs configurados via OneSignal.login()
        no frontend.
        """
        if not external_user_ids:
            return {
                'success': False,
                'error': 'Nenhum external_user_id fornecido',
                'sent_count': 0
            }
        
        return self.send_notification(
            title=title,
            message=message,
            url=url,
            external_user_ids=external_user_ids,
            **kwargs
        )
    
    def send_to_emails(
        self,
        emails: List[str],
        title: str,
        message: str,
        url: str = '/',
        **kwargs
    ) -> Dict[str, Any]:
        """
        Envia notificação para emails específicos.
        Os emails devem corresponder aos configurados via OneSignal.User.addEmail()
        no frontend.
        """
        if not emails:
            return {
                'success': False,
                'error': 'Nenhum email fornecido',
                'sent_count': 0
            }
        
        return self.send_notification(
            title=title,
            message=message,
            url=url,
            emails=emails,
            **kwargs
        )
    
    def send_to_users(
        self,
        users: List,
        title: str,
        message: str,
        url: str = '/',
        **kwargs
    ) -> Dict[str, Any]:
        """
        Envia notificação para uma lista de usuários do Django.
        Usa o ID do usuário como external_user_id.
        
        Args:
            users: Lista de objetos User do Django
            title: Título da notificação
            message: Corpo da mensagem
            url: URL de destino
        """
        if not users:
            return {
                'success': False,
                'error': 'Nenhum usuário fornecido',
                'sent_count': 0
            }
        
        # Extrair IDs dos usuários
        external_user_ids = [str(user.id) for user in users if user.is_active]
        
        if not external_user_ids:
            return {
                'success': False,
                'error': 'Nenhum usuário ativo fornecido',
                'sent_count': 0
            }
        
        logger.info(f"OneSignal: Sending notification to {len(external_user_ids)} users: {external_user_ids}")
        
        return self.send_to_external_users(
            external_user_ids=external_user_ids,
            title=title,
            message=message,
            url=url,
            **kwargs
        )
    
    def send_to_all(
        self,
        title: str,
        message: str,
        url: str = '/',
        **kwargs
    ) -> Dict[str, Any]:
        """
        Envia notificação para todos os assinantes
        """
        return self.send_notification(
            title=title,
            message=message,
            url=url,
            segment='Total Subscriptions',
            **kwargs
        )
    
    def get_notification(self, notification_id: str) -> Dict[str, Any]:
        """
        Obtém detalhes de uma notificação específica
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'OneSignal não configurado'
            }
        
        try:
            response = requests.get(
                f"{self.BASE_URL}/notifications/{notification_id}?app_id={self.app_id}",
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'notification': response.json()
                }
            else:
                return {
                    'success': False,
                    'error': response.text
                }
                
        except Exception as e:
            logger.error(f"OneSignal: Erro ao obter notificação - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_notifications(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        Lista notificações enviadas
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'OneSignal não configurado'
            }
        
        try:
            response = requests.get(
                f"{self.BASE_URL}/notifications?app_id={self.app_id}&limit={limit}&offset={offset}",
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'notifications': response.json().get('notifications', []),
                    'total_count': response.json().get('total_count', 0)
                }
            else:
                return {
                    'success': False,
                    'error': response.text
                }
                
        except Exception as e:
            logger.error(f"OneSignal: Erro ao listar notificações - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_app_info(self) -> Dict[str, Any]:
        """
        Obtém informações do app incluindo contagem de players
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'OneSignal não configurado'
            }
        
        try:
            response = requests.get(
                f"{self.BASE_URL}/apps/{self.app_id}",
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    'success': True,
                    'app': data,
                    'players': data.get('players', 0),
                    'messageable_players': data.get('messageable_players', 0)
                }
            else:
                return {
                    'success': False,
                    'error': response.text
                }
                
        except Exception as e:
            logger.error(f"OneSignal: Erro ao obter info do app - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_player_count(self) -> Dict[str, Any]:
        """
        Obtém contagem de assinantes (players)
        """
        result = self.get_app_info()
        if result.get('success'):
            return {
                'success': True,
                'count': result.get('messageable_players', 0),
                'total_players': result.get('players', 0)
            }
        return result
    
    def cancel_notification(self, notification_id: str) -> Dict[str, Any]:
        """
        Cancela uma notificação agendada
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'OneSignal não configurado'
            }
        
        try:
            response = requests.delete(
                f"{self.BASE_URL}/notifications/{notification_id}?app_id={self.app_id}",
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code in [200, 204]:
                return {
                    'success': True,
                    'message': 'Notificação cancelada com sucesso'
                }
            else:
                return {
                    'success': False,
                    'error': response.text
                }
                
        except Exception as e:
            logger.error(f"OneSignal: Erro ao cancelar notificação - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_segments(self) -> Dict[str, Any]:
        """
        Retorna segmentos padrão do OneSignal
        (OneSignal não tem API para listar segmentos customizados no plano gratuito)
        """
        return {
            'success': True,
            'segments': [
                {'id': 'Total Subscriptions', 'name': 'Todos os Inscritos', 'description': 'Todos os usuários que permitiram notificações'},
                {'id': 'Active Users', 'name': 'Usuários Ativos', 'description': 'Usuários ativos nos últimos 30 dias'},
                {'id': 'Inactive Users', 'name': 'Usuários Inativos', 'description': 'Usuários inativos há mais de 30 dias'},
            ]
        }


# Instância global do serviço
onesignal_service = OneSignalService()


# Funções de conveniência
def send_onesignal_notification(
    title: str,
    message: str,
    url: str = '/',
    **kwargs
) -> Dict[str, Any]:
    """Função de conveniência para enviar notificação OneSignal"""
    return onesignal_service.send_notification(
        title=title,
        message=message,
        url=url,
        **kwargs
    )


def send_onesignal_to_all(
    title: str,
    message: str,
    url: str = '/',
    **kwargs
) -> Dict[str, Any]:
    """Função de conveniência para enviar para todos"""
    return onesignal_service.send_to_all(
        title=title,
        message=message,
        url=url,
        **kwargs
    )
