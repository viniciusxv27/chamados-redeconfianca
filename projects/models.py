from django.db import models
from django.conf import settings
from django.utils import timezone
from users.models import Sector, User
from decimal import Decimal


def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


def upload_project_attachment(instance, filename):
    """Function to generate upload path for project attachments"""
    from core.utils import sanitize_filename
    safe_filename = sanitize_filename(filename)
    return f'projects/{instance.project.id}/attachments/{safe_filename}'


class ProjectSectorAccess(models.Model):
    """Modelo para controlar quais setores podem ver o menu de projetos"""
    
    sector = models.OneToOneField(
        Sector,
        on_delete=models.CASCADE,
        verbose_name="Setor",
        related_name="project_access"
    )
    can_view_projects = models.BooleanField(
        default=False,
        verbose_name="Pode ver projetos"
    )
    can_create_projects = models.BooleanField(
        default=False,
        verbose_name="Pode criar projetos"
    )
    can_manage_all_projects = models.BooleanField(
        default=False,
        verbose_name="Pode gerenciar todos os projetos"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Acesso de Setor a Projetos"
        verbose_name_plural = "Acessos de Setores a Projetos"
    
    def __str__(self):
        return f"{self.sector.name} - Projetos"


class Project(models.Model):
    """Modelo principal para projetos"""
    
    STATUS_CHOICES = [
        ('STANDBY', 'Stand By'),
        ('EM_ANDAMENTO', 'Em Andamento'),
        ('EM_TESTE', 'Em Teste'),
        ('CONCLUIDO', 'Concluído'),
        ('CANCELADO', 'Cancelado'),
    ]
    
    PRIORITY_CHOICES = [
        ('BAIXA', 'Baixa'),
        ('MEDIA', 'Média'),
        ('ALTA', 'Alta'),
        ('CRITICA', 'Crítica'),
    ]
    
    name = models.CharField(
        max_length=200,
        verbose_name="Nome do Projeto"
    )
    description = models.TextField(
        verbose_name="Descrição"
    )
    scope = models.TextField(
        verbose_name="Escopo Total",
        help_text="Descrição completa do escopo do projeto"
    )
    reason = models.TextField(
        verbose_name="Motivo",
        help_text="Justificativa para o projeto"
    )
    
    # Datas
    start_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Data de Início"
    )
    deadline = models.DateField(
        verbose_name="Prazo"
    )
    completion_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Data de Conclusão"
    )
    
    # Status e prioridade
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='STANDBY',
        verbose_name="Status"
    )
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default='MEDIA',
        verbose_name="Prioridade"
    )
    
    # Progresso
    progress_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name="Progresso (%)",
        help_text="Porcentagem de conclusão do projeto"
    )
    
    # Usuários
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_projects',
        verbose_name="Criado por"
    )
    responsible_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='responsible_projects',
        verbose_name="Responsável Principal"
    )
    
    # Controle de acesso
    sector = models.ForeignKey(
        Sector,
        on_delete=models.CASCADE,
        verbose_name="Setor Responsável"
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Projeto"
        verbose_name_plural = "Projetos"
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    @property
    def is_overdue(self):
        """Verifica se o projeto está atrasado"""
        if self.status in ['CONCLUIDO', 'CANCELADO']:
            return False
        return timezone.now().date() > self.deadline
    
    @property
    def status_class(self):
        """Retorna classe CSS baseada no status"""
        classes = {
            'STANDBY': 'bg-yellow-100 text-yellow-800',
            'EM_ANDAMENTO': 'bg-blue-100 text-blue-800',
            'EM_TESTE': 'bg-purple-100 text-purple-800',
            'CONCLUIDO': 'bg-green-100 text-green-800',
            'CANCELADO': 'bg-red-100 text-red-800',
        }
        return classes.get(self.status, 'bg-gray-100 text-gray-800')
    
    @property
    def deadline_status(self):
        """Retorna o status do prazo (dentro do prazo ou atrasado)"""
        if self.status in ['CONCLUIDO', 'CANCELADO']:
            if self.completion_date and self.completion_date <= self.deadline:
                return 'DENTRO_DO_PRAZO'
            else:
                return 'ATRASADO'
        
        # Para projetos em andamento
        if self.is_overdue:
            return 'ATRASADO'
        return 'DENTRO_DO_PRAZO'
    
    def update_progress(self):
        """Atualiza o progresso baseado nas atividades"""
        activities = self.activities.all()
        if not activities.exists():
            self.progress_percentage = Decimal('0.00')
        else:
            total_weight = activities.count()
            completed_weight = activities.filter(status='CONCLUIDA').count()
            self.progress_percentage = Decimal(str((completed_weight / total_weight) * 100))
        
        self.save()


class ProjectAttachment(models.Model):
    """Modelo para anexos de projetos"""
    
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='attachments',
        verbose_name="Projeto"
    )
    file = models.FileField(
        upload_to=upload_project_attachment,
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
        verbose_name = "Anexo do Projeto"
        verbose_name_plural = "Anexos dos Projetos"
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f"{self.project.name} - {self.original_filename}"
    
    @property
    def file_size_formatted(self):
        """Retorna o tamanho do arquivo formatado"""
        if self.file_size < 1024:
            return f"{self.file_size} bytes"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        else:
            return f"{self.file_size / (1024 * 1024):.1f} MB"


class Activity(models.Model):
    """Modelo para atividades dos projetos"""
    
    STATUS_CHOICES = [
        ('NAO_INICIADA', 'Não Iniciada'),
        ('EM_ANDAMENTO', 'Em Andamento'),
        ('CONCLUIDA', 'Concluída'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    PRIORITY_CHOICES = [
        ('BAIXA', 'Baixa'),
        ('MEDIA', 'Média'),
        ('ALTA', 'Alta'),
        ('CRITICA', 'Crítica'),
    ]
    
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name='activities',
        verbose_name="Projeto"
    )
    parent_activity = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='sub_activities',
        verbose_name="Atividade Pai"
    )
    
    name = models.CharField(
        max_length=200,
        verbose_name="Nome da Atividade"
    )
    description = models.TextField(
        verbose_name="Descrição"
    )
    
    # Datas
    start_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Data de Início"
    )
    deadline = models.DateField(
        verbose_name="Prazo"
    )
    completion_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Data de Conclusão"
    )
    
    # Status e prioridade
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default='NAO_INICIADA',
        verbose_name="Status"
    )
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default='MEDIA',
        verbose_name="Prioridade"
    )
    
    # Responsável
    responsible_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='responsible_activities',
        verbose_name="Responsável"
    )
    
    # Categoria para organização no Kanban
    category = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Categoria",
        help_text="Categoria para organização no Kanban"
    )
    
    # Ordem de exibição
    order = models.PositiveIntegerField(
        default=0,
        verbose_name="Ordem"
    )
    
    # Metadados
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_activities',
        verbose_name="Criado por"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Atividade"
        verbose_name_plural = "Atividades"
        ordering = ['order', 'deadline']
    
    def __str__(self):
        if self.parent_activity:
            return f"{self.project.name} > {self.parent_activity.name} > {self.name}"
        return f"{self.project.name} > {self.name}"
    
    @property
    def hierarchy_level(self):
        """Retorna o nível hierárquico da atividade (0 = raiz, 1 = sub-atividade, etc.)"""
        if not self.parent_activity:
            return 0
        return self.parent_activity.hierarchy_level + 1
    
    @property
    def hierarchy_path(self):
        """Retorna o caminho hierárquico completo como lista"""
        path = []
        current = self
        while current:
            path.insert(0, current.name)
            current = current.parent_activity
        return path
    
    @property
    def hierarchy_display(self):
        """Retorna uma string formatada para exibição da hierarquia"""
        level = self.hierarchy_level
        prefix = "└── " if level > 0 else ""
        indent = "    " * max(0, level - 1)
        return f"{indent}{prefix}{self.name}"
    
    def get_all_children(self):
        """Retorna todas as sub-atividades recursivamente"""
        children = []
        for child in self.sub_activities.all():
            children.append(child)
            children.extend(child.get_all_children())
        return children
    
    def get_root_activity(self):
        """Retorna a atividade raiz desta hierarquia"""
        if not self.parent_activity:
            return self
        return self.parent_activity.get_root_activity()
    
    def can_have_children(self):
        """Verifica se pode ter sub-atividades (máximo 3 níveis)"""
        return self.hierarchy_level < 2
    
    @property
    def is_overdue(self):
        """Verifica se a atividade está atrasada"""
        if self.status in ['CONCLUIDA', 'CANCELADA']:
            return False
        return timezone.now().date() > self.deadline
    
    @property
    def level(self):
        """Retorna o nível hierárquico da atividade"""
        if not self.parent_activity:
            return 0
        return self.parent_activity.level + 1
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Atualizar progresso do projeto quando a atividade for salva
        self.project.update_progress()


class ActivityComment(models.Model):
    """Modelo para comentários em atividades"""
    
    activity = models.ForeignKey(
        Activity,
        on_delete=models.CASCADE,
        related_name='comments',
        verbose_name="Atividade"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="Usuário"
    )
    content = models.TextField(
        verbose_name="Comentário"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Comentário da Atividade"
        verbose_name_plural = "Comentários das Atividades"
        ordering = ['created_at']
    
    def __str__(self):
        return f"{self.activity.name} - {self.user.full_name}"


# Modelos de Chat importados
from .models_chat import TaskChat, TaskChatMessage, SupportChat, SupportChatMessage, SupportAgent
