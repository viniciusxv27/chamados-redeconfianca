from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from users.models import User, Sector
from django.utils import timezone
import json


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
    
    class Meta:
        verbose_name = 'Tarefa de Checklist'
        verbose_name_plural = 'Tarefas de Checklist'
        ordering = ['order', 'id']
        
    def __str__(self):
        return f'{self.template.name} - {self.title}'


class ChecklistAssignment(models.Model):
    """Atribuição de checklist para um usuário específico"""
    
    SCHEDULE_CHOICES = [
        ('this_week', 'Esta Semana'),
        ('weekdays_month', 'Dias Úteis do Mês'),
        ('weekends_month', 'Fins de Semana do Mês'),
        ('daily', 'Todos os Dias'),
        ('custom', 'Datas Personalizadas'),
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
        return f'{self.template.name} → {self.assigned_to.get_full_name()}'
    
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
    ]
    
    assignment = models.ForeignKey(
        ChecklistAssignment, 
        on_delete=models.CASCADE, 
        related_name='executions',
        verbose_name='Atribuição'
    )
    
    execution_date = models.DateField(verbose_name='Data de Execução')
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='pending',
        verbose_name='Status'
    )
    
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='Iniciado em')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Concluído em')
    
    # Observações
    notes = models.TextField(blank=True, verbose_name='Observações')
    
    class Meta:
        verbose_name = 'Execução de Checklist'
        verbose_name_plural = 'Execuções de Checklist'
        ordering = ['-execution_date']
        unique_together = ['assignment', 'execution_date']
        
    def __str__(self):
        return f'{self.assignment.template.name} - {self.execution_date} ({self.get_status_display()})'
    
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
    
    class Meta:
        verbose_name = 'Execução de Tarefa'
        verbose_name_plural = 'Execuções de Tarefas'
        ordering = ['task__order']
        unique_together = ['execution', 'task']
        
    def __str__(self):
        return f'{self.execution} - {self.task.title}'
    
    def complete_task(self, notes=''):
        """Marca a tarefa como concluída"""
        self.is_completed = True
        self.completed_at = timezone.now()
        if notes:
            self.notes = notes
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