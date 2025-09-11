from django.db import models
from django.contrib.auth import get_user_model
from users.models import User

User = get_user_model()


class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('FILE', 'Novo Arquivo'),
        ('TICKET', 'Novo Chamado'),
        ('TRAINING', 'Novo Treinamento'),
        ('COMMUNICATION', 'Novo Comunicado'),
        ('TICKET_UPDATE', 'Atualização de Chamado'),
        ('SYSTEM', 'Notificação do Sistema'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200, verbose_name="Título")
    message = models.TextField(verbose_name="Mensagem")
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, verbose_name="Tipo")
    
    # Links e referências
    related_object_id = models.PositiveIntegerField(null=True, blank=True, verbose_name="ID do objeto relacionado")
    related_url = models.CharField(max_length=500, null=True, blank=True, verbose_name="URL relacionada")
    
    # Estado da notificação
    is_read = models.BooleanField(default=False, verbose_name="Lida")
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Notificação"
        verbose_name_plural = "Notificações"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.full_name} - {self.title}"
    
    def mark_as_read(self):
        """Marca a notificação como lida"""
        from django.utils import timezone
        self.is_read = True
        self.read_at = timezone.now()
        self.save()


# Adicionar ao core/models.py
class NotificationMixin:
    """Mixin para adicionar funcionalidades de notificação aos models"""
    
    @staticmethod
    def create_notification(user, title, message, notification_type, related_object_id=None, related_url=None):
        """Cria uma nova notificação para um usuário"""
        return Notification.objects.create(
            user=user,
            title=title,
            message=message,
            notification_type=notification_type,
            related_object_id=related_object_id,
            related_url=related_url
        )
    
    @staticmethod
    def create_notifications_for_users(users, title, message, notification_type, related_object_id=None, related_url=None):
        """Cria notificações em massa para uma lista de usuários"""
        notifications = []
        for user in users:
            notifications.append(
                Notification(
                    user=user,
                    title=title,
                    message=message,
                    notification_type=notification_type,
                    related_object_id=related_object_id,
                    related_url=related_url
                )
            )
        return Notification.objects.bulk_create(notifications)
