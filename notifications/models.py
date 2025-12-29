from django.db import models
from django.conf import settings
from django.utils import timezone
from users.models import User, Sector
import json


class NotificationCategory(models.Model):
    """Categorias de notificações"""
    
    name = models.CharField(max_length=100, verbose_name="Nome")
    icon = models.CharField(max_length=50, default="fas fa-bell", verbose_name="Ícone Font Awesome")
    color = models.CharField(max_length=20, default="blue", verbose_name="Cor")
    is_active = models.BooleanField(default=True, verbose_name="Ativa")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Categoria de Notificação"
        verbose_name_plural = "Categorias de Notificações"
        ordering = ['name']
    
    def __str__(self):
        return self.name


class PushNotification(models.Model):
    """Notificações push para o sistema"""
    
    TYPE_CHOICES = [
        ('SYSTEM', 'Sistema'),
        ('TICKET', 'Chamado'),
        ('COMMUNICATION', 'Comunicado'),
        ('TASK', 'Tarefa'),
        ('CUSTOM', 'Personalizada'),
    ]
    
    PRIORITY_CHOICES = [
        ('LOW', 'Baixa'),
        ('NORMAL', 'Normal'),
        ('HIGH', 'Alta'),
        ('URGENT', 'Urgente'),
    ]
    
    # Conteúdo da notificação
    title = models.CharField(max_length=200, verbose_name="Título")
    message = models.TextField(verbose_name="Mensagem")
    category = models.ForeignKey(
        NotificationCategory, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        verbose_name="Categoria"
    )
    
    # Tipo e prioridade
    notification_type = models.CharField(
        max_length=20, 
        choices=TYPE_CHOICES, 
        default='CUSTOM',
        verbose_name="Tipo"
    )
    priority = models.CharField(
        max_length=10, 
        choices=PRIORITY_CHOICES, 
        default='NORMAL',
        verbose_name="Prioridade"
    )
    
    # Configurações de exibição
    icon = models.CharField(max_length=50, default="fas fa-bell", verbose_name="Ícone")
    action_url = models.URLField(blank=True, verbose_name="URL da Ação")
    action_text = models.CharField(max_length=50, blank=True, verbose_name="Texto da Ação")
    
    # Configurações de envio
    send_to_all = models.BooleanField(default=False, verbose_name="Enviar para Todos")
    target_sectors = models.ManyToManyField(
        Sector, 
        blank=True, 
        verbose_name="Setores Alvo"
    )
    target_users = models.ManyToManyField(
        User, 
        blank=True, 
        related_name='targeted_notifications',
        verbose_name="Usuários Alvo"
    )
    
    # Controle de agendamento
    schedule_for = models.DateTimeField(
        null=True, 
        blank=True, 
        verbose_name="Agendar para"
    )
    is_scheduled = models.BooleanField(default=False, verbose_name="Agendada")
    
    # Metadados
    created_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='created_notifications',
        verbose_name="Criado por"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="Enviado em")
    is_sent = models.BooleanField(default=False, verbose_name="Enviado")
    
    # Dados adicionais (JSON)
    extra_data = models.JSONField(default=dict, blank=True, verbose_name="Dados Extras")
    
    class Meta:
        verbose_name = "Notificação Push"
        verbose_name_plural = "Notificações Push"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.title} - {self.get_notification_type_display()}"
    
    def get_target_users_count(self):
        """Retorna o número de usuários que receberão a notificação"""
        if self.send_to_all:
            return User.objects.filter(is_active=True).count()
        
        count = self.target_users.count()
        for sector in self.target_sectors.all():
            count += sector.users.filter(is_active=True).count()
        return count
    
    def send_notification(self):
        """Envia a notificação para os usuários alvo"""
        if self.is_sent:
            return False
        
        # Determinar usuários alvo
        target_users = set()
        
        if self.send_to_all:
            target_users.update(User.objects.filter(is_active=True))
        else:
            # Usuários específicos
            target_users.update(self.target_users.filter(is_active=True))
            
            # Usuários dos setores
            for sector in self.target_sectors.all():
                target_users.update(sector.users.filter(is_active=True))
        
        # Criar registros de notificação para cada usuário
        notification_records = []
        for user in target_users:
            notification_records.append(
                UserNotification(
                    notification=self,
                    user=user,
                    is_read=False
                )
            )
        
        # Bulk create para performance
        UserNotification.objects.bulk_create(notification_records)
        
        # Enviar push notifications usando o serviço
        try:
            from .push_utils import send_push_notification_to_users
            
            # Preparar dados extras para o push
            push_data = {
                'notification_id': self.id,
                'action_url': self.action_url if self.action_url else '/',
                'action_text': self.action_text,
                'icon': '/static/images/logo.png',
                'extra_data': self.extra_data,
                'timestamp': int(timezone.now().timestamp() * 1000)
            }
            
            # Enviar push notification
            push_result = send_push_notification_to_users(
                list(target_users),
                self.title,
                self.message,
                **push_data
            )
            
            # Log do resultado
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Push notification {self.id}: {push_result['message']}")
            
        except Exception as e:
            # Log do erro mas não falhar o processo
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error sending push notification {self.id}: {str(e)}")
        
        # Marcar como enviada
        self.is_sent = True
        self.sent_at = timezone.now()  
        self.save()
        
        return True


