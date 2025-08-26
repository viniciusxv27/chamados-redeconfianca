from django.db import models
from django.conf import settings
from users.models import Sector
import requests


class Category(models.Model):
    sector = models.ForeignKey(Sector, on_delete=models.CASCADE, verbose_name="Setor")
    name = models.CharField(max_length=100, verbose_name="Nome")
    webhook_url = models.URLField(blank=True, verbose_name="URL do Webhook")
    requires_approval = models.BooleanField(default=False, verbose_name="Requer Aprovação")
    default_description = models.TextField(blank=True, verbose_name="Descrição Padrão")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Categoria"
        verbose_name_plural = "Categorias"
        ordering = ['sector__name', 'name']
        unique_together = ['sector', 'name']
    
    def __str__(self):
        return f"{self.sector.name} - {self.name}"


class Ticket(models.Model):
    STATUS_CHOICES = [
        ('ABERTO', 'Aberto'),
        ('EM_ANDAMENTO', 'Em Andamento'),
        ('RESOLVIDO', 'Resolvido'),
        ('AGUARDANDO_APROVACAO', 'Aguardando Aprovação do Usuário'),
        ('FECHADO', 'Fechado'),
        ('REABERTO', 'Reaberto'),
    ]
    
    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(verbose_name="Descrição")
    sector = models.ForeignKey(Sector, on_delete=models.CASCADE, verbose_name="Setor")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, verbose_name="Categoria")
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='ABERTO', verbose_name="Status")
    solution = models.TextField(blank=True, verbose_name="Solução")
    
    # Usuários
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='created_tickets',
        verbose_name="Criado por"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='assigned_tickets',
        verbose_name="Responsável"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='approved_tickets',
        verbose_name="Aprovado por"
    )
    
    # Datas
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data de Abertura")
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Data de Resolução")
    closed_at = models.DateTimeField(null=True, blank=True, verbose_name="Data de Fechamento")
    
    # Controle de aprovação
    requires_approval = models.BooleanField(default=False, verbose_name="Requer Aprovação")
    approval_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='pending_approvals',
        verbose_name="Usuário para Aprovação"
    )
    
    class Meta:
        verbose_name = "Chamado"
        verbose_name_plural = "Chamados"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"#{self.id} - {self.title}"
    
    def save(self, *args, **kwargs):
        # Se é um novo ticket e a categoria tem webhook, dispara o webhook
        is_new = self.pk is None
        super().save(*args, **kwargs)
        
        if is_new and self.category.webhook_url:
            self.trigger_webhook()
    
    def trigger_webhook(self):
        """Dispara webhook quando o ticket é criado"""
        try:
            payload = {
                'ticket_id': self.id,
                'title': self.title,
                'description': self.description,
                'sector': self.sector.name,
                'category': self.category.name,
                'created_by': self.created_by.full_name,
                'created_at': self.created_at.isoformat(),
                'status': self.status
            }
            requests.post(self.category.webhook_url, json=payload, timeout=10)
        except Exception as e:
            # Log do erro (implementar logging posteriormente)
            pass


class TicketLog(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='logs', verbose_name="Chamado")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    old_status = models.CharField(max_length=25, blank=True, verbose_name="Status Anterior")
    new_status = models.CharField(max_length=25, verbose_name="Novo Status")
    observation = models.TextField(blank=True, verbose_name="Observação")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    
    class Meta:
        verbose_name = "Log do Chamado"
        verbose_name_plural = "Logs dos Chamados"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"#{self.ticket.id} - {self.old_status} → {self.new_status}"


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comments', verbose_name="Chamado")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    comment = models.TextField(verbose_name="Comentário")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    
    class Meta:
        verbose_name = "Comentário"
        verbose_name_plural = "Comentários"
        ordering = ['created_at']
    
    def __str__(self):
        return f"#{self.ticket.id} - {self.user.full_name}"
