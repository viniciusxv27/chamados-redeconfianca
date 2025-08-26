from django.db import models
from django.conf import settings


class SystemLog(models.Model):
    ACTION_TYPES = [
        ('USER_LOGIN', 'Login de Usuário'),
        ('USER_LOGOUT', 'Logout de Usuário'),
        ('TICKET_CREATE', 'Criação de Chamado'),
        ('TICKET_UPDATE', 'Atualização de Chamado'),
        ('CS_CHANGE', 'Alteração de C$'),
        ('PRIZE_REDEEM', 'Resgate de Prêmio'),
        ('COMMUNICATION_SEND', 'Envio de Comunicado'),
        ('USER_CREATE', 'Criação de Usuário'),
        ('USER_UPDATE', 'Atualização de Usuário'),
        ('ADMIN_ACTION', 'Ação Administrativa'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Usuário"
    )
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES, verbose_name="Tipo de Ação")
    description = models.TextField(verbose_name="Descrição")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="Endereço IP")
    user_agent = models.TextField(blank=True, verbose_name="User Agent")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    
    class Meta:
        verbose_name = "Log do Sistema"
        verbose_name_plural = "Logs do Sistema"
        ordering = ['-created_at']
    
    def __str__(self):
        user_name = self.user.full_name if self.user else "Sistema"
        return f"{user_name} - {self.action_type}"
