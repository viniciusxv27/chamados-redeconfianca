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


class Lixeira(models.Model):
    """Excluir em /files/ nunca apaga: marca quando, quem e em que lote.

    O lote junta o que saiu de uma vez (uma pasta leva as subpastas, categorias e
    arquivos dela) para o SUPERADMIN recuperar tudo junto. O arquivo continua no
    storage — só some das telas.
    """
    excluido_em = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name='Excluído em')
    excluido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
        verbose_name='Excluído por')
    lote_exclusao = models.CharField(max_length=32, blank=True, db_index=True, verbose_name='Lote da exclusão')

    class Meta:
        abstract = True

    @property
    def na_lixeira(self):
        return self.excluido_em is not None


def upload_file_path(instance, filename):
    """Gera o caminho para upload do arquivo"""
    return f'files/{instance.category}/{filename}'


class Folder(Lixeira):
    """Modelo para organizar arquivos em pastas"""
    name = models.CharField(max_length=100, verbose_name="Nome da Pasta")
    description = models.TextField(blank=True, verbose_name="Descrição")
    icon = models.CharField(max_length=50, default='fas fa-folder', verbose_name="Ícone")
    color = models.CharField(max_length=7, default='#3B82F6', verbose_name="Cor", help_text="Cor em hexadecimal")
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subfolders', verbose_name="Pasta Pai")
    
    # Controle de visibilidade (herda para as categorias)
    visibility = models.CharField(max_length=10, choices=[
        ('ALL', 'Todos os usuários'),
        ('SECTOR', 'Usuários do setor'),
        ('ADMIN', 'Apenas administradores'),
    ], default='ALL', verbose_name="Visibilidade")
    target_sector = models.ForeignKey(Sector, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Setor alvo")
    
    # Acesso por pessoa (SUPERADMIN): além da visibilidade acima, estas pessoas veem a pasta.
    allowed_users = models.ManyToManyField(User, blank=True, related_name='pastas_liberadas',
                                           verbose_name="Pessoas com acesso")

    created_by = models.ForeignKey(User, on_delete=models.CASCADE, verbose_name="Criado por")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    order = models.PositiveIntegerField(default=0, verbose_name="Ordem")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pasta"
        verbose_name_plural = "Pastas"
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.name
    
    def get_full_path(self):
        """Retorna o caminho completo da pasta"""
        if self.parent:
            return f"{self.parent.get_full_path()} / {self.name}"
        return self.name
    
    def can_be_viewed_by(self, user):
        """Verifica se o usuário pode ver esta pasta"""
        if not self.is_active or self.na_lixeira:
            return False
        if self.allowed_users.filter(pk=getattr(user, 'pk', None)).exists():
            return True

        if self.visibility == 'ALL':
            return True
        elif self.visibility == 'SECTOR' and self.target_sector:
            return user.is_in_sector(self.target_sector)
        elif self.visibility == 'ADMIN':
            return user.can_access_admin_panel()
        
        return False


class FileCategory(Lixeira):
    name = models.CharField(max_length=100, verbose_name="Nome")
    description = models.TextField(blank=True, verbose_name="Descrição")
    icon = models.CharField(max_length=50, default='fas fa-file', verbose_name="Ícone")
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, null=True, blank=True, related_name='categories', verbose_name="Pasta")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    order = models.PositiveIntegerField(default=0, verbose_name="Ordem")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Categoria de Arquivo"
        verbose_name_plural = "Categorias de Arquivos"
        ordering = ['order', 'name']
    
    def __str__(self):
        return self.name
    
    def can_be_viewed_by(self, user):
        """Verifica se o usuário pode ver esta categoria (baseado na pasta)"""
        if not self.is_active or self.na_lixeira:
            return False
        
        # Se tem pasta, verifica a permissão da pasta
        if self.folder:
            return self.folder.can_be_viewed_by(user)
        
        # Se não tem pasta, é visível para todos
        return True


