"""
Nova estrutura para múltiplos arquivos de instrução
"""
from django.db import models
from checklists.models import ChecklistTask
from django.conf import settings

def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None

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
