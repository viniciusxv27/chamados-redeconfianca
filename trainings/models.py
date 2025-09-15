from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import FileExtensionValidator
from django.urls import reverse
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
import os
import uuid

User = get_user_model()

def get_training_storage():
    """Return training storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import TrainingStorage
        return TrainingStorage
    return None

def training_video_path(instance, filename):
    """Função para definir o path de upload dos vídeos de treinamento"""
    # Gera nome aleatório para evitar problemas com caracteres especiais
    ext = os.path.splitext(filename)[1].lower()
    random_name = str(uuid.uuid4()).replace('-', '')
    
    # Usa UUID temporário se instance ainda não tem ID
    identifier = instance.id if instance.id else str(uuid.uuid4())[:8]
    return f'trainings/videos/{identifier}/{random_name}{ext}'

def training_thumbnail_path(instance, filename):
    """Função para definir o path de upload das thumbnails de treinamento"""
    # Gera nome aleatório para evitar problemas com caracteres especiais
    ext = os.path.splitext(filename)[1].lower()
    random_name = str(uuid.uuid4()).replace('-', '')
    
    # Usa UUID temporário se instance ainda não tem ID
    identifier = instance.id if instance.id else str(uuid.uuid4())[:8]
    return f'trainings/thumbnails/{identifier}/{random_name}{ext}'

class Training(models.Model):
    """Modelo para treinamentos com upload de vídeos"""
    
    title = models.CharField(
        max_length=200,
        verbose_name="Título",
        help_text="Título do treinamento"
    )
    
    description = models.TextField(
        verbose_name="Descrição",
        help_text="Descrição detalhada do treinamento"
    )
    
    video_file = models.FileField(
        upload_to=training_video_path,
        storage=get_training_storage(),
        validators=[
            FileExtensionValidator(
                allowed_extensions=['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv']
            )
        ],
        verbose_name="Arquivo de Vídeo",
        help_text="Formatos aceitos: MP4, AVI, MOV, WMV, FLV, WebM, MKV"
    )
    
    thumbnail = models.ImageField(
        upload_to=training_thumbnail_path,
        storage=get_training_storage(),
        blank=True,
        null=True,
        verbose_name="Miniatura",
        help_text="Imagem de miniatura para o vídeo (opcional)"
    )
    
    duration_seconds = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Duração (segundos)",
        help_text="Duração do vídeo em segundos"
    )
    
    file_size = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="Tamanho do Arquivo",
        help_text="Tamanho do arquivo em bytes"
    )
    
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        verbose_name="Enviado por",
        related_name="uploaded_trainings"
    )
    
    is_active = models.BooleanField(
        default=True,
        verbose_name="Ativo",
        help_text="Se o treinamento está disponível para visualização"
    )
    
    views_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Número de Visualizações"
    )
    
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Criado em"
    )
    
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Atualizado em"
    )
    
    class Meta:
        verbose_name = "Treinamento"
        verbose_name_plural = "Treinamentos"
        ordering = ['-created_at']
        
    def __str__(self):
        return self.title
    
    def get_absolute_url(self):
        return reverse('training_detail', kwargs={'pk': self.pk})
    
    def get_duration_display(self):
        """Retorna a duração formatada em MM:SS ou HH:MM:SS"""
        if not self.duration_seconds:
            return "Duração não informada"
        
        hours = self.duration_seconds // 3600
        minutes = (self.duration_seconds % 3600) // 60
        seconds = self.duration_seconds % 60
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes:02d}:{seconds:02d}"
    
    def get_file_size_display(self):
        """Retorna o tamanho do arquivo formatado"""
        if not self.file_size:
            return "Tamanho não informado"
        
        # Converter bytes para MB
        size_mb = self.file_size / (1024 * 1024)
        
        if size_mb < 1024:
            return f"{size_mb:.1f} MB"
        else:
            size_gb = size_mb / 1024
            return f"{size_gb:.1f} GB"
    
    def get_video_url(self):
        """Retorna a URL do vídeo de forma segura"""
        try:
            if self.video_file:
                return self.video_file.url
        except Exception:
            pass
        return None
    
    def get_thumbnail_url(self):
        """Retorna a URL da thumbnail de forma segura"""
        try:
            if self.thumbnail:
                return self.thumbnail.url
        except Exception:
            pass
        return None


@receiver(post_save, sender=Training)
def move_training_files_after_save(sender, instance, created, **kwargs):
    """
    Move arquivos para o path correto após salvar com ID definido
    """
    if created and instance.id:
        # Verifica se precisa mover arquivos do path temporário
        updated = False
        
        # Verificar e mover vídeo
        if instance.video_file:
            current_path = instance.video_file.name
            if '/temp/' in current_path or not f'/{instance.id}/' in current_path:
                # Gerar novo path correto
                filename = os.path.basename(current_path)
                name, ext = os.path.splitext(filename)
                safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                new_path = f'trainings/videos/{instance.id}/{safe_name}{ext}'
                
                # Mover arquivo no storage
                storage = instance.video_file.storage
                if storage.exists(current_path):
                    try:
                        # Copiar conteúdo para novo path
                        with storage.open(current_path, 'rb') as old_file:
                            storage.save(new_path, old_file)
                        # Remover arquivo antigo
                        storage.delete(current_path)
                        # Atualizar campo
                        instance.video_file.name = new_path
                        updated = True
                    except Exception as e:
                        print(f"Erro ao mover vídeo: {e}")
        
        # Verificar e mover thumbnail
        if instance.thumbnail:
            current_path = instance.thumbnail.name
            if '/temp/' in current_path or not f'/{instance.id}/' in current_path:
                # Gerar novo path correto
                filename = os.path.basename(current_path)
                name, ext = os.path.splitext(filename)
                safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).rstrip()
                new_path = f'trainings/thumbnails/{instance.id}/{safe_name}{ext}'
                
                # Mover arquivo no storage
                storage = instance.thumbnail.storage
                if storage.exists(current_path):
                    try:
                        # Copiar conteúdo para novo path
                        with storage.open(current_path, 'rb') as old_file:
                            storage.save(new_path, old_file)
                        # Remover arquivo antigo
                        storage.delete(current_path)
                        # Atualizar campo
                        instance.thumbnail.name = new_path
                        updated = True
                    except Exception as e:
                        print(f"Erro ao mover thumbnail: {e}")
        
        # Salvar alterações se necessário (sem trigger do signal novamente)
        if updated:
            Training.objects.filter(pk=instance.pk).update(
                video_file=instance.video_file.name,
                thumbnail=instance.thumbnail.name if instance.thumbnail else None
            )


class TrainingView(models.Model):
    """Modelo para registrar visualizações de treinamentos"""
    
    training = models.ForeignKey(
        Training,
        on_delete=models.CASCADE,
        related_name="training_views"
    )
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="training_views"
    )
    
    viewed_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Visualizado em"
    )
    
    duration_watched = models.PositiveIntegerField(
        default=0,
        verbose_name="Duração assistida (segundos)",
        help_text="Quantos segundos do vídeo foram assistidos"
    )
    
    completed = models.BooleanField(
        default=False,
        verbose_name="Completado",
        help_text="Se o usuário assistiu ao vídeo completo"
    )
    
    class Meta:
        verbose_name = "Visualização de Treinamento"
        verbose_name_plural = "Visualizações de Treinamentos"
        unique_together = ['training', 'user']
        
    def __str__(self):
        return f"{self.user.username} - {self.training.title}"
