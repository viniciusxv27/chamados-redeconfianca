from django.db import models
from django.conf import settings
from django.utils import timezone
from users.models import Sector, User
from core.utils import upload_ticket_attachment
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
        ('REJEITADO', 'Rejeitado'),
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
            
            # Verificar se é ordem de compra e iniciar fluxo de aprovação
            if self.category.name.lower() == 'ordem de compra':
                self._start_purchase_approval_flow()
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
            # Buscar usuários do setor onde o chamado foi aberto
            from users.models import User
            sector_users = []
            if self.sector:
                sector_users_qs = User.objects.filter(sector=self.sector).values(
                    'id', 'first_name', 'last_name', 'email', 'phone', 'hierarchy'
                )
                sector_users = [
                    {
                        'id': user['id'],
                        'name': f"{user['first_name']} {user['last_name']}".strip(),
                        'email': user['email'],
                        'phone': user['phone'] or '',
                        'hierarchy': user['hierarchy']
                    }
                    for user in sector_users_qs
                ]
            
            payload = {
                'event': 'ticket_created',
                'ticket': {
                    'id': self.id,
                    'title': self.title,
                    'description': self.description,
                    'sector': self.sector.name if self.sector else '',
                    'category': self.category.name,
                    'created_by': {
                        'id': self.created_by.id,
                        'name': self.created_by.get_full_name(),
                        'email': self.created_by.email,
                        'phone': getattr(self.created_by, 'phone', '') or '',
                        'hierarchy': self.created_by.hierarchy
                    },
                    'created_at': self.created_at.isoformat(),
                    'status': self.status
                },
                'sector_users': sector_users,
                'timestamp': timezone.now().isoformat()
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
    
    def _start_purchase_approval_flow(self):
        """Inicia o fluxo de aprovação para ordem de compra"""
        # Extrair valor do título ou descrição (assumindo formato "Valor: R$ XXX")
        import re
        
        # Tentar extrair valor do título ou descrição
        text_to_search = f"{self.title} {self.description}"
        value_match = re.search(r'R?\$?\s*(\d+(?:[.,]\d{2})?)', text_to_search)
        
        if not value_match:
            # Se não encontrar valor, assumir valor padrão para teste
            amount = 50.00
        else:
            amount_str = value_match.group(1).replace(',', '.')
            amount = float(amount_str)
        
        # Buscar primeiro aprovador ativo
        first_approver = PurchaseOrderApprover.objects.filter(
            approval_order=1,
            is_active=True
        ).first()
        
        if first_approver and amount <= first_approver.max_amount:
            # Criar primeira aprovação
            approval = PurchaseOrderApproval.objects.create(
                ticket=self,
                approver=first_approver.user,
                amount=amount,
                approval_step=1
            )
            
            # Disparar webhook de solicitação
            approval._trigger_approval_request_webhook()
        else:
            # Se valor exceder o máximo do primeiro aprovador, criar comentário
            TicketComment.objects.create(
                ticket=self,
                user=self.created_by,
                comment=f"Ordem de compra criada com valor R$ {amount:.2f}. Aguardando aprovação.",
                comment_type='COMMENT'
            )


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
        ('APPROVAL_REQUEST', 'Solicitação de Aprovação'),
        ('APPROVED', 'Aprovado'),
        ('REJECTED', 'Rejeitado'),
        ('COMMUNICATION_CREATED', 'Comunicado Criado'),
        ('COMMUNICATION_UPDATED', 'Comunicado Atualizado'),
    ]
    
    name = models.CharField(max_length=100, verbose_name="Nome")
    url = models.URLField(verbose_name="URL do Webhook")
    event = models.CharField(max_length=25, choices=EVENT_CHOICES, verbose_name="Evento")
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
    
    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.event:
            raise ValidationError({'event': 'O campo event é obrigatório.'})
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
    
    def trigger(self, instance, user=None):
        """Dispara o webhook com os dados da instância"""
        import requests
        
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
            
            response = requests.post(self.url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            
        except Exception as e:
            # Log do erro silenciosamente
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f'Erro no webhook {self.name}: {e}')
    
    def _send_webhook(self, payload):
        """Envia webhook com payload customizado"""
        try:
            if not self.is_active:
                return
            
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
            # Buscar usuários do setor onde o chamado foi aberto
            from users.models import User
            sector_users = []
            if instance.sector:
                sector_users_qs = User.objects.filter(sector=instance.sector).values(
                    'id', 'first_name', 'last_name', 'email', 'phone', 'hierarchy'
                )
                sector_users = [
                    {
                        'id': user['id'],
                        'name': f"{user['first_name']} {user['last_name']}".strip(),
                        'email': user['email'],
                        'phone': user['phone'] or '',
                        'hierarchy': user['hierarchy']
                    }
                    for user in sector_users_qs
                ]
            
            payload['ticket'] = {
                'id': instance.id,
                'title': instance.title,
                'description': instance.description,
                'status': instance.status,
                'priority': instance.priority,
                'sector': instance.sector.name if instance.sector else '',
                'category': instance.category.name,
                'created_by': {
                    'id': instance.created_by.id,
                    'name': instance.created_by.get_full_name(),
                    'email': instance.created_by.email,
                    'phone': getattr(instance.created_by, 'phone', '') or '',
                    'hierarchy': instance.created_by.hierarchy
                },
                'created_at': instance.created_at.isoformat(),
                'due_date': instance.due_date.isoformat() if instance.due_date else None,
                'is_overdue': instance.is_overdue
            }
            payload['sector_users'] = sector_users
        elif self.event.startswith('COMMUNICATION_'):
            # Buscar usuários que devem receber o comunicado
            from users.models import User
            recipients_data = []
            
            if instance.send_to_all:
                # Se é para todos, buscar todos os usuários ativos
                recipients_data = list(User.objects.filter(is_active=True).values(
                    'id', 'first_name', 'last_name', 'email', 'phone', 'hierarchy'
                ))
            else:
                # Se é para usuários específicos, buscar os recipients
                recipients_data = list(instance.recipients.values(
                    'id', 'first_name', 'last_name', 'email', 'phone', 'hierarchy'
                ))
            
            # Formatar dados dos recipients
            recipients_users = [
                {
                    'id': user['id'],
                    'name': f"{user['first_name']} {user['last_name']}".strip(),
                    'email': user['email'],
                    'phone': user['phone'] or '',
                    'hierarchy': user['hierarchy']
                }
                for user in recipients_data
            ]
            
            payload['communication'] = {
                'id': instance.id,
                'title': instance.title,
                'message': instance.message,
                'sender': {
                    'id': instance.sender.id,
                    'name': instance.sender.get_full_name(),
                    'email': instance.sender.email,
                    'phone': getattr(instance.sender, 'phone', '') or '',
                    'hierarchy': instance.sender.hierarchy
                },
                'send_to_all': instance.send_to_all,
                'is_pinned': instance.is_pinned,
                'is_popup': instance.is_popup,
                'created_at': instance.created_at.isoformat(),
                'active_from': instance.active_from.isoformat() if instance.active_from else None,
                'active_until': instance.active_until.isoformat() if instance.active_until else None,
                'recipients_count': len(recipients_users),
                'has_image': bool(instance.image)
            }
            payload['recipients_users'] = recipients_users
        
        if user:
            payload['user'] = {
                'id': user.id,
                'full_name': user.full_name,
                'email': user.email
            }
        
        return payload


