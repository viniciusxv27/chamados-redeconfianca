from django.db import models
from django.conf import settings
import requests
from core.utils import upload_communication_attachment

def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


class CommunicationGroup(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nome do Grupo")
    description = models.TextField(blank=True, verbose_name="Descrição")
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='communication_groups',
        verbose_name="Membros"
    )
    can_send = models.BooleanField(default=True, verbose_name="Pode Enviar Comunicados")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_groups',
        verbose_name="Criado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Grupo de Comunicação"
        verbose_name_plural = "Grupos de Comunicação"
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Communication(models.Model):
    SENDER_GROUP_CHOICES = [
    ]
    
    title = models.CharField(max_length=200, verbose_name="Título")
    message = models.TextField(verbose_name="Mensagem")
    image = models.ImageField(upload_to=upload_communication_attachment, storage=get_media_storage(), null=True, blank=True, verbose_name="Imagem")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='sent_communications',
        verbose_name="Remetente"
    )
    sender_group = models.CharField(
        max_length=20, 
        choices=SENDER_GROUP_CHOICES, 
        null=True, 
        blank=True,
        verbose_name="Grupo Remetente"
    )
    custom_group = models.ForeignKey(
        CommunicationGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Grupo Personalizado"
    )
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, 
        related_name='received_communications',
        blank=True,
        verbose_name="Destinatários"
    )
    send_to_all = models.BooleanField(default=False, verbose_name="Enviar para Todos")
    is_pinned = models.BooleanField(default=False, verbose_name="Fixar na Dashboard")
    is_popup = models.BooleanField(default=False, verbose_name="Exibir como Pop-up")
    
    # Reaction fields
    liked_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='liked_communications',
        verbose_name="Curtido por"
    )
    viewed_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='viewed_communications',
        verbose_name="Visualizado por"
    )
    
    active_from = models.DateTimeField(null=True, blank=True, verbose_name="Ativo a partir de")
    active_until = models.DateTimeField(null=True, blank=True, verbose_name="Ativo até")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data de Envio")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    
    class Meta:
        verbose_name = "Comunicado"
        verbose_name_plural = "Comunicados"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.title} - {self.sender.full_name}"
    
    def is_active(self):
        """Verifica se o comunicado está ativo no momento"""
        from django.utils import timezone
        now = timezone.now()
        
        if self.active_from and now < self.active_from:
            return False
        if self.active_until and now > self.active_until:
            return False
        return True
    
    def save(self, *args, **kwargs):
        # Se tem imagem, não pode ser pop-up
        if self.image and self.is_popup:
            self.is_popup = False
            
        is_new = self.pk is None
        super().save(*args, **kwargs)
        
        if is_new:
            self.trigger_notification()
            self.trigger_webhooks('COMMUNICATION_CREATED')
        else:
            self.trigger_webhooks('COMMUNICATION_UPDATED')
    
    def trigger_notification(self):
        """Dispara notificação via webhook para WhatsApp"""
        try:
            # Obter números dos destinatários
            if self.send_to_all:
                from users.models import User
                recipients = User.objects.filter(is_active=True)
            else:
                recipients = self.recipients.all()
            
            phone_numbers = [user.phone for user in recipients if user.phone]
            
            if phone_numbers:
                payload = {
                    'title': self.title,
                    'message': self.message,
                    'sender': self.sender.full_name,
                    'phone_numbers': phone_numbers,
                    'created_at': self.created_at.isoformat()
                }
                
                # URL do webhook para WhatsApp (configurar conforme necessário)
                webhook_url = "https://your-whatsapp-webhook.com/notify"
                requests.post(webhook_url, json=payload, timeout=10)
        except Exception as e:
            # Log do erro (implementar logging posteriormente)
            pass
    
    def trigger_webhooks(self, event_type):
        """Dispara webhooks configurados para eventos de comunicado"""
        try:
            # Importar aqui para evitar dependência circular
            from tickets.models import Webhook
            from core.middleware import log_action
            
            webhooks = Webhook.objects.filter(
                event=event_type,
                is_active=True
            )
            
            log_action(
                user=self.sender,
                action_type='WEBHOOK_TRIGGER',
                description=f'Disparando {webhooks.count()} webhook(s) para evento {event_type} - Comunicado: {self.title}'
            )
            
            for webhook in webhooks:
                webhook.trigger(self, user=self.sender)
                
        except Exception as e:
            # Log do erro
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f'Erro ao disparar webhooks para comunicado {self.id}: {e}')
            
            # Log também no sistema
            try:
                from core.middleware import log_action
                log_action(
                    user=self.sender,
                    action_type='WEBHOOK_ERROR',
                    description=f'Erro ao disparar webhook para comunicado {self.title}: {str(e)}'
                )
            except:
                pass


class CommunicationRead(models.Model):
    STATUS_CHOICES = [
        ('NAO_VISUALIZADO', 'Não Visualizado'),
        ('ESTOU_CIENTE', 'Estou Ciente'),
        ('ESTOU_COM_DUVIDA', 'Estou com Dúvida'),
    ]
    
    communication = models.ForeignKey(Communication, on_delete=models.CASCADE, verbose_name="Comunicado")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NAO_VISUALIZADO', verbose_name="Status")
    read_at = models.DateTimeField(auto_now_add=True, verbose_name="Lido em")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")
    
    class Meta:
        verbose_name = "Leitura de Comunicado"
        verbose_name_plural = "Leituras de Comunicados"
        unique_together = ['communication', 'user']
        ordering = ['-read_at']
    
    def __str__(self):
        return f"{self.communication.title} - {self.user.full_name} - {self.get_status_display()}"


class CommunicationComment(models.Model):
    communication = models.ForeignKey(
        Communication, 
        on_delete=models.CASCADE, 
        related_name='comments',
        verbose_name="Comunicado"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE,
        verbose_name="Usuário"
    )
    content = models.TextField(verbose_name="Comentário")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")
    
    class Meta:
        verbose_name = "Comentário"
        verbose_name_plural = "Comentários"
        ordering = ['created_at']
    
    def __str__(self):
        return f"{self.user.full_name}: {self.content[:50]}..."
