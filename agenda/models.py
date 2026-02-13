from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta


class EventParticipant(models.Model):
    """Participante de um evento com status de aceite"""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('accepted', 'Aceito'),
        ('rejected', 'Recusado'),
    ]

    event = models.ForeignKey(
        'CalendarEvent',
        on_delete=models.CASCADE,
        related_name='event_participants',
        verbose_name='Evento',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='event_invitations',
        verbose_name='Participante',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status',
    )
    response_notes = models.TextField(blank=True, verbose_name='Observação')
    invited_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Participante do Evento'
        verbose_name_plural = 'Participantes do Evento'
        unique_together = ['event', 'user']

    def __str__(self):
        return f'{self.user.full_name} - {self.event.title} ({self.get_status_display()})'

    def accept(self, notes=''):
        self.status = 'accepted'
        self.response_notes = notes
        self.responded_at = timezone.now()
        self.save()

    def reject(self, notes=''):
        self.status = 'rejected'
        self.response_notes = notes
        self.responded_at = timezone.now()
        self.save()


class CalendarEvent(models.Model):
    """Evento na agenda do usuário (similar ao Google Calendar)"""
    TYPE_CHOICES = [
        ('event', 'Evento'),
        ('meeting', 'Reunião'),
        ('call', 'Chamada'),
        ('task', 'Tarefa'),
        ('reminder', 'Lembrete'),
        ('block', 'Bloqueio'),
    ]

    COLOR_CHOICES = [
        ('#4f46e5', 'Indigo'),
        ('#dc2626', 'Vermelho'),
        ('#16a34a', 'Verde'),
        ('#ca8a04', 'Amarelo'),
        ('#2563eb', 'Azul'),
        ('#9333ea', 'Roxo'),
        ('#ea580c', 'Laranja'),
        ('#0d9488', 'Teal'),
        ('#6b7280', 'Cinza'),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calendar_events',
        verbose_name='Proprietário',
    )
    title = models.CharField(max_length=255, verbose_name='Título')
    description = models.TextField(blank=True, verbose_name='Descrição')
    event_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='event',
        verbose_name='Tipo',
    )
    color = models.CharField(
        max_length=10,
        choices=COLOR_CHOICES,
        default='#4f46e5',
        verbose_name='Cor',
    )
    start = models.DateTimeField(verbose_name='Início')
    end = models.DateTimeField(verbose_name='Fim')
    all_day = models.BooleanField(default=False, verbose_name='Dia inteiro')
    location = models.CharField(max_length=255, blank=True, verbose_name='Local')

    # Participantes convidados
    participants = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='invited_events',
        verbose_name='Participantes',
    )

    # Privacidade
    is_private = models.BooleanField(
        default=False,
        verbose_name='Evento privado',
        help_text='Eventos privados mostram apenas "Ocupado" para outros usuários',
    )

    # Metadados
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Evento'
        verbose_name_plural = 'Eventos'
        ordering = ['start']
        indexes = [
            models.Index(fields=['owner', 'start', 'end']),
            models.Index(fields=['start', 'end']),
        ]

    def __str__(self):
        return f'{self.title} ({self.start:%d/%m/%Y %H:%M})'

    @property
    def duration_minutes(self):
        return int((self.end - self.start).total_seconds() / 60)

    def overlaps(self, other_start, other_end):
        """Verifica se este evento tem sobreposição com o intervalo dado"""
        return self.start < other_end and self.end > other_start


class MeetingRequest(models.Model):
    """Solicitação de encontro/chamada/horário entre usuários"""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('accepted', 'Aceito'),
        ('rejected', 'Recusado'),
        ('cancelled', 'Cancelado'),
    ]

    TYPE_CHOICES = [
        ('meeting', 'Reunião'),
        ('call', 'Chamada'),
        ('appointment', 'Horário'),
    ]

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='meeting_requests_sent',
        verbose_name='Solicitante',
    )
    target = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='meeting_requests_received',
        verbose_name='Destinatário',
    )
    title = models.CharField(max_length=255, verbose_name='Título')
    description = models.TextField(blank=True, verbose_name='Descrição / Motivo')
    meeting_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='meeting',
        verbose_name='Tipo',
    )
    proposed_start = models.DateTimeField(verbose_name='Horário proposto - Início')
    proposed_end = models.DateTimeField(verbose_name='Horário proposto - Fim')
    location = models.CharField(max_length=255, blank=True, verbose_name='Local sugerido')

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status',
    )
    response_notes = models.TextField(blank=True, verbose_name='Observação da resposta')

    # Evento criado quando aceito
    created_event = models.ForeignKey(
        CalendarEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='meeting_request',
        verbose_name='Evento criado',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Solicitação de Reunião'
        verbose_name_plural = 'Solicitações de Reunião'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.requester} → {self.target}: {self.title}'

    def accept(self, notes=''):
        """Aceitar solicitação e criar eventos na agenda de ambos"""
        self.status = 'accepted'
        self.response_notes = notes
        self.save()

        # Criar evento na agenda do destinatário
        event = CalendarEvent.objects.create(
            owner=self.target,
            title=self.title,
            description=self.description,
            event_type=self.meeting_type if self.meeting_type != 'appointment' else 'meeting',
            start=self.proposed_start,
            end=self.proposed_end,
            location=self.location,
            color='#16a34a',
        )
        event.participants.add(self.requester)
        self.created_event = event
        self.save()

        # Criar evento espelhado na agenda do solicitante
        mirror = CalendarEvent.objects.create(
            owner=self.requester,
            title=self.title,
            description=self.description,
            event_type=self.meeting_type if self.meeting_type != 'appointment' else 'meeting',
            start=self.proposed_start,
            end=self.proposed_end,
            location=self.location,
            color='#16a34a',
        )
        mirror.participants.add(self.target)

    def reject(self, notes=''):
        self.status = 'rejected'
        self.response_notes = notes
        self.save()

    def cancel(self):
        self.status = 'cancelled'
        self.save()