class SharedFile(Lixeira):
    VISIBILITY_CHOICES = [
        ('ALL', 'Todos os usuários'),
        ('SECTOR', 'Usuários do setor'),
        ('GROUP', 'Grupo específico'),
        ('USER', 'Usuário específico'),
    ]
    
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(blank=True, verbose_name="Descrição")
    file = models.FileField(upload_to=upload_file_path, storage=get_media_storage(), verbose_name="Arquivo")
    category = models.ForeignKey(FileCategory, on_delete=models.CASCADE, verbose_name="Categoria")
    
    # Controle de visibilidade
    visibility = models.CharField(max_length=10, choices=VISIBILITY_CHOICES, default='ALL', verbose_name="Visibilidade")
    target_sector = models.ForeignKey(Sector, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Setor alvo")
    target_group = models.ForeignKey('communications.CommunicationGroup', on_delete=models.CASCADE, null=True, blank=True, verbose_name="Grupo alvo")
    target_user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name='targeted_files', verbose_name="Usuário alvo")
    # Acesso por pessoa (SUPERADMIN): além da visibilidade acima, estas pessoas veem o arquivo.
    allowed_users = models.ManyToManyField(User, blank=True, related_name='arquivos_liberados',
                                           verbose_name="Pessoas com acesso")
    
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
        """Verifica se o usuário pode ver este arquivo (a regra do próprio arquivo)"""
        if not self.is_active or self.na_lixeira:
            return False
        if self.allowed_users.filter(pk=getattr(user, 'pk', None)).exists():
            return True
            
        if self.visibility == 'ALL':
            return True
        elif self.visibility == 'SECTOR' and self.target_sector:
            return user.is_in_sector(self.target_sector)
        elif self.visibility == 'GROUP' and self.target_group:
            return user in self.target_group.members.all()
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


class MovimentacaoArquivo(models.Model):
    """Tudo o que acontece em /files/, para o SUPERADMIN acompanhar.

    Guarda o nome do item no momento (o item pode ser movido, renomeado ou ir para
    a lixeira depois) e um resumo legível do que mudou.
    """

    class Acao(models.TextChoices):
        ENVIO = 'ENVIO', 'Envio de arquivo'
        DOWNLOAD = 'DOWNLOAD', 'Download / visualização'
        CRIAR_PASTA = 'CRIAR_PASTA', 'Pasta criada'
        CRIAR_CATEGORIA = 'CRIAR_CATEGORIA', 'Categoria criada'
        MOVER = 'MOVER', 'Arquivo movido'
        EXCLUIR = 'EXCLUIR', 'Enviado para a lixeira'
        RESTAURAR = 'RESTAURAR', 'Recuperado da lixeira'
        LIBERAR = 'LIBERAR', 'Acesso liberado para pessoa'
        RETIRAR = 'RETIRAR', 'Acesso retirado de pessoa'
        NEGADO = 'NEGADO', 'Tentativa sem permissão'

    class Tipo(models.TextChoices):
        ARQUIVO = 'ARQUIVO', 'Arquivo'
        PASTA = 'PASTA', 'Pasta'
        CATEGORIA = 'CATEGORIA', 'Categoria'

    acao = models.CharField(max_length=20, choices=Acao.choices, db_index=True, verbose_name='Ação')
    tipo = models.CharField(max_length=10, choices=Tipo.choices, verbose_name='Tipo do item')
    item_id = models.PositiveIntegerField(null=True, blank=True, db_index=True, verbose_name='ID do item')
    item_nome = models.CharField(max_length=255, blank=True, verbose_name='Item')
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='movimentacoes_arquivos', verbose_name='Quem')
    pessoa = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='+', verbose_name='Pessoa afetada',
                               help_text='Em liberar/retirar acesso: a quem.')
    detalhe = models.TextField(blank=True, verbose_name='Detalhe')
    ip = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP')
    quando = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Quando')

    class Meta:
        verbose_name = 'Movimentação de arquivo'
        verbose_name_plural = 'Movimentações de arquivos'
        ordering = ['-quando', '-id']

    def __str__(self):
        return f'{self.get_acao_display()} — {self.item_nome}'