class UserNotification(models.Model):
    """Registro de notificação por usuário"""
    
    notification = models.ForeignKey(
        PushNotification, 
        on_delete=models.CASCADE, 
        related_name='user_notifications'
    )
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='notifications'
    )
    
    # Status
    is_read = models.BooleanField(default=False, verbose_name="Lida")
    is_clicked = models.BooleanField(default=False, verbose_name="Clicada")
    read_at = models.DateTimeField(null=True, blank=True, verbose_name="Lida em")
    clicked_at = models.DateTimeField(null=True, blank=True, verbose_name="Clicada em")
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Notificação do Usuário"
        verbose_name_plural = "Notificações dos Usuários"
        ordering = ['-created_at']
        unique_together = ['notification', 'user']
    
    def __str__(self):
        return f"{self.notification.title} - {self.user.get_full_name()}"
    
    def mark_as_read(self):
        """Marca a notificação como lida"""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save()
    
    def mark_as_clicked(self):
        """Marca a notificação como clicada"""
        if not self.is_clicked:
            self.is_clicked = True
            self.clicked_at = timezone.now()
            if not self.is_read:
                self.mark_as_read()
            else:
                self.save()


class DeviceToken(models.Model):
    """Tokens de dispositivos para push notifications"""
    
    DEVICE_TYPES = [
        ('WEB', 'Web Browser'),
        ('ANDROID', 'Android'),
        ('IOS', 'iOS'),
    ]
    
    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='device_tokens'
    )
    token = models.TextField(verbose_name="Token do Dispositivo")
    device_type = models.CharField(
        max_length=10, 
        choices=DEVICE_TYPES, 
        default='WEB',
        verbose_name="Tipo do Dispositivo"
    )
    device_info = models.JSONField(
        default=dict, 
        blank=True, 
        verbose_name="Informações do Dispositivo"
    )
    
    # Controle
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Token do Dispositivo"
        verbose_name_plural = "Tokens dos Dispositivos"
        unique_together = ['user', 'token']
        ordering = ['-last_used']
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.get_device_type_display()}"


class NotificationPreference(models.Model):
    """Preferências de notificação do usuário"""
    
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='notification_preferences',
        verbose_name="Usuário"
    )
    
    # Canais habilitados
    in_app_enabled = models.BooleanField(default=True, verbose_name="Notificações In-App")
    push_enabled = models.BooleanField(default=True, verbose_name="Notificações Push/Browser")
    email_enabled = models.BooleanField(default=True, verbose_name="Notificações por Email")
    
    # Tipos de notificação habilitados
    ticket_created = models.BooleanField(default=True, verbose_name="Novos Chamados")
    ticket_assigned = models.BooleanField(default=True, verbose_name="Atribuição de Chamados")
    ticket_status_changed = models.BooleanField(default=True, verbose_name="Mudança de Status")
    ticket_comment = models.BooleanField(default=True, verbose_name="Novos Comentários")
    communication_new = models.BooleanField(default=True, verbose_name="Novos Comunicados")
    
    # Configurações de horário
    quiet_hours_enabled = models.BooleanField(default=False, verbose_name="Horário Silencioso")
    quiet_hours_start = models.TimeField(null=True, blank=True, verbose_name="Início do Silêncio")
    quiet_hours_end = models.TimeField(null=True, blank=True, verbose_name="Fim do Silêncio")
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Preferência de Notificação"
        verbose_name_plural = "Preferências de Notificações"
    
    def __str__(self):
        return f"Preferências de {self.user.full_name}"
    
    def is_channel_enabled(self, channel: str) -> bool:
        """Verifica se um canal está habilitado"""
        channel_map = {
            'in_app': self.in_app_enabled,
            'push': self.push_enabled,
            'browser': self.push_enabled,
            'email': self.email_enabled,
        }
        return channel_map.get(channel, True)
    
    def is_type_enabled(self, notification_type: str) -> bool:
        """Verifica se um tipo de notificação está habilitado"""
        type_map = {
            'ticket_created': self.ticket_created,
            'ticket_assigned': self.ticket_assigned,
            'ticket_status_changed': self.ticket_status_changed,
            'ticket_comment': self.ticket_comment,
            'communication_new': self.communication_new,
        }
        return type_map.get(notification_type, True)
    
    def is_quiet_hours(self) -> bool:
        """Verifica se está em horário silencioso"""
        if not self.quiet_hours_enabled:
            return False
        
        if not self.quiet_hours_start or not self.quiet_hours_end:
            return False
        
        from django.utils import timezone
        current_time = timezone.localtime().time()
        
        if self.quiet_hours_start <= self.quiet_hours_end:
            return self.quiet_hours_start <= current_time <= self.quiet_hours_end
        else:
            # Horário atravessa meia-noite (ex: 22:00 - 07:00)
            return current_time >= self.quiet_hours_start or current_time <= self.quiet_hours_end
    
    @classmethod
    def get_or_create_for_user(cls, user):
        """Obtém ou cria preferências para um usuário"""
        preferences, created = cls.objects.get_or_create(user=user)
        return preferences


