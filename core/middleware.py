from django.utils.deprecation import MiddlewareMixin
from .models import SystemLog


class LoggingMiddleware(MiddlewareMixin):
    def process_request(self, request):
        # Armazenar informações da requisição para uso posterior
        request.user_ip = self.get_client_ip(request)
        request.user_agent = request.META.get('HTTP_USER_AGENT', '')
        return None
    
    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip


def log_action(user, action_type, description, request=None):
    """
    Função utilitária para registrar ações no sistema
    """
    ip_address = None
    user_agent = ''
    
    if request:
        ip_address = getattr(request, 'user_ip', None)
        user_agent = getattr(request, 'user_agent', '')
    
    SystemLog.objects.create(
        user=user,
        action_type=action_type,
        description=description,
        ip_address=ip_address,
        user_agent=user_agent
    )
