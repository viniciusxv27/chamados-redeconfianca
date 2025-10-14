"""
Signals para capturar automaticamente ações importantes do sistema
"""
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import SystemLog


@receiver(user_logged_in)
def log_user_login(sender, request, user, **kwargs):
    """Registra quando um usuário faz login"""
    ip_address = None
    user_agent = ''
    
    if request:
        # Capturar IP
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip_address = x_forwarded_for.split(',')[0]
        else:
            ip_address = request.META.get('REMOTE_ADDR')
        
        # Capturar User Agent
        user_agent = request.META.get('HTTP_USER_AGENT', '')
    
    SystemLog.objects.create(
        user=user,
        action_type='USER_LOGIN',
        description=f'{user.get_full_name() or user.username} realizou login no sistema',
        ip_address=ip_address,
        user_agent=user_agent
    )


@receiver(user_logged_out)
def log_user_logout(sender, request, user, **kwargs):
    """Registra quando um usuário faz logout"""
    if user:
        ip_address = None
        user_agent = ''
        
        if request:
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                ip_address = x_forwarded_for.split(',')[0]
            else:
                ip_address = request.META.get('REMOTE_ADDR')
            
            user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        SystemLog.objects.create(
            user=user,
            action_type='USER_LOGOUT',
            description=f'{user.get_full_name() or user.username} realizou logout do sistema',
            ip_address=ip_address,
            user_agent=user_agent
        )


# Registrar criação de comunicações
@receiver(post_save, sender='communications.Communication')
def log_communication_creation(sender, instance, created, **kwargs):
    """Registra quando uma comunicação é criada"""
    if created:
        SystemLog.objects.create(
            user=instance.created_by,
            action_type='COMMUNICATION_SEND',
            description=f'Criou comunicado: {instance.title[:100]}'
        )


# Registrar criação de usuários
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def log_user_creation(sender, instance, created, **kwargs):
    """Registra quando um novo usuário é criado"""
    if created:
        SystemLog.objects.create(
            user=None,  # Não temos o criador aqui, poderia ser melhorado
            action_type='USER_CREATE',
            description=f'Novo usuário criado: {instance.get_full_name() or instance.username} ({instance.email})'
        )


# Registrar resgate de prêmios
@receiver(post_save, sender='prizes.Redemption')
def log_prize_redemption(sender, instance, created, **kwargs):
    """Registra quando um prêmio é resgatado"""
    if created:
        SystemLog.objects.create(
            user=instance.user,
            action_type='PRIZE_REDEEM',
            description=f'Resgatou prêmio: {instance.prize.name} por C$ {instance.prize.value_cs}'
        )


# Registrar alterações de C$ (se houver um modelo específico)
# Você pode adicionar mais signals conforme necessário
