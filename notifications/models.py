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