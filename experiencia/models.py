import os
import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from users.models import Sector


def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage()
    return None


def upload_experiencia_photo(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"exp_{uuid.uuid4()}{ext}"
    return f"experiencia/fotos/{unique_filename}"


class ExperienciaTemplate(models.Model):
    """Template de perguntas criado pelo gestor para lançar para os setores."""
    name = models.CharField(max_length=200, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrição')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='experiencia_templates_created',
        verbose_name='Criado por',
    )
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'Template de Experiência'
        verbose_name_plural = 'Templates de Experiência'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def total_points(self):
        return self.questions.aggregate(total=models.Sum('points'))['total'] or 0


class ExperienciaQuestion(models.Model):
    """Pergunta que compõe um template."""
    template = models.ForeignKey(
        ExperienciaTemplate,
        on_delete=models.CASCADE,
        related_name='questions',
        verbose_name='Template',
    )
    text = models.CharField(max_length=500, verbose_name='Pergunta')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    points = models.PositiveIntegerField(default=0, verbose_name='Pontos')

    class Meta:
        verbose_name = 'Pergunta'
        verbose_name_plural = 'Perguntas'
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.text} ({self.points} pts)"


class ExperienciaTodo(models.Model):
    """To-do mensal lançado pelo gestor para um setor."""
    STATUS_CHOICES = [
        ('aberto', 'Aberto'),
        ('enviado', 'Enviado'),
        ('aprovado', 'Aprovado'),
        ('recusado', 'Recusado Parcialmente'),
        ('finalizado', 'Finalizado'),
    ]

    template = models.ForeignKey(
        ExperienciaTemplate,
        on_delete=models.CASCADE,
        related_name='todos',
        verbose_name='Template',
    )
    sector = models.ForeignKey(
        Sector,
        on_delete=models.CASCADE,
        related_name='experiencia_todos',
        verbose_name='Setor',
    )
    month = models.PositiveIntegerField(verbose_name='Mês')
    year = models.PositiveIntegerField(verbose_name='Ano')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='aberto',
        verbose_name='Status',
    )
    launched_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='experiencia_todos_launched',
        verbose_name='Lançado por',
    )
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='experiencia_todos_submitted',
        verbose_name='Enviado por',
    )
    evaluated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='experiencia_todos_evaluated',
        verbose_name='Avaliado por',
    )
    evaluation_date = models.DateTimeField(null=True, blank=True, verbose_name='Data da Avaliação')
    score_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        verbose_name='Pontuação (%)',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'To-Do Experiência'
        verbose_name_plural = 'To-Dos Experiência'
        ordering = ['-year', '-month', 'sector__name']
        constraints = [
            models.UniqueConstraint(
                fields=['sector', 'month', 'year', 'template'],
                name='unique_todo_per_sector_month_template',
            )
        ]

    def __str__(self):
        return f"{self.sector.name} - {self.month:02d}/{self.year} - {self.template.name}"

    @property
    def month_year_display(self):
        months = [
            '', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
            'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
        ]
        return f"{months[self.month]}/{self.year}"

    def calculate_score(self):
        """Calcula a porcentagem de pontos aprovados."""
        answers = self.answers.all()
        total_points = sum(a.question.points for a in answers)
        if total_points == 0:
            return 0
        approved_points = sum(
            a.question.points for a in answers if a.status == 'aprovado'
        )
        return round((approved_points / total_points) * 100, 2)

    def update_score(self):
        self.score_percentage = self.calculate_score()
        self.save(update_fields=['score_percentage'])

    def can_be_filled(self):
        """Verifica se o to-do pode ser preenchido (está aberto ou recusado parcialmente)."""
        return self.status in ('aberto', 'recusado')

    @property
    def is_current_month(self):
        now = timezone.now()
        return self.month == now.month and self.year == now.year


class ExperienciaAnswer(models.Model):
    """Resposta de uma pergunta dentro de um to-do."""
    STATUS_CHOICES = [
        ('pendente', 'Pendente'),
        ('aprovado', 'Aprovado'),
        ('recusado', 'Recusado'),
    ]

    todo = models.ForeignKey(
        ExperienciaTodo,
        on_delete=models.CASCADE,
        related_name='answers',
        verbose_name='To-Do',
    )
    question = models.ForeignKey(
        ExperienciaQuestion,
        on_delete=models.CASCADE,
        related_name='answers',
        verbose_name='Pergunta',
    )
    observation = models.TextField(blank=True, verbose_name='Observação/Resposta')
    photo = models.ImageField(
        upload_to=upload_experiencia_photo,
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Foto',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pendente',
        verbose_name='Status',
    )
    rejection_reason = models.TextField(blank=True, verbose_name='Motivo da Recusa')
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='experiencia_answers',
        verbose_name='Respondido por',
    )
    answered_at = models.DateTimeField(null=True, blank=True, verbose_name='Respondido em')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'Resposta'
        verbose_name_plural = 'Respostas'
        ordering = ['question__order', 'question__id']
        constraints = [
            models.UniqueConstraint(
                fields=['todo', 'question'],
                name='unique_answer_per_question',
            )
        ]

    def __str__(self):
        return f"Resposta: {self.question.text[:50]} - {self.get_status_display()}"


class ExperienciaEvaluator(models.Model):
    """Avaliador designado pelo superadmin para avaliar os to-dos."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='experiencia_evaluator_roles',
        verbose_name='Avaliador',
    )
    sectors = models.ManyToManyField(
        Sector,
        blank=True,
        related_name='experiencia_evaluators',
        verbose_name='Setores que avalia',
    )
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')

    class Meta:
        verbose_name = 'Avaliador'
        verbose_name_plural = 'Avaliadores'

    def __str__(self):
        return f"Avaliador: {self.user.get_full_name() or self.user.username}"
