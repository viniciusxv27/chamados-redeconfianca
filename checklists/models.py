from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from users.models import User, Sector
from django.utils import timezone
from django.conf import settings
import json


def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


def upload_checklist_evidence_image(instance, filename):
    """Define o caminho de upload para imagens de evidência de checklist"""
    import os
    from datetime import datetime
    
    # Pegar extensão do arquivo
    ext = filename.split('.')[-1]
    # Criar nome único baseado em timestamp e ID da execução
    new_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{instance.execution.id}_{instance.task.id}.{ext}"
    
    return os.path.join('checklists', 'evidences', 'images', new_filename)


def upload_checklist_evidence_video(instance, filename):
    """Define o caminho de upload para vídeos de evidência de checklist"""
    import os
    from datetime import datetime
    
    # Pegar extensão do arquivo
    ext = filename.split('.')[-1]
    # Criar nome único baseado em timestamp e ID da execução
    new_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{instance.execution.id}_{instance.task.id}.{ext}"
    
    return os.path.join('checklists', 'evidences', 'videos', new_filename)


class ChecklistTemplate(models.Model):
    """Template para criação de checklists"""
    
    name = models.CharField(max_length=200, verbose_name='Nome do Checklist')
    description = models.TextField(blank=True, verbose_name='Descrição')
    
    # Quem pode usar este template
    sector = models.ForeignKey(
        Sector, 
        on_delete=models.CASCADE, 
        verbose_name='Setor',
        help_text='Setor que pode usar este checklist'
    )
    
    # Metadados
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name='Criado por')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    
    class Meta:
        verbose_name = 'Template de Checklist'
        verbose_name_plural = 'Templates de Checklist'
        ordering = ['name']
        
    def __str__(self):
        return f'{self.name} - {self.sector.name}'


class ChecklistTask(models.Model):
    """Tarefas do template de checklist"""
    
    TASK_TYPE_CHOICES = [
        ('normal', 'Tarefa Normal'),
        ('yes_no', 'Pergunta Sim/Não'),
        ('dropdown', 'Menu Suspenso (Sim/Não/Não se Aplica)'),
    ]
    
    template = models.ForeignKey(
        ChecklistTemplate, 
        on_delete=models.CASCADE, 
        related_name='tasks',
        verbose_name='Template'
    )
    
    title = models.CharField(max_length=200, verbose_name='Título da Tarefa')
    description = models.TextField(blank=True, verbose_name='Descrição')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    is_required = models.BooleanField(default=True, verbose_name='Obrigatória')
    
    # Novos campos
    task_type = models.CharField(
        max_length=10,
        choices=TASK_TYPE_CHOICES,
        default='normal',
        verbose_name='Tipo de Tarefa',
        help_text='Tipo de tarefa: normal (checkbox) ou pergunta sim/não'
    )
    points = models.PositiveIntegerField(
        default=0,
        verbose_name='Pontos',
        help_text='Quantidade de pontos que esta tarefa vale'
    )
    
    # Mídia de instrução
    instruction_image = models.ImageField(
        upload_to='checklists/instructions/images/',
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Imagem de Instrução',
        help_text='Imagem explicativa de como executar a tarefa'
    )
    instruction_video = models.FileField(
        upload_to='checklists/instructions/videos/',
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Vídeo de Instrução',
        help_text='Vídeo explicativo de como executar a tarefa (MP4, AVI, MOV)'
    )
    instruction_document = models.FileField(
        upload_to='checklists/instructions/documents/',
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Documento de Instrução',
        help_text='Documento de apoio (PDF, DOC, XLS, PPT, etc.)'
    )
    
    class Meta:
        verbose_name = 'Tarefa de Checklist'
        verbose_name_plural = 'Tarefas de Checklist'
        ordering = ['order', 'id']
        
    def __str__(self):
        return f'{self.template.name} - {self.title}'
    
    def has_instruction_media(self):
        """Verifica se a tarefa possui mídia de instrução"""
        return bool(self.instruction_image or self.instruction_video or self.instruction_document) or self.instruction_media.exists()


