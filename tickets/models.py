from django.db import models
from django.conf import settings
from django.utils import timezone
from users.models import Sector
import requests


class Category(models.Model):
    sector = models.ForeignKey(Sector, on_delete=models.CASCADE, verbose_name="Setor")
    name = models.CharField(max_length=100, verbose_name="Nome")
    webhook_url = models.URLField(blank=True, verbose_name="URL do Webhook")
    requires_approval = models.BooleanField(default=False, verbose_name="Requer Aprovação")
    default_description = models.TextField(blank=True, verbose_name="Descrição Padrão")
    default_solution_time_hours = models.IntegerField(default=24, verbose_name="Tempo Padrão para Solução (horas)")
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
    PRIORITY_CHOICES = [
        ('BAIXA', 'Baixa'),
        ('MEDIA', 'Média'),
        ('ALTA', 'Alta'),
        ('CRITICA', 'Crítica'),
    ]
    
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
    priority = models.CharField(max_length=15, choices=PRIORITY_CHOICES, default='MEDIA', verbose_name="Prioridade")
    solution = models.TextField(blank=True, verbose_name="Solução")
    solution_time_hours = models.IntegerField(default=24, verbose_name="Tempo para Solução (horas)")
    due_date = models.DateTimeField(null=True, blank=True, verbose_name="Data Limite")
    
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
    
    # Denúncia anônima
    is_anonymous = models.BooleanField(default=False, verbose_name="Denúncia Anônima")
    
    class Meta:
        verbose_name = "Chamado"
        verbose_name_plural = "Chamados"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"#{self.id} - {self.title}"
    
    def save(self, *args, **kwargs):
        # Se é um novo ticket e a categoria tem webhook, dispara o webhook
        is_new = self.pk is None
        old_status = None
        
        if not is_new:
            old_instance = Ticket.objects.get(pk=self.pk)
            old_status = old_instance.status
        
        # Se é um novo ticket, usar o tempo padrão da categoria
        if is_new:
            if hasattr(self, 'category') and self.category and self.category.default_solution_time_hours:
                self.solution_time_hours = self.category.default_solution_time_hours
            
            # Calcular data limite baseada no tempo para solução
            if self.solution_time_hours:
                from datetime import timedelta
                self.due_date = timezone.now() + timedelta(hours=self.solution_time_hours)
        
        super().save(*args, **kwargs)
        
        # Disparar webhooks
        if is_new:
            self.trigger_webhooks('TICKET_CREATED')
            if self.category.webhook_url:
                self.trigger_webhook()
        elif old_status != self.status:
            if self.status == 'RESOLVIDO':
                self.trigger_webhooks('TICKET_RESOLVED')
            elif self.status == 'FECHADO':
                self.trigger_webhooks('TICKET_CLOSED')
            else:
                self.trigger_webhooks('TICKET_UPDATED')
    
    def trigger_webhooks(self, event_type, user=None):
        """Dispara todos os webhooks configurados para o evento"""
        webhooks = Webhook.objects.filter(event=event_type, is_active=True)
        for webhook in webhooks:
            webhook.trigger(self, user)
    
    @property
    def is_overdue(self):
        """Verifica se o chamado está em atraso"""
        if self.due_date and self.status not in ['FECHADO', 'RESOLVIDO']:
            from django.utils import timezone
            return timezone.now() > self.due_date
        return False
    
    @property
    def time_remaining(self):
        """Retorna o tempo restante até o vencimento"""
        if self.due_date and self.status not in ['FECHADO', 'RESOLVIDO']:
            from django.utils import timezone
            remaining = self.due_date - timezone.now()
            if remaining.total_seconds() > 0:
                return remaining
        return None
    
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
    
    def can_assume(self, user):
        """Verifica se o usuário pode assumir o chamado"""
        # Não pode assumir se já é o criador
        if self.created_by == user:
            return False
        
        # Não pode assumir se já é o responsável
        if self.assigned_to == user:
            return False
        
        # Só pode assumir se o status permitir
        if self.status in ['FECHADO']:
            return False
        
        # Verifica permissões do usuário
        if user.can_view_all_tickets() or user.can_view_sector_tickets():
            return True
        
        return False
    
    def assume_ticket(self, user, comment=None):
        """Permite que um usuário assuma o chamado"""
        if not self.can_assume(user):
            return False
        
        # Atualiza o responsável
        old_assigned = self.assigned_to
        self.assigned_to = user
        
        # Se não estava em andamento, muda o status
        if self.status == 'ABERTO':
            self.status = 'EM_ANDAMENTO'
        
        self.save()
        
        # Registra no histórico
        comment_text = comment or f"Chamado assumido por {user.full_name}"
        TicketComment.objects.create(
            ticket=self,
            user=user,
            comment=comment_text,
            comment_type='ASSUMPTION'
        )
        
        # Log da mudança
        TicketLog.objects.create(
            ticket=self,
            user=user,
            old_status='ABERTO' if self.status == 'EM_ANDAMENTO' else self.status,
            new_status=self.status,
            observation=f"Chamado assumido. Responsável anterior: {old_assigned.full_name if old_assigned else 'Nenhum'}"
        )
        
        return True
    
    def assign_additional_user(self, user, assigned_by, comment=None):
        """Atribui um usuário adicional para auxiliar no chamado"""
        assignment, created = TicketAssignment.objects.get_or_create(
            ticket=self,
            user=user,
            defaults={'assigned_by': assigned_by}
        )
        
        if created:
            # Registra no histórico
            comment_text = comment or f"{user.full_name} foi atribuído para auxiliar no chamado"
            TicketComment.objects.create(
                ticket=self,
                user=assigned_by,
                comment=comment_text,
                comment_type='ASSIGNMENT',
                assigned_to=user
            )
        
        return assignment
    
    def mark_as_viewed(self, user):
        """Marca o chamado como visualizado pelo usuário"""
        view, created = TicketView.objects.get_or_create(
            ticket=self,
            user=user
        )
        return view
    
    def get_all_assigned_users(self):
        """Retorna todos os usuários atribuídos ao chamado (principal + auxiliares)"""
        users = []
        if self.assigned_to:
            users.append(self.assigned_to)
        
        additional_users = self.additional_assignments.filter(is_active=True).select_related('user')
        users.extend([assignment.user for assignment in additional_users])
        
        return users


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
    COMMENT_TYPES = [
        ('COMMENT', 'Comentário'),
        ('FOLLOW_UP', 'Acompanhamento'),
        ('ASSIGNMENT', 'Atribuição'),
        ('STATUS_CHANGE', 'Mudança de Status'),
        ('ASSUMPTION', 'Assumir Chamado'),
    ]
    
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='comments', verbose_name="Chamado")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    comment = models.TextField(verbose_name="Comentário")
    comment_type = models.CharField(max_length=15, choices=COMMENT_TYPES, default='COMMENT', verbose_name="Tipo")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='ticket_assignments',
        verbose_name="Atribuído para"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    
    class Meta:
        verbose_name = "Comentário"
        verbose_name_plural = "Comentários"
        ordering = ['created_at']
    
    def __str__(self):
        return f"#{self.ticket.id} - {self.user.full_name} - {self.get_comment_type_display()}"


