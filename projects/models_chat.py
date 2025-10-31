from django.db import models
from django.conf import settings
from django.utils import timezone
from projects.models import Activity


class TaskChat(models.Model):
    """Chat de uma tarefa específica"""
    activity = models.OneToOneField(
        Activity,
        on_delete=models.CASCADE,
        related_name='chat'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Chat da Tarefa"
        verbose_name_plural = "Chats das Tarefas"

    def __str__(self):
        return f"Chat: {self.activity.name}"


class TaskChatMessage(models.Model):
    """Mensagem do chat de uma tarefa"""
    chat = models.ForeignKey(
        TaskChat,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='task_chat_messages'
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Mensagem do Chat"
        verbose_name_plural = "Mensagens do Chat"
        ordering = ['created_at']

    def __str__(self):
        return f"{self.user.get_full_name()}: {self.message[:50]}..."


class SupportChat(models.Model):
    """Chat de suporte geral da plataforma"""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='support_chats'
    )
    title = models.CharField(max_length=200, verbose_name="Título")
    status = models.CharField(
        max_length=20,
        choices=[
            ('ABERTO', 'Aberto'),
            ('EM_ANDAMENTO', 'Em Andamento'),
            ('RESOLVIDO', 'Resolvido'),
            ('FECHADO', 'Fechado'),
        ],
        default='ABERTO'
    )
    priority = models.CharField(
        max_length=20,
        choices=[
            ('BAIXA', 'Baixa'),
            ('MEDIA', 'Média'),
            ('ALTA', 'Alta'),
            ('URGENTE', 'Urgente'),
        ],
        default='MEDIA'
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_support_chats',
        verbose_name="Atribuído para"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Chat de Suporte"
        verbose_name_plural = "Chats de Suporte"
        ordering = ['-created_at']

    def __str__(self):
        return f"Suporte: {self.title} - {self.user.get_full_name()}"

    def close_chat(self):
        self.status = 'FECHADO'
        self.closed_at = timezone.now()
        self.save()


class SupportChatMessage(models.Model):
    """Mensagem do chat de suporte"""
    chat = models.ForeignKey(
        SupportChat,
        on_delete=models.CASCADE,
        related_name='messages'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='support_chat_messages'
    )
    message = models.TextField()
    is_internal = models.BooleanField(
        default=False,
        verbose_name="Mensagem interna (apenas para equipe de suporte)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Mensagem do Suporte"
        verbose_name_plural = "Mensagens do Suporte"
        ordering = ['created_at']

    def __str__(self):
        return f"{self.user.get_full_name()}: {self.message[:50]}..."


class SupportAgent(models.Model):
    """Agentes de suporte autorizados"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='support_agent'
    )
    sectors = models.ManyToManyField(
        'users.Sector',
        related_name='support_agents',
        blank=True,
        verbose_name="Setores atendidos"
    )
    is_active = models.BooleanField(default=True)
    can_assign_tickets = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Agente de Suporte"
        verbose_name_plural = "Agentes de Suporte"

    def __str__(self):
        return f"Agente: {self.user.get_full_name()}"


class SupportCategory(models.Model):
    """Categorias de suporte por setor"""
    name = models.CharField(max_length=100, verbose_name="Nome da Categoria")
    sector = models.ForeignKey(
        'users.Sector',
        on_delete=models.CASCADE,
        related_name='support_categories',
        verbose_name="Setor"
    )
    description = models.TextField(blank=True, verbose_name="Descrição")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Categoria de Suporte"
        verbose_name_plural = "Categorias de Suporte"
        unique_together = ['name', 'sector']

    def __str__(self):
        return f"{self.sector.name} - {self.name}"


class SupportChatFile(models.Model):
    """Arquivos enviados no chat de suporte"""
    MESSAGE_TYPES = [
        ('IMAGE', 'Imagem'),
        ('VIDEO', 'Vídeo'),
        ('AUDIO', 'Áudio'),
        ('DOCUMENT', 'Documento'),
    ]
    
    chat = models.ForeignKey(
        SupportChat,
        on_delete=models.CASCADE,
        related_name='files'
    )
    message = models.ForeignKey(
        SupportChatMessage,
        on_delete=models.CASCADE,
        related_name='files'
    )
    file = models.FileField(upload_to='support_chat_files/%Y/%m/')
    file_type = models.CharField(max_length=20, choices=MESSAGE_TYPES)
    original_name = models.CharField(max_length=255)
    file_size = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Arquivo do Chat"
        verbose_name_plural = "Arquivos do Chat"

    def __str__(self):
        return f"{self.original_name} - {self.chat.title}"


class SupportChatRating(models.Model):
    """Avaliação do atendimento"""
    RATING_CHOICES = [
        (1, 'Muito Insatisfeito'),
        (2, 'Insatisfeito'),
        (3, 'Neutro'),
        (4, 'Satisfeito'),
        (5, 'Muito Satisfeito'),
    ]
    
    chat = models.OneToOneField(
        SupportChat,
        on_delete=models.CASCADE,
        related_name='rating'
    )
    rating = models.IntegerField(choices=RATING_CHOICES)
    feedback = models.TextField(blank=True, verbose_name="Comentário")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Avaliação do Atendimento"
        verbose_name_plural = "Avaliações do Atendimento"

    def __str__(self):
        return f"Avaliação {self.rating}/5 - {self.chat.title}"


# Atualizar o modelo SupportChat para incluir setor e categoria
SupportChat.add_to_class('sector', models.ForeignKey(
    'users.Sector',
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name='support_chats',
    verbose_name="Setor"
))

SupportChat.add_to_class('category', models.ForeignKey(
    SupportCategory,
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name='support_chats',
    verbose_name="Categoria"
))