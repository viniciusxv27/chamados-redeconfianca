from django.db import models
from django.conf import settings
import requests


class Communication(models.Model):
    title = models.CharField(max_length=200, verbose_name="Título")
    message = models.TextField(verbose_name="Mensagem")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='sent_communications',
        verbose_name="Remetente"
    )
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL, 
        related_name='received_communications',
        blank=True,
        verbose_name="Destinatários"
    )
    send_to_all = models.BooleanField(default=False, verbose_name="Enviar para Todos")
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
        is_new = self.pk is None
        super().save(*args, **kwargs)
        
        if is_new:
            self.trigger_notification()
    
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
