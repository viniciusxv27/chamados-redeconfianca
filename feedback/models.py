from django.conf import settings
from django.db import models
from django.utils import timezone


SCALE_CHOICES = [(i, str(i)) for i in range(0, 11)]


class FeedbackAssignment(models.Model):
    """Define quem deve dar feedback em quem.

    Uma pessoa (evaluator) pode ter várias atribuições (vários evaluatees).
    """

    STATUS_CHOICES = [
        ('ACTIVE', 'Ativo'),
        ('CANCELLED', 'Cancelado'),
    ]

    evaluator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='feedback_assignments_to_give',
        verbose_name='Avaliador (quem dará o feedback)',
    )
    evaluatee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='feedback_assignments_to_receive',
        verbose_name='Avaliado (quem receberá o feedback)',
    )
    notes = models.TextField(blank=True, verbose_name='Observações da atribuição')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    monthly = models.BooleanField(
        default=False,
        verbose_name='Fazer todo mês',
        help_text='Se marcado, o avaliador será lembrado todo mês (10 dias antes do fim) para aplicar este feedback.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='feedback_assignments_created',
        verbose_name='Criado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Atribuição de Feedback'
        verbose_name_plural = 'Atribuições de Feedback'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.evaluator} → {self.evaluatee} ({self.get_status_display()})'


class Feedback(models.Model):
    """Feedback aplicado por um avaliador a um colaborador.

    Replica os campos do formulário FM-005 - FEEDBACK GERAL.
    """

    assignment = models.ForeignKey(
        FeedbackAssignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='feedbacks',
        verbose_name='Atribuição de origem',
    )
    evaluator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='feedbacks_given',
        verbose_name='Avaliador',
    )
    evaluatee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='feedbacks_received',
        verbose_name='Colaborador avaliado',
    )

    # Cabeçalho do formulário
    setor_area = models.CharField(max_length=200, blank=True, verbose_name='Setor / Área')
    data = models.DateField(default=timezone.now, verbose_name='Data')
    nome_colaborador = models.CharField(max_length=200, blank=True, verbose_name='Nome do Colaborador(a)')
    gestor_imediato = models.CharField(max_length=200, blank=True, verbose_name='Gestor Imediato')
    gestor_mediato = models.CharField(max_length=200, blank=True, verbose_name='Gestor Mediato')

    # Bloco AVALIAÇÃO (texto)
    pontos_fortes = models.TextField(blank=True, verbose_name='Pontos Fortes')
    oportunidades_melhoria = models.TextField(blank=True, verbose_name='Oportunidades de Melhoria')
    acoes_propostas = models.TextField(blank=True, verbose_name='Ações Propostas')

    # AVALIAÇÃO DE DESEMPENHO E COMPETÊNCIAS (escalas 0-10)
    nota_comunicacao = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=SCALE_CHOICES,
        verbose_name='Comunicação (0-10)',
        help_text='Como você avalia sua comunicação (0 insatisfeito, 10 muito satisfeito).',
    )
    nota_trabalho_equipe = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=SCALE_CHOICES,
        verbose_name='Trabalho em equipe (0-10)',
    )
    nota_organizacao = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=SCALE_CHOICES,
        verbose_name='Organização (0-10)',
    )
    comunicacao_clara_texto = models.TextField(
        blank=True,
        verbose_name='Você sente que se comunica de forma clara e eficaz com sua equipe e superiores?',
    )
    nota_ferramentas_recursos = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=SCALE_CHOICES,
        verbose_name='Possui as ferramentas e recursos necessários (0-10)',
    )
    nota_iniciativa = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=SCALE_CHOICES,
        verbose_name='Tomou iniciativa / foi além (0-10)',
    )
    nota_mudancas = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=SCALE_CHOICES,
        verbose_name='Lida com mudanças e imprevistos (0-10)',
    )
    nota_conflitos = models.PositiveSmallIntegerField(
        null=True, blank=True, choices=SCALE_CHOICES,
        verbose_name='Lida com conflitos / desentendimentos (0-10)',
    )
    cumpriu_metas_texto = models.TextField(
        blank=True,
        verbose_name='Você acredita que cumpriu as metas estabelecidas para este período? Por quê?',
    )
    suporte_orientacao_texto = models.TextField(
        blank=True,
        verbose_name='Como avalia o suporte e a orientação que recebe de seus superiores?',
    )

    # Campo de evolução
    evolution_notes = models.TextField(
        blank=True,
        verbose_name='Evolução do colaborador',
        help_text='Considerações sobre a evolução do colaborador desde o último feedback.',
    )

    # Áudio do feedback (gravado pelo avaliador) e transcrição por IA
    AUDIO_CONTEXT_CHOICES = [
        ('CONVERSA', 'Conversa entre avaliador e avaliado'),
        ('AVALIADOR', 'Somente o avaliador'),
        ('AVALIADO', 'Somente o avaliado'),
    ]
    audio_file = models.FileField(
        upload_to='feedback/audio/%Y/%m/',
        null=True,
        blank=True,
        verbose_name='Áudio do feedback',
    )
    audio_context = models.CharField(
        max_length=20,
        choices=AUDIO_CONTEXT_CHOICES,
        blank=True,
        verbose_name='Contexto do áudio',
        help_text='Quem aparece no áudio gravado.',
    )
    audio_transcription = models.TextField(
        blank=True,
        verbose_name='Transcrição do áudio',
    )
    audio_transcribed_at = models.DateTimeField(null=True, blank=True)
    audio_transcription_error = models.TextField(blank=True)

    # Resumo gerado por IA (visível para superadmin)
    ai_summary = models.TextField(
        blank=True,
        verbose_name='Resumo gerado por IA',
    )
    ai_summary_generated_at = models.DateTimeField(null=True, blank=True)
    ai_summary_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Feedback'
        verbose_name_plural = 'Feedbacks'
        ordering = ['-data', '-created_at']

    def __str__(self):
        return f'Feedback de {self.evaluator} para {self.evaluatee} em {self.data}'

    SCALE_FIELDS = [
        ('nota_comunicacao', 'Comunicação'),
        ('nota_trabalho_equipe', 'Trabalho em equipe'),
        ('nota_organizacao', 'Organização'),
        ('nota_ferramentas_recursos', 'Ferramentas e recursos'),
        ('nota_iniciativa', 'Iniciativa'),
        ('nota_mudancas', 'Lida com mudanças'),
        ('nota_conflitos', 'Lida com conflitos'),
    ]

    def average_score(self):
        values = [getattr(self, f) for f, _ in self.SCALE_FIELDS if getattr(self, f) is not None]
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    def previous_feedback(self):
        return (
            Feedback.objects
            .filter(evaluatee=self.evaluatee, created_at__lt=self.created_at)
            .order_by('-created_at')
            .first()
        )

    def evolution_delta(self):
        """Diferença entre média atual e média do feedback anterior do mesmo colaborador."""
        prev = self.previous_feedback()
        if not prev:
            return None
        prev_avg = prev.average_score()
        cur_avg = self.average_score()
        if prev_avg is None or cur_avg is None:
            return None
        return round(cur_avg - prev_avg, 2)