class OneSignalPlayer(models.Model):
    """
    Modelo para vincular Player IDs do OneSignal a usuários do sistema.
    Permite enviar notificações para usuários específicos.
    """
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='onesignal_players',
        verbose_name="Usuário",
        null=True,
        blank=True
    )
    player_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name="Player ID OneSignal"
    )
    
    # Informações do dispositivo
    device_type = models.CharField(
        max_length=50,
        default='web',
        verbose_name="Tipo de Dispositivo"
    )
    browser = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Navegador"
    )
    os = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Sistema Operacional"
    )
    
    # Status
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    
    # Metadados
    extra_data = models.JSONField(default=dict, blank=True, verbose_name="Dados Extras")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Player OneSignal"
        verbose_name_plural = "Players OneSignal"
        ordering = ['-created_at']
    
    def __str__(self):
        if self.user:
            return f"{self.user.get_full_name()} - {self.player_id[:20]}..."
        return f"Anônimo - {self.player_id[:20]}..."


class OneSignalNotificationLog(models.Model):
    """
    Log de notificações enviadas via OneSignal.
    Permite rastrear histórico e métricas.
    """
    
    # Conteúdo da notificação
    title = models.CharField(max_length=200, verbose_name="Título")
    message = models.TextField(verbose_name="Mensagem")
    url = models.CharField(max_length=500, blank=True, verbose_name="URL de Destino")
    
    # Alvo
    segment = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Segmento"
    )
    sent_to_all = models.BooleanField(default=True, verbose_name="Enviado para Todos")
    
    # Resultado
    success = models.BooleanField(default=False, verbose_name="Sucesso")
    sent_count = models.IntegerField(default=0, verbose_name="Quantidade Enviada")
    notification_id = models.CharField(max_length=255, blank=True, verbose_name="ID da Notificação OneSignal")
    response_data = models.JSONField(default=dict, blank=True, verbose_name="Resposta da API")
    error_message = models.TextField(blank=True, verbose_name="Mensagem de Erro")
    
    # Quem enviou
    sent_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='onesignal_notifications_sent',
        verbose_name="Enviado por"
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Log de Notificação OneSignal"
        verbose_name_plural = "Logs de Notificações OneSignal"
        ordering = ['-created_at']
    
    def __str__(self):
        status = "✓" if self.success else "✗"
        return f"[{status}] {self.title} - {self.created_at.strftime('%d/%m/%Y %H:%M')}"


# Modelos legados (Truepush) - mantidos para compatibilidade com migrações existentes
class TruepushSubscriber(models.Model):
    """DEPRECATED: Use OneSignalPlayer instead"""
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='truepush_subscriptions',
        verbose_name="Usuário",
        null=True,
        blank=True
    )
    subscriber_id = models.CharField(
        max_length=255,
        unique=True,
        verbose_name="ID do Assinante Truepush"
    )
    device_type = models.CharField(max_length=50, default='web')
    browser = models.CharField(max_length=100, blank=True)
    os = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    extra_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Assinante Truepush (Legado)"
        verbose_name_plural = "Assinantes Truepush (Legado)"


class TruepushNotificationLog(models.Model):
    """DEPRECATED: Use OneSignalNotificationLog instead"""
    
    title = models.CharField(max_length=200)
    message = models.TextField()
    url = models.URLField(blank=True)
    segment_id = models.CharField(max_length=255, blank=True)
    sent_to_all = models.BooleanField(default=True)
    success = models.BooleanField(default=False)
    sent_count = models.IntegerField(default=0)
    response_data = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    sent_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='truepush_notifications_sent')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Log Truepush (Legado)"
        verbose_name_plural = "Logs Truepush (Legado)"