class PurchaseOrderApprover(models.Model):
    """Configuração de aprovadores para ordem de compra"""
    
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='purchase_approval_config',
        verbose_name="Usuário"
    )
    max_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Valor Máximo de Aprovação"
    )
    approval_order = models.PositiveIntegerField(
        verbose_name="Ordem de Aprovação",
        help_text="1 = Primeiro aprovador, 2 = Segundo aprovador, etc."
    )
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    
    class Meta:
        verbose_name = "Aprovador de Ordem de Compra"
        verbose_name_plural = "Aprovadores de Ordem de Compra"
        ordering = ['approval_order']
        unique_together = ['approval_order']  # Cada ordem deve ser única
    
    def __str__(self):
        return f"{self.user.full_name} - Ordem {self.approval_order} - Até R$ {self.max_amount}"


class PurchaseOrderApproval(models.Model):
    """Log de aprovações de ordem de compra"""
    
    STATUS_CHOICES = [
        ('PENDING', 'Pendente'),
        ('APPROVED', 'Aprovado'),
        ('REJECTED', 'Rejeitado'),
    ]
    
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='purchase_approvals')
    approver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name="Aprovador"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor")
    
    # Comentários e timestamps
    comment = models.TextField(blank=True, verbose_name="Comentário")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Solicitado em")
    decided_at = models.DateTimeField(null=True, blank=True, verbose_name="Decidido em")
    
    # Ordem de aprovação no fluxo
    approval_step = models.PositiveIntegerField(verbose_name="Etapa de Aprovação")
    
    class Meta:
        verbose_name = "Aprovação de Ordem de Compra"
        verbose_name_plural = "Aprovações de Ordem de Compra"
        ordering = ['-created_at']
        unique_together = ['ticket', 'approver', 'approval_step']
    
    def __str__(self):
        return f"Aprovação {self.ticket.id} - {self.approver.full_name} - {self.status}"
    
    def approve(self, comment=''):
        """Aprova a ordem de compra"""
        from django.utils import timezone
        self.status = 'APPROVED'
        self.comment = comment
        self.decided_at = timezone.now()
        self.save()
        
        # Verificar se precisa seguir para o próximo aprovador
        self._process_next_approval()
    
    def reject(self, comment=''):
        """Rejeita a ordem de compra"""
        from django.utils import timezone
        self.status = 'REJECTED'
        self.comment = comment
        self.decided_at = timezone.now()
        self.save()
        
        # Disparar webhook de rejeição
        self._trigger_rejection_webhook()
    
    def _process_next_approval(self):
        """Processa a próxima aprovação no fluxo"""
        # Buscar próximo aprovador
        next_approver = PurchaseOrderApprover.objects.filter(
            approval_order=self.approval_step + 1,
            is_active=True
        ).first()
        
        if next_approver and self.amount <= next_approver.max_amount:
            # Criar próxima aprovação
            next_approval = PurchaseOrderApproval.objects.create(
                ticket=self.ticket,
                approver=next_approver.user,
                amount=self.amount,
                approval_step=next_approver.approval_order
            )
            
            # Disparar webhook de solicitação
            next_approval._trigger_approval_request_webhook()
        else:
            # Todos aprovaram - disparar webhook final
            self._trigger_final_approval_webhook()
    
    def _trigger_approval_request_webhook(self):
        """Dispara webhook de solicitação de aprovação"""
        webhooks = Webhook.objects.filter(event='APPROVAL_REQUEST', is_active=True)
        
        for webhook in webhooks:
            payload = {
                'event': 'approval_request',
                'purchase_approval': {
                    'id': self.id,
                    'ticket_id': self.ticket.id,
                    'amount': float(self.amount),
                    'approval_step': self.approval_step,
                    'status': self.status,
                    'created_at': self.created_at.isoformat(),
                    'callback_url': f"/api/purchase-orders/{self.ticket.id}/approve/{self.id}/"
                },
                'approver_user': {
                    'id': self.approver.id,
                    'name': self.approver.get_full_name(),
                    'email': self.approver.email,
                    'phone': getattr(self.approver, 'phone', '') or '',
                    'hierarchy': self.approver.hierarchy
                },
                'timestamp': timezone.now().isoformat()
            }
            webhook._send_webhook(payload)
    
    def _trigger_final_approval_webhook(self):
        """Dispara webhook de aprovação final"""
        webhooks = Webhook.objects.filter(event='APPROVED', is_active=True)
        
        # Buscar todos os aprovadores que participaram do processo
        all_approvals = PurchaseOrderApproval.objects.filter(
            ticket=self.ticket,
            status='APPROVED'
        ).select_related('approver')
        
        approvers_data = [
            {
                'id': approval.approver.id,
                'name': approval.approver.get_full_name(),
                'email': approval.approver.email,
                'phone': getattr(approval.approver, 'phone', '') or '',
                'hierarchy': approval.approver.hierarchy,
                'approval_step': approval.approval_step,
                'decided_at': approval.decided_at.isoformat() if approval.decided_at else None,
                'comment': approval.comment
            }
            for approval in all_approvals
        ]
        
        for webhook in webhooks:
            payload = {
                'event': 'approved',
                'purchase_approval': {
                    'ticket_id': self.ticket.id,
                    'amount': float(self.amount),
                    'final_status': 'APPROVED',
                    'completed_at': timezone.now().isoformat()
                },
                'approvers_users': approvers_data,
                'timestamp': timezone.now().isoformat()
            }
            webhook._send_webhook(payload)
    
    def _trigger_rejection_webhook(self):
        """Dispara webhook de rejeição"""
        webhooks = Webhook.objects.filter(event='REJECTED', is_active=True)
        
        for webhook in webhooks:
            payload = {
                'event': 'rejected',
                'purchase_approval': {
                    'id': self.id,
                    'ticket_id': self.ticket.id,
                    'amount': float(self.amount),
                    'approval_step': self.approval_step,
                    'status': self.status,
                    'decided_at': self.decided_at.isoformat() if self.decided_at else None,
                    'comment': self.comment
                },
                'rejector_user': {
                    'id': self.approver.id,
                    'name': self.approver.get_full_name(),
                    'email': self.approver.email,
                    'phone': getattr(self.approver, 'phone', '') or '',
                    'hierarchy': self.approver.hierarchy
                },
                'timestamp': timezone.now().isoformat()
            }
            webhook._send_webhook(payload)