class TicketView(models.Model):
    """Registra quando um usuário visualiza um chamado"""
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='views', verbose_name="Chamado")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    viewed_at = models.DateTimeField(auto_now_add=True, verbose_name="Visualizado em")
    
    class Meta:
        verbose_name = "Visualização do Chamado"
        verbose_name_plural = "Visualizações dos Chamados"
        unique_together = ['ticket', 'user']
        ordering = ['-viewed_at']
    
    def __str__(self):
        return f"#{self.ticket.id} - {self.user.full_name} - {self.viewed_at}"


class TicketAssignment(models.Model):
    """Registra usuários adicionais atribuídos para auxiliar no chamado"""
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='additional_assignments', verbose_name="Chamado")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='assignments_made',
        verbose_name="Atribuído por"
    )
    assigned_at = models.DateTimeField(auto_now_add=True, verbose_name="Atribuído em")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    
    class Meta:
        verbose_name = "Atribuição Adicional"
        verbose_name_plural = "Atribuições Adicionais"
        unique_together = ['ticket', 'user']
        ordering = ['-assigned_at']
    
    def __str__(self):
        return f"#{self.ticket.id} - {self.user.full_name} - Atribuído por {self.assigned_by.full_name}"


class Webhook(models.Model):
    EVENT_CHOICES = [
        ('TICKET_CREATED', 'Chamado Criado'),
        ('TICKET_UPDATED', 'Chamado Atualizado'),
        ('TICKET_RESOLVED', 'Chamado Resolvido'),
        ('TICKET_CLOSED', 'Chamado Fechado'),
        ('CATEGORY_CREATED', 'Categoria Criada'),
        ('USER_CREATED', 'Usuário Criado'),
    ]
    
    name = models.CharField(max_length=100, verbose_name="Nome")
    url = models.URLField(verbose_name="URL do Webhook")
    event = models.CharField(max_length=20, choices=EVENT_CHOICES, verbose_name="Evento")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Categoria (filtro)")
    sector = models.ForeignKey(Sector, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Setor (filtro)")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    headers = models.JSONField(default=dict, blank=True, verbose_name="Headers Customizados")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Webhook"
        verbose_name_plural = "Webhooks"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} - {self.get_event_display()}"
    
    def trigger(self, instance, user=None):
        """Dispara o webhook com os dados da instância"""
        try:
            if not self.is_active:
                return
            
            # Verificar filtros
            if hasattr(instance, 'category') and self.category and instance.category != self.category:
                return
            if hasattr(instance, 'sector') and self.sector and instance.sector != self.sector:
                return
            
            payload = self._build_payload(instance, user)
            headers = {'Content-Type': 'application/json'}
            if self.headers:
                headers.update(self.headers)
            
            requests.post(self.url, json=payload, headers=headers, timeout=10)
        except Exception as e:
            # Log do erro (implementar logging posteriormente)
            pass
    
    def _build_payload(self, instance, user=None):
        """Constrói o payload baseado no tipo de instância"""
        payload = {
            'event': self.event,
            'timestamp': timezone.now().isoformat(),
            'webhook_name': self.name
        }
        
        if hasattr(instance, 'id'):
            payload['object_id'] = instance.id
        
        if self.event.startswith('TICKET_'):
            payload['ticket'] = {
                'id': instance.id,
                'title': instance.title,
                'description': instance.description,
                'status': instance.status,
                'priority': instance.priority,
                'sector': instance.sector.name,
                'category': instance.category.name,
                'created_by': instance.created_by.full_name,
                'created_at': instance.created_at.isoformat(),
                'due_date': instance.due_date.isoformat() if instance.due_date else None,
                'is_overdue': instance.is_overdue
            }
        
        if user:
            payload['user'] = {
                'id': user.id,
                'full_name': user.full_name,
                'email': user.email
            }
        
        return payload
