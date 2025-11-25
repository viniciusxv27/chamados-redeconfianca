from django.db import models
from django.conf import settings
from users.models import User, Sector
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
import os


def upload_trail_icon(instance, filename):
    """Define o caminho de upload para ícones de trilha"""
    ext = filename.split('.')[-1]
    instance_id = instance.id if instance.id else 'new'
    new_filename = f"trail_{instance_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    return os.path.join('knowledge_trails', 'icons', new_filename)


def upload_lesson_media(instance, filename):
    """Define o caminho de upload para mídia de lição"""
    ext = filename.split('.')[-1]
    instance_id = instance.id if instance.id else 'new'
    new_filename = f"lesson_{instance_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    return os.path.join('knowledge_trails', 'lessons', new_filename)


def upload_certificate_logo(instance, filename):
    """Define o caminho de upload para logo do certificado"""
    ext = filename.split('.')[-1]
    new_filename = f"cert_logo_{timezone.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    return os.path.join('knowledge_trails', 'certificates', new_filename)


class KnowledgeTrail(models.Model):
    """Trilha de conhecimento de um setor"""
    
    DIFFICULTY_CHOICES = [
        ('beginner', 'Iniciante'),
        ('intermediate', 'Intermediário'),
        ('advanced', 'Avançado'),
        ('expert', 'Especialista'),
    ]
    
    title = models.CharField(max_length=200, verbose_name='Título da Trilha')
    description = models.TextField(verbose_name='Descrição')
    sector = models.ForeignKey(
        Sector,
        on_delete=models.CASCADE,
        related_name='knowledge_trails',
        verbose_name='Setor'
    )
    
    # Gamificação
    icon = models.ImageField(
        upload_to=upload_trail_icon,
        blank=True,
        null=True,
        verbose_name='Ícone da Trilha'
    )
    color = models.CharField(
        max_length=7,
        default='#3B82F6',
        verbose_name='Cor Principal',
        help_text='Código hexadecimal (ex: #3B82F6)'
    )
    difficulty = models.CharField(
        max_length=20,
        choices=DIFFICULTY_CHOICES,
        default='beginner',
        verbose_name='Dificuldade'
    )
    estimated_hours = models.PositiveIntegerField(
        default=1,
        verbose_name='Horas Estimadas',
        help_text='Tempo estimado para conclusão'
    )
    total_points = models.PositiveIntegerField(
        default=0,
        verbose_name='Total de Pontos',
        help_text='Pontos totais da trilha (calculado automaticamente)'
    )
    
    # Certificado
    enable_certificate = models.BooleanField(
        default=True,
        verbose_name='Habilitar Certificado',
        help_text='Gerar certificado ao concluir a trilha'
    )
    certificate_logo = models.ImageField(
        upload_to=upload_certificate_logo,
        blank=True,
        null=True,
        verbose_name='Logo para Certificado',
        help_text='Logo que aparecerá no certificado'
    )
    
    # Metadados
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_trails',
        verbose_name='Criado por'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    is_active = models.BooleanField(default=True, verbose_name='Ativa')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem de Exibição')
    
    class Meta:
        verbose_name = 'Trilha de Conhecimento'
        verbose_name_plural = 'Trilhas de Conhecimento'
        ordering = ['order', 'title']
        
    def __str__(self):
        return f'{self.title} - {self.sector.name}'
    
    def calculate_total_points(self):
        """Calcula o total de pontos da trilha"""
        total = 0
        for module in self.modules.all():
            for lesson in module.lessons.all():
                total += lesson.points
        self.total_points = total
        self.save(update_fields=['total_points'])
        return total
    
    def get_progress(self, user):
        """Retorna o progresso do usuário nesta trilha"""
        progress, created = TrailProgress.objects.get_or_create(
            trail=self,
            user=user,
            defaults={'status': 'not_started'}
        )
        return progress
    
    def get_completion_percentage(self, user):
        """Retorna a porcentagem de conclusão"""
        total_lessons = sum(module.lessons.count() for module in self.modules.all())
        if total_lessons == 0:
            return 0
        
        completed_lessons = LessonProgress.objects.filter(
            lesson__module__trail=self,
            user=user,
            completed=True
        ).count()
        
        return round((completed_lessons / total_lessons) * 100)
    
    def get_leaderboard(self):
        """Retorna o ranking de usuários desta trilha"""
        from django.db.models import Count, Sum, F
        
        return TrailProgress.objects.filter(
            trail=self,
            status='completed'
        ).annotate(
            total_points=Sum('lesson_progresses__lesson__points')
        ).order_by('-total_points', 'completed_at')[:10]