class ChecklistTaskInstructionMedia(models.Model):
    """Múltiplos arquivos de instrução para uma tarefa"""
    
    MEDIA_TYPE_CHOICES = [
        ('image', 'Imagem'),
        ('video', 'Vídeo'),
        ('document', 'Documento'),
    ]
    
    task = models.ForeignKey(
        ChecklistTask,
        on_delete=models.CASCADE,
        related_name='instruction_media',
        verbose_name='Tarefa'
    )
    
    media_type = models.CharField(
        max_length=10,
        choices=MEDIA_TYPE_CHOICES,
        verbose_name='Tipo de Mídia'
    )
    
    file = models.FileField(
        upload_to='checklists/instructions/',
        storage=get_media_storage(),
        verbose_name='Arquivo'
    )
    
    title = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Título/Descrição'
    )
    
    order = models.PositiveIntegerField(
        default=0,
        verbose_name='Ordem'
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Criado em'
    )
    
    class Meta:
        verbose_name = 'Mídia de Instrução'
        verbose_name_plural = 'Mídias de Instrução'
        ordering = ['order', 'created_at']
    
    def __str__(self):
        return f'{self.get_media_type_display()} - {self.task.title}'


class ChecklistAssignment(models.Model):
    """Atribuição de checklist para um usuário específico"""
    
    SCHEDULE_CHOICES = [
        ('this_week', 'Esta Semana'),
        ('weekdays_month', 'Dias Úteis do Mês'),
        ('weekends_month', 'Fins de Semana do Mês'),
        ('daily', 'Todos os Dias'),
        ('custom', 'Datas Personalizadas'),
    ]
    
    PERIOD_CHOICES = [
        ('morning', 'Manhã'),
        ('afternoon', 'Tarde'),
        ('both', 'Manhã e Tarde'),
    ]
    
    template = models.ForeignKey(
        ChecklistTemplate, 
        on_delete=models.CASCADE, 
        verbose_name='Template'
    )
    
    # Destinatário
    assigned_to = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='checklist_assignments',
        verbose_name='Atribuído para'
    )
    
    # Quem atribuiu
    assigned_by = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='checklist_assignments_created',
        verbose_name='Atribuído por'
    )
    
    # Agendamento
    schedule_type = models.CharField(
        max_length=20, 
        choices=SCHEDULE_CHOICES,
        default='custom',
        verbose_name='Tipo de Agendamento'
    )
    
    # Período do dia
    period = models.CharField(
        max_length=10,
        choices=PERIOD_CHOICES,
        default='both',
        verbose_name='Período',
        help_text='Período do dia para execução do checklist'
    )
    
    # Datas personalizadas (JSON)
    custom_dates = models.JSONField(
        default=list,
        blank=True,
        verbose_name='Datas Personalizadas',
        help_text='Lista de datas no formato YYYY-MM-DD'
    )
    
    # Período para tipos predefinidos
    start_date = models.DateField(verbose_name='Data de Início')
    end_date = models.DateField(verbose_name='Data de Fim')
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    
    class Meta:
        verbose_name = 'Atribuição de Checklist'
        verbose_name_plural = 'Atribuições de Checklist'
        ordering = ['-created_at']
        
    def __str__(self):
        period_display = self.get_period_display()
        return f'{self.template.name} ({period_display}) → {self.assigned_to.get_full_name()}'
    
    def get_active_dates(self):
        """Retorna as datas ativas para este checklist"""
        from datetime import datetime, timedelta
        import calendar
        
        dates = []
        
        if self.schedule_type == 'custom':
            dates = [datetime.strptime(date_str, '%Y-%m-%d').date() for date_str in self.custom_dates]
        
        elif self.schedule_type == 'this_week':
            # Esta semana (segunda a domingo)
            today = timezone.now().date()
            start_of_week = today - timedelta(days=today.weekday())
            for i in range(7):
                date = start_of_week + timedelta(days=i)
                if self.start_date <= date <= self.end_date:
                    dates.append(date)
        
        elif self.schedule_type == 'weekdays_month':
            # Dias úteis do mês
            current_date = self.start_date
            while current_date <= self.end_date:
                if current_date.weekday() < 5:  # Segunda a sexta
                    dates.append(current_date)
                current_date += timedelta(days=1)
        
        elif self.schedule_type == 'weekends_month':
            # Fins de semana do mês
            current_date = self.start_date
            while current_date <= self.end_date:
                if current_date.weekday() >= 5:  # Sábado e domingo
                    dates.append(current_date)
                current_date += timedelta(days=1)
        
        elif self.schedule_type == 'daily':
            # Todos os dias
            current_date = self.start_date
            while current_date <= self.end_date:
                dates.append(current_date)
                current_date += timedelta(days=1)
        
        return sorted(dates)
    
    def get_status(self):
        """Retorna o status geral do assignment"""
        executions = self.executions.all()
        if not executions.exists():
            return 'pending'
        
        completed_count = executions.filter(status='completed').count()
        in_progress_count = executions.filter(status='in_progress').count()
        
        if completed_count == executions.count():
            return 'completed'
        elif in_progress_count > 0:
            return 'in_progress'
        else:
            return 'pending'
    
    def get_progress(self):
        """Retorna o progresso geral"""
        executions = self.executions.all()
        if not executions.exists():
            return {'completed': 0, 'total': 0, 'percentage': 0}
        
        completed = executions.filter(status='completed').count()
        total = executions.count()
        percentage = round((completed / total) * 100) if total > 0 else 0
        
        return {
            'completed': completed,
            'total': total,
            'percentage': percentage
        }
    
    def get_next_execution_dates(self):
        """Retorna as próximas datas de execução"""
        active_dates = self.get_active_dates()
        today = timezone.now().date()
        return [date for date in active_dates if date >= today][:10]  # Próximas 10 datas
    
    def is_due_today(self):
        """Verifica se há execução devida hoje"""
        today = timezone.now().date()
        return today in self.get_active_dates()


