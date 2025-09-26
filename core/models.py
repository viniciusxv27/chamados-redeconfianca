from django.db import models
from django.conf import settings
from django.utils import timezone
from core.utils import upload_tutorial_pdf, upload_report_evidence

User = settings.AUTH_USER_MODEL

def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


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
        ('PRIZE_CATEGORY_CREATE', 'Criação de Categoria de Prêmio'),
        ('PRIZE_CATEGORY_UPDATE', 'Atualização de Categoria de Prêmio'),
        ('PRIZE_CATEGORY_DELETE', 'Exclusão de Categoria de Prêmio'),
        ('SECTOR_CREATE', 'Criação de Setor'),
        ('SECTOR_EDIT', 'Edição de Setor'),
        ('SECTOR_DELETE', 'Exclusão de Setor'),
        ('CATEGORY_CREATE', 'Criação de Categoria'),
        ('CATEGORY_UPDATE', 'Atualização de Categoria'),
        ('CATEGORY_DELETE', 'Exclusão de Categoria'),
        ('WEBHOOK_CREATE', 'Criação de Webhook'),
        ('WEBHOOK_UPDATE', 'Atualização de Webhook'),
        ('WEBHOOK_DELETE', 'Exclusão de Webhook'),
        ('REPORT_CREATE', 'Criação de Denúncia'),
        ('REPORT_UPDATE', 'Atualização de Denúncia'),
        ('REPORT_COMMENT', 'Comentário em Denúncia'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Usuário"
    )
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES, verbose_name="Tipo de Ação")
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


class TrainingCategory(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nome")
    description = models.TextField(blank=True, verbose_name="Descrição")
    color = models.CharField(max_length=7, default="#3B82F6", verbose_name="Cor")  # Hex color
    icon = models.CharField(max_length=50, default="fas fa-graduation-cap", verbose_name="Ícone")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Categoria de Treinamento"
        verbose_name_plural = "Categorias de Treinamento"
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Tutorial(models.Model):
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(verbose_name="Descrição")
    category = models.ForeignKey(TrainingCategory, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Categoria")
    pdf_file = models.FileField(upload_to=upload_tutorial_pdf, storage=get_media_storage(), verbose_name="Arquivo PDF")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        verbose_name="Criado por"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    order = models.IntegerField(default=0, verbose_name="Ordem")

    class Meta:
        verbose_name = "Tutorial"
        verbose_name_plural = "Tutoriais"
        ordering = ['order', 'title']

    def __str__(self):
        return self.title
    
    def get_progress_for_user(self, user):
        """Retorna o progresso do usuário para este tutorial"""
        try:
            return self.user_progress.get(user=user)
        except TutorialProgress.DoesNotExist:
            return None
    
    def get_viewers_count(self):
        """Retorna quantos usuários visualizaram este tutorial"""
        return self.user_progress.filter(viewed_at__isnull=False).count()
    
    def get_completed_count(self):
        """Retorna quantos usuários completaram este tutorial"""
        return self.user_progress.filter(completed_at__isnull=False).count()
    
    def get_all_viewers(self):
        """Retorna todos os usuários que visualizaram este tutorial"""
        return User.objects.filter(
            id__in=self.user_progress.filter(viewed_at__isnull=False).values_list('user_id', flat=True)
        )
    
    def get_all_completed(self):
        """Retorna todos os usuários que completaram este tutorial"""
        return User.objects.filter(
            id__in=self.user_progress.filter(completed_at__isnull=False).values_list('user_id', flat=True)
        )


class TutorialProgress(models.Model):
    """Progresso do usuário em um tutorial"""
    tutorial = models.ForeignKey(Tutorial, on_delete=models.CASCADE, related_name='user_progress')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tutorial_progress')
    viewed_at = models.DateTimeField(null=True, blank=True, verbose_name="Visualizado em")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Concluído em")
    
    class Meta:
        verbose_name = "Progresso do Tutorial"
        verbose_name_plural = "Progresso dos Tutoriais"
        unique_together = ['tutorial', 'user']
        ordering = ['-viewed_at']
    
    def __str__(self):
        status = "Concluído" if self.completed_at else ("Visualizado" if self.viewed_at else "Não iniciado")
        return f"{self.user.full_name} - {self.tutorial.title} ({status})"
    
    def mark_as_viewed(self):
        """Marca como visualizado se ainda não foi"""
        if not self.viewed_at:
            self.viewed_at = timezone.now()
            self.save()
    
    def mark_as_completed(self):
        """Marca como concluído"""
        now = timezone.now()
        if not self.viewed_at:
            self.viewed_at = now
        if not self.completed_at:
            self.completed_at = now
            self.save()


class Report(models.Model):
    REPORT_TYPES = [
        ('HARASSMENT', 'Assédio/Bullying'),
        ('INAPPROPRIATE_CONTENT', 'Conteúdo Inadequado'),
        ('SPAM', 'Spam'),
        ('FAKE_INFO', 'Informação Falsa'),
        ('DISCRIMINATION', 'Discriminação'),
        ('VIOLENCE', 'Violência/Ameaças'),
        ('OTHER', 'Outro'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pendente'),
        ('UNDER_REVIEW', 'Em Análise'),
        ('RESOLVED', 'Resolvida'),
        ('DISMISSED', 'Descartada'),
    ]
    
    PRIORITY_CHOICES = [
        ('LOW', 'Baixa'),
        ('MEDIUM', 'Média'),
        ('HIGH', 'Alta'),
        ('CRITICAL', 'Crítica'),
    ]
    
    # Quem está fazendo a denúncia
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reports_made', verbose_name="Denunciante")
    
    # Contra quem é a denúncia (opcional)
    reported_user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='reports_received', verbose_name="Usuário Denunciado")
    
    # Detalhes da denúncia
    report_type = models.CharField(max_length=30, choices=REPORT_TYPES, verbose_name="Tipo de Denúncia")
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(verbose_name="Descrição")
    evidence = models.FileField(upload_to=upload_report_evidence, storage=get_media_storage(), blank=True, null=True, verbose_name="Evidência")
    
    # Metadados
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', verbose_name="Status")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM', verbose_name="Prioridade")
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")
    
    # Quem está analisando
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_reports', verbose_name="Responsável")
    
    # Resposta/Resolução
    admin_notes = models.TextField(blank=True, verbose_name="Notas do Administrador")
    resolution = models.TextField(blank=True, verbose_name="Resolução")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Resolvido em")
    
    # Informações adicionais
    is_anonymous = models.BooleanField(default=False, verbose_name="Denúncia Anônima")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP Address")
    
    class Meta:
        verbose_name = "Denúncia"
        verbose_name_plural = "Denúncias"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['reporter', 'created_at']),
            models.Index(fields=['reported_user', 'created_at']),
        ]
    
    def __str__(self):
        target = f" contra {self.reported_user.full_name}" if self.reported_user else ""
        return f"{self.get_report_type_display()}{target} - {self.reporter.full_name}"
    
    def save(self, *args, **kwargs):
        if self.status == 'RESOLVED' and not self.resolved_at:
            self.resolved_at = timezone.now()
        super().save(*args, **kwargs)
    
    @property
    def is_urgent(self):
        return self.priority in ['HIGH', 'CRITICAL']
    
    def can_be_viewed_by(self, user):
        """Verifica se o usuário pode ver esta denúncia"""
        # Superadmins podem ver tudo
        if user.hierarchy == 'SUPERADMIN':
            return True
        
        # O denunciante pode ver sua própria denúncia
        if user == self.reporter:
            return True
        
        # Usuário designado para resolver pode ver
        if user == self.assigned_to:
            return True
            
        return False
    
    def can_be_managed_by(self, user):
        """Verifica se o usuário pode gerenciar esta denúncia"""
        return user.hierarchy in ['SUPERADMIN', 'ADMINISTRATIVO']