class TrailModule(models.Model):
    """Módulo dentro de uma trilha"""
    
    trail = models.ForeignKey(
        KnowledgeTrail,
        on_delete=models.CASCADE,
        related_name='modules',
        verbose_name='Trilha'
    )
    title = models.CharField(max_length=200, verbose_name='Título do Módulo')
    description = models.TextField(blank=True, verbose_name='Descrição')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    
    # Minimapa
    map_x = models.IntegerField(
        default=0,
        verbose_name='Posição X no Mapa',
        help_text='Coordenada horizontal no minimapa (0-100)'
    )
    map_y = models.IntegerField(
        default=0,
        verbose_name='Posição Y no Mapa',
        help_text='Coordenada vertical no minimapa (0-100)'
    )
    
    icon_emoji = models.CharField(
        max_length=10,
        default='📚',
        verbose_name='Emoji do Módulo',
        help_text='Emoji que representa o módulo'
    )
    
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    
    class Meta:
        verbose_name = 'Módulo da Trilha'
        verbose_name_plural = 'Módulos da Trilha'
        ordering = ['order']
        
    def __str__(self):
        return f'{self.trail.title} - {self.title}'
    
    def is_unlocked_for_user(self, user):
        """Verifica se o módulo está desbloqueado para o usuário"""
        if self.order == 0:
            return True
        
        # Verificar se o módulo anterior foi concluído
        previous_module = TrailModule.objects.filter(
            trail=self.trail,
            order__lt=self.order
        ).order_by('-order').first()
        
        if not previous_module:
            return True
        
        # Verificar se todas as lições do módulo anterior foram concluídas
        previous_lessons = previous_module.lessons.count()
        if previous_lessons == 0:
            return True
        
        completed_previous = LessonProgress.objects.filter(
            lesson__module=previous_module,
            user=user,
            completed=True
        ).count()
        
        return completed_previous >= previous_lessons