class ChecklistExecution(models.Model):
    """Execução de um checklist em uma data específica"""
    
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('in_progress', 'Em Andamento'),
        ('completed', 'Concluído'),
        ('overdue', 'Atrasado'),
        ('awaiting_approval', 'Aguardando Aprovação'),
    ]
    
    PERIOD_CHOICES = [
        ('morning', 'Manhã'),
        ('afternoon', 'Tarde'),
    ]
    
    assignment = models.ForeignKey(
        ChecklistAssignment, 
        on_delete=models.CASCADE, 
        related_name='executions',
        verbose_name='Atribuição'
    )
    
    execution_date = models.DateField(verbose_name='Data de Execução')
    period = models.CharField(
        max_length=10,
        choices=PERIOD_CHOICES,
        default='morning',
        verbose_name='Período',
        help_text='Período do dia (Manhã ou Tarde)'
    )
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='pending',
        verbose_name='Status'
    )
    
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='Iniciado em')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Concluído em')
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name='Enviado para Aprovação em')
    
    # Observações
    notes = models.TextField(blank=True, verbose_name='Observações')
    
    class Meta:
        verbose_name = 'Execução de Checklist'
        verbose_name_plural = 'Execuções de Checklist'
        ordering = ['-execution_date', 'period']
        unique_together = ['assignment', 'execution_date', 'period']
        
    def __str__(self):
        period_display = self.get_period_display()
        return f'{self.assignment.template.name} - {self.execution_date} {period_display} ({self.get_status_display()})'
    
    @property
    def progress_percentage(self):
        """Calcula a porcentagem de conclusão"""
        total_tasks = self.task_executions.count()
        if total_tasks == 0:
            return 0
        completed_tasks = self.task_executions.filter(is_completed=True).count()
        return round((completed_tasks / total_tasks) * 100)
    
    def update_status(self):
        """Atualiza o status baseado no progresso"""
        if self.completed_at:
            self.status = 'completed'
        elif self.started_at:
            if self.execution_date < timezone.now().date():
                self.status = 'overdue'
            else:
                self.status = 'in_progress'
        else:
            if self.execution_date < timezone.now().date():
                self.status = 'overdue'
            else:
                self.status = 'pending'
        self.save()


