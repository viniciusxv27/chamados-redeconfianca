from django.db import models
from django.contrib.auth import get_user_model
from django.conf import settings
from users.models import User, Sector
import os

User = get_user_model()

def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


def upload_file_path(instance, filename):
    """Gera o caminho para upload do arquivo"""
    return f'files/{instance.category}/{filename}'


class FileCategory(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nome")
    description = models.TextField(blank=True, verbose_name="Descrição")
    icon = models.CharField(max_length=50, default='fas fa-file', verbose_name="Ícone")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Categoria de Arquivo"
        verbose_name_plural = "Categorias de Arquivos"
        ordering = ['name']
    
    def __str__(self):
        return self.name


class SharedFile(models.Model):
    VISIBILITY_CHOICES = [
        ('ALL', 'Todos os usuários'),
        ('SECTOR', 'Usuários do setor'),
        ('USER', 'Usuário específico'),
    ]
    
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(blank=True, verbose_name="Descrição")
    file = models.FileField(upload_to=upload_file_path, storage=get_media_storage(), verbose_name="Arquivo")
    category = models.ForeignKey(FileCategory, on_delete=models.CASCADE, verbose_name="Categoria")
    
    # Controle de visibilidade
    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default='ALL', verbose_name="Visibilidade")
    target_sector = models.ForeignKey(Sector, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Setor alvo")
    target_user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='targeted_files', verbose_name="Usuário alvo")
    
    # Metadados
    uploaded_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='uploaded_files', verbose_name="Enviado por")
    file_size = models.PositiveIntegerField(verbose_name="Tamanho do arquivo (bytes)", null=True, blank=True)
    downloads = models.PositiveIntegerField(default=0, verbose_name="Downloads")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Arquivo Compartilhado"
        verbose_name_plural = "Arquivos Compartilhados"
        ordering = ['-created_at']
    
    def __str__(self):
        return self.title
    
    @property
    def file_extension(self):
        """Retorna a extensão do arquivo"""
        return os.path.splitext(self.file.name)[1].lower()
    
    @property
    def file_size_formatted(self):
        """Retorna o tamanho do arquivo formatado"""
        if not self.file_size:
            return "N/A"
        
        size = self.file_size
        for unit in ['bytes', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    
    def can_be_viewed_by(self, user):
        """Verifica se o usuário pode ver este arquivo"""
        if not self.is_active:
            return False
            
        if self.visibility == 'ALL':
            return True
        elif self.visibility == 'SECTOR' and self.target_sector:
            return user.is_in_sector(self.target_sector)
        elif self.visibility == 'USER' and self.target_user:
            return user == self.target_user
        
        return False
    
    def increment_downloads(self):
        """Incrementa o contador de downloads"""
        self.downloads += 1
        self.save(update_fields=['downloads'])


class FileDownload(models.Model):
    """Log de downloads de arquivos"""
    file = models.ForeignKey(SharedFile, on_delete=models.CASCADE, related_name='download_logs')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    downloaded_at = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    
    class Meta:
        verbose_name = "Log de Download"
        verbose_name_plural = "Logs de Downloads"
        ordering = ['-downloaded_at']
    
    def __str__(self):
        return f"{self.user.full_name} - {self.file.title} - {self.downloaded_at}"