class Lesson(models.Model):
    """Lição dentro de um módulo"""
    
    LESSON_TYPE_CHOICES = [
        ('text', 'Texto'),
        ('video', 'Vídeo'),
        ('document', 'Documento'),
        ('quiz', 'Quiz'),
        ('interactive', 'Interativo'),
    ]
    
    module = models.ForeignKey(
        TrailModule,
        on_delete=models.CASCADE,
        related_name='lessons',
        verbose_name='Módulo'
    )
    title = models.CharField(max_length=200, verbose_name='Título da Lição')
    description = models.TextField(blank=True, verbose_name='Descrição')
    lesson_type = models.CharField(
        max_length=20,
        choices=LESSON_TYPE_CHOICES,
        default='text',
        verbose_name='Tipo de Lição'
    )
    
    # Conteúdo
    content = models.TextField(
        blank=True,
        verbose_name='Conteúdo',
        help_text='Conteúdo em texto (suporta markdown)'
    )
    video_url = models.URLField(
        blank=True,
        verbose_name='URL do Vídeo',
        help_text='URL do YouTube, Vimeo, etc.'
    )
    video_file = models.FileField(
        upload_to=upload_lesson_media,
        blank=True,
        null=True,
        verbose_name='Arquivo de Vídeo',
        help_text='Upload de vídeo (MP4, WebM, etc.)'
    )
    document_file = models.FileField(
        upload_to=upload_lesson_media,
        blank=True,
        null=True,
        verbose_name='Arquivo de Documento',
        help_text='PDF, DOC, PPT, XLS, etc.'
    )
    media_file = models.FileField(
        upload_to=upload_lesson_media,
        blank=True,
        null=True,
        verbose_name='Arquivo de Mídia',
        help_text='Outros arquivos (imagens, etc.)'
    )
    
    # Gamificação
    points = models.PositiveIntegerField(
        default=10,
        verbose_name='Pontos',
        help_text='Pontos ganhos ao concluir esta lição'
    )
    duration_minutes = models.PositiveIntegerField(
        default=5,
        verbose_name='Duração (minutos)',
        help_text='Tempo estimado para completar'
    )
    
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    is_required = models.BooleanField(
        default=True,
        verbose_name='Obrigatória',
        help_text='Lição obrigatória para conclusão do módulo'
    )
    is_active = models.BooleanField(default=True, verbose_name='Ativa')
    
    class Meta:
        verbose_name = 'Lição'
        verbose_name_plural = 'Lições'
        ordering = ['order']
        
    def __str__(self):
        return f'{self.module.title} - {self.title}'
    
    def is_unlocked_for_user(self, user):
        """Verifica se a lição está desbloqueada para o usuário"""
        # Verificar se o módulo está desbloqueado
        if not self.module.is_unlocked_for_user(user):
            return False
        
        if self.order == 0:
            return True
        
        # Verificar se a lição anterior foi concluída
        previous_lesson = Lesson.objects.filter(
            module=self.module,
            order__lt=self.order
        ).order_by('-order').first()
        
        if not previous_lesson:
            return True
        
        return LessonProgress.objects.filter(
            lesson=previous_lesson,
            user=user,
            completed=True
        ).exists()


class QuizQuestion(models.Model):
    """Pergunta de quiz para lições do tipo quiz"""
    
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name='quiz_questions',
        verbose_name='Lição'
    )
    question_text = models.TextField(verbose_name='Pergunta')
    points = models.PositiveIntegerField(
        default=10,
        verbose_name='Pontos',
        help_text='Pontos ganhos ao acertar esta questão'
    )
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    
    class Meta:
        verbose_name = 'Pergunta de Quiz'
        verbose_name_plural = 'Perguntas de Quiz'
        ordering = ['order']
        
    def __str__(self):
        return f'{self.lesson.title} - Q{self.order + 1}'


class QuizOption(models.Model):
    """Opção de resposta para uma pergunta de quiz"""
    
    question = models.ForeignKey(
        QuizQuestion,
        on_delete=models.CASCADE,
        related_name='options',
        verbose_name='Pergunta'
    )
    option_text = models.CharField(max_length=500, verbose_name='Texto da Opção')
    is_correct = models.BooleanField(default=False, verbose_name='É a Resposta Correta')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    
    class Meta:
        verbose_name = 'Opção de Resposta'
        verbose_name_plural = 'Opções de Resposta'
        ordering = ['order']
        
    def __str__(self):
        return self.option_text