class ChecklistTaskExecution(models.Model):
    """Execução de uma tarefa específica do checklist"""
    
    execution = models.ForeignKey(
        ChecklistExecution, 
        on_delete=models.CASCADE, 
        related_name='task_executions',
        verbose_name='Execução'
    )
    
    task = models.ForeignKey(
        ChecklistTask, 
        on_delete=models.CASCADE, 
        verbose_name='Tarefa'
    )
    
    is_completed = models.BooleanField(default=False, verbose_name='Concluída')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Concluída em')
    notes = models.TextField(blank=True, verbose_name='Observações')
    
    # Campo para resposta de pergunta sim/não
    yes_no_answer = models.BooleanField(
        null=True,
        blank=True,
        verbose_name='Resposta Sim/Não',
        help_text='True = Sim, False = Não, None = Não respondida'
    )
    
    # Campo para resposta de dropdown (Sim/Não/Não se Aplica)
    DROPDOWN_CHOICES = [
        ('yes', 'Sim'),
        ('no', 'Não'),
        ('not_applicable', 'Não se Aplica'),
    ]
    dropdown_answer = models.CharField(
        max_length=20,
        choices=DROPDOWN_CHOICES,
        null=True,
        blank=True,
        verbose_name='Resposta Menu Suspenso',
        help_text='Resposta para perguntas do tipo menu suspenso'
    )
    
    # Status de aprovação por tarefa
    approval_status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pendente'),
            ('approved', 'Aprovada'),
            ('rejected', 'Reprovada'),
        ],
        default='pending',
        verbose_name='Status de Aprovação'
    )
    approval_notes = models.TextField(
        blank=True,
        verbose_name='Observações da Aprovação',
        help_text='Motivo da aprovação/reprovação'
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_tasks',
        verbose_name='Aprovado por'
    )
    approved_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Aprovado em'
    )
    
    # Evidências (mantidos para compatibilidade)
    evidence_image = models.ImageField(
        upload_to=upload_checklist_evidence_image,
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Imagem de Evidência',
        help_text='Foto como prova de execução da tarefa'
    )
    evidence_video = models.FileField(
        upload_to=upload_checklist_evidence_video,
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Vídeo de Evidência',
        help_text='Vídeo como prova de execução da tarefa'
    )
    
    class Meta:
        verbose_name = 'Execução de Tarefa'
        verbose_name_plural = 'Execuções de Tarefas'
        ordering = ['task__order']
        unique_together = ['execution', 'task']
        
    def __str__(self):
        return f'{self.execution} - {self.task.title}'
    
    def has_evidence(self):
        """Verifica se possui evidência anexada"""
        return bool(self.evidence_image or self.evidence_video) or self.evidences.exists()
    
    def complete_task(self, notes='', evidence_image=None, evidence_video=None):
        """Marca a tarefa como concluída com evidências"""
        self.is_completed = True
        self.completed_at = timezone.now()
        if notes:
            self.notes = notes
        if evidence_image:
            self.evidence_image = evidence_image
        if evidence_video:
            self.evidence_video = evidence_video
        self.save()
        
        # Verificar se todas as tarefas foram concluídas
        execution = self.execution
        all_completed = not execution.task_executions.filter(
            task__is_required=True, 
            is_completed=False
        ).exists()
        
        if all_completed and not execution.completed_at:
            execution.completed_at = timezone.now()
            execution.status = 'completed'
            execution.save()


class ChecklistTaskEvidence(models.Model):
    """Múltiplas evidências (fotos/vídeos/documentos) para uma tarefa executada"""
    
    EVIDENCE_TYPE_CHOICES = [
        ('image', 'Imagem'),
        ('video', 'Vídeo'),
        ('document', 'Documento'),
    ]
    
    task_execution = models.ForeignKey(
        ChecklistTaskExecution,
        on_delete=models.CASCADE,
        related_name='evidences',
        verbose_name='Execução da Tarefa'
    )
    
    evidence_type = models.CharField(
        max_length=10,
        choices=EVIDENCE_TYPE_CHOICES,
        verbose_name='Tipo de Evidência'
    )
    
    file = models.FileField(
        upload_to='checklists/evidences/',
        storage=get_media_storage(),
        verbose_name='Arquivo'
    )
    
    original_filename = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Nome Original do Arquivo'
    )
    
    uploaded_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Enviado em'
    )
    
    order = models.PositiveIntegerField(
        default=0,
        verbose_name='Ordem'
    )
    
    class Meta:
        verbose_name = 'Evidência de Tarefa'
        verbose_name_plural = 'Evidências de Tarefa'
        ordering = ['order', 'uploaded_at']
    
    def __str__(self):
        return f'{self.get_evidence_type_display()} - {self.task_execution}'
    
    def get_file_extension(self):
        """Retorna a extensão do arquivo"""
        import os
        return os.path.splitext(self.file.name)[1].lower() if self.file else ''
    
    def get_file_icon(self):
        """Retorna o ícone apropriado para o tipo de arquivo"""
        ext = self.get_file_extension()
        icons = {
            '.pdf': 'fa-file-pdf text-red-600',
            '.doc': 'fa-file-word text-blue-600',
            '.docx': 'fa-file-word text-blue-600',
            '.xls': 'fa-file-excel text-green-600',
            '.xlsx': 'fa-file-excel text-green-600',
            '.ppt': 'fa-file-powerpoint text-orange-600',
            '.pptx': 'fa-file-powerpoint text-orange-600',
            '.txt': 'fa-file-alt text-gray-600',
            '.zip': 'fa-file-archive text-yellow-600',
            '.rar': 'fa-file-archive text-yellow-600',
        }
        return icons.get(ext, 'fa-file text-gray-600')