class ClimateSurveyParticipation(models.Model):
    """Controle separado de participação sem vínculo com as respostas anônimas."""

    STATUS_CHOICES = [
        ('IN_PROGRESS', 'Em andamento'),
        ('COMPLETED', 'Concluída'),
    ]

    survey_key = models.CharField(max_length=80, default='clima_organizacional_2026', db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='climate_survey_participations',
        verbose_name='Usuário',
    )
    sector = models.ForeignKey(
        'users.Sector',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='climate_survey_participations',
        verbose_name='Setor',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='IN_PROGRESS')
    last_step = models.CharField(max_length=120, blank=True, verbose_name='Última etapa')
    started_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('survey_key', 'user')
        verbose_name = 'Participação na Pesquisa de Clima'
        verbose_name_plural = 'Participações na Pesquisa de Clima'
        ordering = ['status', 'user__first_name', 'user__last_name']

    def __str__(self):
        return f'{self.user} - {self.get_status_display()}'


class ClimateSurveyResponse(models.Model):
    """Resposta anônima da pesquisa; não armazena usuário nem nome."""

    survey_key = models.CharField(max_length=80, default='clima_organizacional_2026', db_index=True)
    sector = models.ForeignKey(
        'users.Sector',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='climate_survey_responses',
        verbose_name='Setor',
    )
    answers = models.JSONField(default=dict, verbose_name='Respostas')
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Resposta Anônima da Pesquisa de Clima'
        verbose_name_plural = 'Respostas Anônimas da Pesquisa de Clima'
        ordering = ['-submitted_at']

    def __str__(self):
        sector = self.sector.name if self.sector else 'Sem setor'
        return f'Pesquisa de Clima - {sector} em {self.submitted_at:%d/%m/%Y %H:%M}'


class FeedbackReminderDismissal(models.Model):
    """Registra que um lembrete específico foi dispensado pelo usuário (para não exibir o popup novamente)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='feedback_reminder_dismissals',
    )
    key = models.CharField(max_length=120, db_index=True)
    dismissed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'key')
        verbose_name = 'Lembrete dispensado'
        verbose_name_plural = 'Lembretes dispensados'

    def __str__(self):
        return f'{self.user} dispensou {self.key}'