class TrailProgress(models.Model):
    """Progresso do usuário em uma trilha"""
    
    STATUS_CHOICES = [
        ('not_started', 'Não Iniciado'),
        ('in_progress', 'Em Progresso'),
        ('completed', 'Concluído'),
    ]
    
    trail = models.ForeignKey(
        KnowledgeTrail,
        on_delete=models.CASCADE,
        related_name='user_progresses',
        verbose_name='Trilha'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='trail_progresses',
        verbose_name='Usuário'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='not_started',
        verbose_name='Status'
    )
    
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='Iniciado em')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Concluído em')
    
    # Pontuação
    total_points_earned = models.PositiveIntegerField(
        default=0,
        verbose_name='Pontos Conquistados'
    )
    
    class Meta:
        verbose_name = 'Progresso na Trilha'
        verbose_name_plural = 'Progressos nas Trilhas'
        unique_together = ['trail', 'user']
        
    def __str__(self):
        return f'{self.user.get_full_name()} - {self.trail.title}'
    
    def update_progress(self):
        """Atualiza o status e pontos do progresso"""
        total_lessons = sum(
            module.lessons.filter(is_active=True).count() 
            for module in self.trail.modules.filter(is_active=True)
        )
        
        if total_lessons == 0:
            return
        
        completed_lessons = LessonProgress.objects.filter(
            lesson__module__trail=self.trail,
            user=self.user,
            completed=True
        ).count()
        
        # Atualizar pontos
        self.total_points_earned = LessonProgress.objects.filter(
            lesson__module__trail=self.trail,
            user=self.user,
            completed=True
        ).aggregate(
            total=models.Sum('lesson__points')
        )['total'] or 0
        
        # Atualizar status
        if completed_lessons == 0:
            self.status = 'not_started'
        elif completed_lessons >= total_lessons:
            self.status = 'completed'
            if not self.completed_at:
                self.completed_at = timezone.now()
        else:
            self.status = 'in_progress'
            if not self.started_at:
                self.started_at = timezone.now()
        
        self.save()


class LessonProgress(models.Model):
    """Progresso do usuário em uma lição"""
    
    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        related_name='user_progresses',
        verbose_name='Lição'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='lesson_progresses',
        verbose_name='Usuário'
    )
    
    completed = models.BooleanField(default=False, verbose_name='Concluída')
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name='Concluída em')
    
    # Quiz
    quiz_score = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Pontuação no Quiz',
        validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    quiz_attempts = models.PositiveIntegerField(
        default=0,
        verbose_name='Tentativas no Quiz'
    )
    
    class Meta:
        verbose_name = 'Progresso na Lição'
        verbose_name_plural = 'Progressos nas Lições'
        unique_together = ['lesson', 'user']
        
    def __str__(self):
        return f'{self.user.get_full_name()} - {self.lesson.title}'
    
    def mark_completed(self):
        """Marca a lição como concluída"""
        if not self.completed:
            self.completed = True
            self.completed_at = timezone.now()
            self.save()
            
            # Atualizar progresso da trilha
            trail_progress = TrailProgress.objects.get_or_create(
                trail=self.lesson.module.trail,
                user=self.user
            )[0]
            trail_progress.update_progress()


class Certificate(models.Model):
    """Certificado de conclusão de trilha"""
    
    trail_progress = models.OneToOneField(
        TrailProgress,
        on_delete=models.CASCADE,
        related_name='certificate',
        verbose_name='Progresso da Trilha'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='certificates',
        verbose_name='Usuário'
    )
    trail = models.ForeignKey(
        KnowledgeTrail,
        on_delete=models.CASCADE,
        related_name='certificates',
        verbose_name='Trilha'
    )
    
    certificate_code = models.CharField(
        max_length=50,
        unique=True,
        verbose_name='Código do Certificado'
    )
    issued_at = models.DateTimeField(auto_now_add=True, verbose_name='Emitido em')
    
    class Meta:
        verbose_name = 'Certificado'
        verbose_name_plural = 'Certificados'
        ordering = ['-issued_at']
        
    def __str__(self):
        return f'Certificado - {self.user.get_full_name()} - {self.trail.title}'
    
    def generate_code(self):
        """Gera um código único para o certificado"""
        import secrets
        import string
        
        while True:
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(12))
            if not Certificate.objects.filter(certificate_code=code).exists():
                self.certificate_code = code
                break
    
    def save(self, *args, **kwargs):
        if not self.certificate_code:
            self.generate_code()
        super().save(*args, **kwargs)
