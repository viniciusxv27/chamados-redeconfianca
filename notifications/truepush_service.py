"""
Serviço de integração com Truepush
Permite envio de notificações push via web e mobile através do serviço Truepush

Documentação: https://docs.truepush.com/
"""
import requests
import json
import logging
from typing import List, Dict, Any, Optional, Union
from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()
logger = logging.getLogger(__name__)


class TruepushService:
    """
    Serviço para integração com Truepush API
    
    Configurações necessárias no settings.py:
        TRUEPUSH_API_KEY = 'sua_api_key'
        TRUEPUSH_PROJECT_ID = 'seu_project_id'
    """
    
    BASE_URL = "https://api.truepush.com/api/v1"
    
    def __init__(self):
        self.api_key = getattr(settings, 'TRUEPUSH_API_KEY', '')
        self.project_id = getattr(settings, 'TRUEPUSH_PROJECT_ID', '')
        self.enabled = bool(self.api_key and self.project_id)
        
        if not self.enabled:
            logger.warning("Truepush não configurado. Defina TRUEPUSH_API_KEY e TRUEPUSH_PROJECT_ID no settings.py")
    
    def _get_headers(self) -> Dict[str, str]:
        """Retorna headers para requisições à API do Truepush"""
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
    
    def _log_notification(
        self,
        title: str,
        message: str,
        url: str = '',
        segment_id: str = '',
        sent_to_all: bool = True,
        success: bool = False,
        sent_count: int = 0,
        response_data: Dict = None,
        error_message: str = '',
        sent_by=None
    ):
        """Registra log de notificação no banco de dados"""
        try:
            from .models import TruepushNotificationLog
            TruepushNotificationLog.objects.create(
                title=title,
                message=message,
                url=url,
                segment_id=segment_id or '',
                sent_to_all=sent_to_all,
                success=success,
                sent_count=sent_count,
                response_data=response_data or {},
                error_message=error_message,
                sent_by=sent_by
            )
        except Exception as e:
            logger.error(f"Erro ao registrar log de notificação Truepush: {e}")
    
    def send_notification(
        self,
        title: str,
        message: str,
        url: str = '/',
        icon: str = None,
        image: str = None,
        badge: str = None,
        segment_id: str = None,
        subscriber_ids: List[str] = None,
        data: Dict = None,
        actions: List[Dict] = None,
        ttl: int = 86400,
        require_interaction: bool = False,
        silent: bool = False
    ) -> Dict[str, Any]:
        """
        Envia notificação push via Truepush
        
        Args:
            title: Título da notificação
            message: Corpo da mensagem
            url: URL para abrir ao clicar
            icon: URL do ícone (opcional)
            image: URL de imagem grande (opcional)
            badge: URL do badge (opcional)
            segment_id: ID do segmento de usuários (opcional)
            subscriber_ids: Lista de IDs de assinantes específicos (opcional)
            data: Dados extras para a notificação (opcional)
            actions: Lista de ações/botões (opcional)
            ttl: Time to live em segundos (padrão: 24h)
            require_interaction: Se requer interação do usuário
            silent: Se é uma notificação silenciosa
            
        Returns:
            Dict com resultado do envio
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'Truepush não configurado',
                'sent_count': 0
            }
        
        try:
            # Preparar payload
            payload = {
                'title': title,
                'body': message,
                'targetUrl': url,
                'ttl': ttl,
                'requireInteraction': require_interaction,
                'silent': silent
            }
            
            # Adicionar campos opcionais
            if icon:
                payload['icon'] = icon
            else:
                # Usar ícone padrão do sistema
                base_url = getattr(settings, 'BASE_URL', 'https://chamados.redeconfianca.com.br')
                payload['icon'] = f"{base_url}/static/images/logo.png"
            
            if image:
                payload['image'] = image
                
            if badge:
                payload['badge'] = badge
            
            if data:
                payload['data'] = data
                
            if actions:
                payload['actions'] = actions
            
            # Definir alvo (segmento ou assinantes específicos)
            if subscriber_ids:
                payload['subscriberIds'] = subscriber_ids
            elif segment_id:
                payload['segmentId'] = segment_id
            else:
                # Enviar para todos os assinantes
                payload['segmentId'] = 'all'
            
            # Fazer requisição
            url_api = f"{self.BASE_URL}/notifications/{self.project_id}"
            
            response = requests.post(
                url_api,
                headers=self._get_headers(),
                json=payload,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                result = response.json()
                logger.info(f"Truepush: Notificação enviada com sucesso - {title}")
                
                # Registrar log no banco de dados
                self._log_notification(
                    title=title,
                    message=message,
                    url=url,
                    segment_id=segment_id,
                    sent_to_all=not subscriber_ids and not segment_id,
                    success=True,
                    sent_count=result.get('successCount', 1),
                    response_data=result
                )
                
                return {
                    'success': True,
                    'message': 'Notificação enviada via Truepush',
                    'response': result,
                    'sent_count': result.get('successCount', 1)
                }
            else:
                error_msg = response.text
                logger.error(f"Truepush: Erro ao enviar notificação - Status {response.status_code}: {error_msg}")
                
                # Registrar log de erro
                self._log_notification(
                    title=title,
                    message=message,
                    url=url,
                    segment_id=segment_id,
                    sent_to_all=not subscriber_ids and not segment_id,
                    success=False,
                    sent_count=0,
                    error_message=error_msg
                )
                
                return {
                    'success': False,
                    'error': error_msg,
                    'status_code': response.status_code,
                    'sent_count': 0
                }
                
        except requests.exceptions.Timeout:
            logger.error("Truepush: Timeout ao enviar notificação")
            return {
                'success': False,
                'error': 'Timeout na requisição',
                'sent_count': 0
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Truepush: Erro de requisição - {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'sent_count': 0
            }
        except Exception as e:
            logger.error(f"Truepush: Erro inesperado - {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'sent_count': 0
            }
    
    def send_to_segment(
        self,
        segment_id: str,
        title: str,
        message: str,
        url: str = '/',
        **kwargs
    ) -> Dict[str, Any]:
        """
        Envia notificação para um segmento específico
        
        Args:
            segment_id: ID do segmento no Truepush
            title: Título da notificação
            message: Mensagem
            url: URL ao clicar
            **kwargs: Argumentos adicionais para send_notification
        """
        return self.send_notification(
            title=title,
            message=message,
            url=url,
            segment_id=segment_id,
            **kwargs
        )
    
    def send_to_subscribers(
        self,
        subscriber_ids: List[str],
        title: str,
        message: str,
        url: str = '/',
        **kwargs
    ) -> Dict[str, Any]:
        """
        Envia notificação para assinantes específicos
        
        Args:
            subscriber_ids: Lista de IDs de assinantes Truepush
            title: Título da notificação
            message: Mensagem
            url: URL ao clicar
            **kwargs: Argumentos adicionais
        """
        return self.send_notification(
            title=title,
            message=message,
            url=url,
            subscriber_ids=subscriber_ids,
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
        
        Args:
            title: Título da notificação
            message: Mensagem
            url: URL ao clicar
            **kwargs: Argumentos adicionais
        """
        return self.send_notification(
            title=title,
            message=message,
            url=url,
            **kwargs
        )
    
    def create_segment(
        self,
        name: str,
        description: str = '',
        rules: Dict = None
    ) -> Dict[str, Any]:
        """
        Cria um novo segmento de usuários
        
        Args:
            name: Nome do segmento
            description: Descrição
            rules: Regras de segmentação
            
        Returns:
            Dict com resultado da criação
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'Truepush não configurado'
            }
        
        try:
            payload = {
                'name': name,
                'description': description
            }
            
            if rules:
                payload['rules'] = rules
            
            response = requests.post(
                f"{self.BASE_URL}/segments/{self.project_id}",
                headers=self._get_headers(),
                json=payload,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                return {
                    'success': True,
                    'segment': response.json()
                }
            else:
                return {
                    'success': False,
                    'error': response.text
                }
                
        except Exception as e:
            logger.error(f"Truepush: Erro ao criar segmento - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_segments(self) -> Dict[str, Any]:
        """
        Lista todos os segmentos do projeto
        
        Returns:
            Dict com lista de segmentos
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'Truepush não configurado'
            }
        
        try:
            response = requests.get(
                f"{self.BASE_URL}/segments/{self.project_id}",
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'segments': response.json()
                }
            else:
                return {
                    'success': False,
                    'error': response.text
                }
                
        except Exception as e:
            logger.error(f"Truepush: Erro ao listar segmentos - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_stats(self, notification_id: str = None) -> Dict[str, Any]:
        """
        Obtém estatísticas de notificações
        
        Args:
            notification_id: ID específico da notificação (opcional)
            
        Returns:
            Dict com estatísticas
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'Truepush não configurado'
            }
        
        try:
            if notification_id:
                url = f"{self.BASE_URL}/notifications/{self.project_id}/{notification_id}/stats"
            else:
                url = f"{self.BASE_URL}/projects/{self.project_id}/stats"
            
            response = requests.get(
                url,
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'stats': response.json()
                }
            else:
                return {
                    'success': False,
                    'error': response.text
                }
                
        except Exception as e:
            logger.error(f"Truepush: Erro ao obter estatísticas - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def get_subscriber_count(self) -> Dict[str, Any]:
        """
        Obtém contagem de assinantes
        
        Returns:
            Dict com contagem de assinantes
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'Truepush não configurado'
            }
        
        try:
            response = requests.get(
                f"{self.BASE_URL}/projects/{self.project_id}/subscribers/count",
                headers=self._get_headers(),
                timeout=30
            )
            
            if response.status_code == 200:
                return {
                    'success': True,
                    'count': response.json().get('count', 0)
                }
            else:
                return {
                    'success': False,
                    'error': response.text
                }
                
        except Exception as e:
            logger.error(f"Truepush: Erro ao obter contagem de assinantes - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def delete_notification(self, notification_id: str) -> Dict[str, Any]:
        """
        Cancela/deleta uma notificação agendada
        
        Args:
            notification_id: ID da notificação
            
        Returns:
            Dict com resultado
        """
        if not self.enabled:
            return {
                'success': False,
                'error': 'Truepush não configurado'
            }
        
        try:
            response = requests.delete(
                f"{self.BASE_URL}/notifications/{self.project_id}/{notification_id}",
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
            logger.error(f"Truepush: Erro ao cancelar notificação - {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }


# Instância global do serviço
truepush_service = TruepushService()


# Funções de conveniência
def send_truepush_notification(
    title: str,
    message: str,
    url: str = '/',
    **kwargs
) -> Dict[str, Any]:
    """Função de conveniência para enviar notificação Truepush"""
    return truepush_service.send_notification(
        title=title,
        message=message,
        url=url,
        **kwargs
    )


def send_truepush_to_all(
    title: str,
    message: str,
    url: str = '/',
    **kwargs
) -> Dict[str, Any]:
    """Função de conveniência para enviar para todos"""
    return truepush_service.send_to_all(
        title=title,
        message=message,
        url=url,
        **kwargs
    )