class ReportComment(models.Model):
    """Comentários em denúncias"""
    report = models.ForeignKey(Report, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    comment = models.TextField(verbose_name="Comentário")
    created_at = models.DateTimeField(auto_now_add=True)
    is_internal = models.BooleanField(default=False, verbose_name="Comentário Interno")  # Só para admins
    
    class Meta:
        verbose_name = "Comentário da Denúncia"
        verbose_name_plural = "Comentários das Denúncias"
        ordering = ['created_at']
    
    def __str__(self):
        return f"Comentário de {self.user.full_name} em {self.report}"


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
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save()


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


# ===== CHECKLIST MODELS =====
class ChecklistTemplate(models.Model):
    """Template de checklist padrão que será aplicado diariamente"""
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(blank=True, verbose_name="Descrição")
    is_default_daily = models.BooleanField(default=False, verbose_name="Checklist Padrão Diário")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_checklist_templates')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Template de Checklist"
        verbose_name_plural = "Templates de Checklist"
        ordering = ['title']
    
    def __str__(self):
        return self.title


class ChecklistTemplateItem(models.Model):
    """Itens do template de checklist"""
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.CASCADE, related_name='items')
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(blank=True, verbose_name="Descrição")
    order = models.IntegerField(default=0, verbose_name="Ordem")
    is_required = models.BooleanField(default=True, verbose_name="Obrigatório")
    
    class Meta:
        verbose_name = "Item do Template"
        verbose_name_plural = "Itens do Template"
        ordering = ['order', 'title']
    
    def __str__(self):
        return f"{self.template.title} - {self.title}"