def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


class TicketAttachment(models.Model):
    """Modelo para anexos de tickets"""
    
    ticket = models.ForeignKey(
        Ticket, 
        on_delete=models.CASCADE, 
        related_name='attachments',
        verbose_name="Chamado"
    )
    file = models.FileField(
        upload_to=upload_ticket_attachment, 
        storage=get_media_storage(),
        verbose_name="Arquivo"
    )
    original_filename = models.CharField(
        max_length=255, 
        verbose_name="Nome Original do Arquivo"
    )
    file_size = models.PositiveIntegerField(
        verbose_name="Tamanho do Arquivo (bytes)"
    )
    content_type = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Tipo de Conteúdo"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="Enviado por"
    )
    uploaded_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Data do Upload"
    )
    
    class Meta:
        verbose_name = "Anexo do Chamado"
        verbose_name_plural = "Anexos dos Chamados"
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"#{self.ticket.id} - {self.original_filename}"
    
    @property
    def file_extension(self):
        """Retorna a extensão do arquivo"""
        import os
        return os.path.splitext(self.original_filename)[1].lower()
    
    @property
    def is_image(self):
        """Verifica se é uma imagem"""
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        return self.file_extension in image_extensions
    
    @property
    def file_size_formatted(self):
        """Retorna o tamanho do arquivo formatado"""
        if self.file_size < 1024:
            return f"{self.file_size} bytes"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        else:
            return f"{self.file_size / (1024 * 1024):.1f} MB"