class DailyChecklist(models.Model):
    """Checklist diário para um usuário específico"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_checklists')
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.CASCADE, related_name='daily_checklists', verbose_name="Template", null=True, blank=True)
    date = models.DateField(verbose_name="Data")
    title = models.CharField(max_length=200, verbose_name="Título")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_checklists')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Concluído em")
    repeat_daily = models.BooleanField(default=False, verbose_name="Repetir todos os dias")
    
    class Meta:
        verbose_name = "Checklist Diário"
        verbose_name_plural = "Checklists Diários"
        unique_together = ['user', 'title', 'date']
        ordering = ['-date', '-created_at']
    
    def __str__(self):
        template_title = self.template.title if self.template else self.title
        return f"{self.user.get_full_name() or self.user.username} - {template_title} ({self.date})"
    
    def get_completion_percentage(self):
        """Calcula a porcentagem de conclusão do checklist"""
        total_items = self.items.count()
        if total_items == 0:
            return 0
        completed_items = self.items.filter(status='DONE').count()
        return round((completed_items / total_items) * 100)
    
    def is_fully_completed(self):
        """Verifica se todos os itens obrigatórios foram concluídos"""
        required_items = self.items.filter(is_required=True)
        completed_required = required_items.filter(status='DONE')
        return required_items.count() == completed_required.count()
    
    def mark_as_completed(self):
        """Marca o checklist como concluído se todos os itens obrigatórios estiverem feitos"""
        if self.is_fully_completed() and not self.completed_at:
            self.completed_at = timezone.now()
            self.save()


class ChecklistItem(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pendente'),
        ('DOING', 'Fazendo'),
        ('DONE', 'Feito'),
    ]
    
    checklist = models.ForeignKey(DailyChecklist, on_delete=models.CASCADE, related_name='items')
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(blank=True, verbose_name="Descrição")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', verbose_name="Status")
    is_required = models.BooleanField(default=True, verbose_name="Obrigatório")
    order = models.IntegerField(default=0, verbose_name="Ordem")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Concluído em")
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Item do Checklist"
        verbose_name_plural = "Itens do Checklist"
        ordering = ['order', 'title']
    
    def __str__(self):
        return f"{self.checklist.user.full_name} - {self.title} ({self.status})"
    
    def save(self, *args, **kwargs):
        # Atualizar timestamp quando marcado como concluído
        if self.status == 'DONE' and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status != 'DONE':
            self.completed_at = None
        
        super().save(*args, **kwargs)
        
        # Verificar se o checklist pai pode ser marcado como concluído
        self.checklist.mark_as_completed()


# ===== ATIVIDADES MODELS =====
class TaskActivity(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pendente'),
        ('DOING', 'Fazendo'),
        ('DONE', 'Feito'),
    ]
    
    PRIORITY_CHOICES = [
        ('LOW', 'Baixa'),
        ('MEDIUM', 'Média'),
        ('HIGH', 'Alta'),
        ('URGENT', 'Urgente'),
    ]
    
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(verbose_name="Descrição")
    assigned_to = models.ForeignKey(User, on_delete=models.CASCADE, related_name='task_activities')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_task_activities')
    
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM', verbose_name="Prioridade")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING', verbose_name="Status")
    
    due_date = models.DateTimeField(verbose_name="Prazo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Concluído em")
    
    # Campos opcionais
    estimated_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Horas Estimadas")
    actual_hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, verbose_name="Horas Reais")
    
    class Meta:
        verbose_name = "Tarefa"
        verbose_name_plural = "Tarefas"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['due_date', 'status']),
            models.Index(fields=['created_by', 'created_at']),
        ]
    
    def __str__(self):
        return f"{self.title} - {self.assigned_to.full_name}"
    
    def save(self, *args, **kwargs):
        # Atualizar timestamp quando marcado como concluído
        if self.status == 'DONE' and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status != 'DONE':
            self.completed_at = None
        
        super().save(*args, **kwargs)
    
    @property
    def is_overdue(self):
        """Verifica se a atividade está em atraso"""
        if self.status == 'DONE':
            return False
        return timezone.now() > self.due_date
    
    @property
    def priority_color(self):
        """Retorna a cor baseada na prioridade"""
        colors = {
            'LOW': 'green',
            'MEDIUM': 'yellow',
            'HIGH': 'orange',
            'URGENT': 'red',
        }
        return colors.get(self.priority, 'gray')
    
    def can_be_managed_by(self, user):
        """Verifica se o usuário pode gerenciar esta atividade"""
        # O criador pode gerenciar
        if user == self.created_by:
            return True
        
        # Superadmin pode gerenciar tudo
        if user.hierarchy == 'SUPERADMIN':
            return True
            
        # Supervisores podem gerenciar atividades dos seus setores
        if user.hierarchy in ['SUPERVISOR', 'ADMINISTRATIVO']:
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            assigned_sectors = list(self.assigned_to.sectors.all())
            if self.assigned_to.sector:
                assigned_sectors.append(self.assigned_to.sector)
            return bool(set(user_sectors) & set(assigned_sectors))
        
        return False
    
    def can_be_viewed_by(self, user):
        """Verifica se o usuário pode visualizar esta atividade"""
        # A pessoa designada pode ver
        if user == self.assigned_to:
            return True
        
        # Se pode gerenciar, pode visualizar
        return self.can_be_managed_by(user)
