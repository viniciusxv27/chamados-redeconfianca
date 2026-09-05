"""Modelos do módulo Drive.

Os ARQUIVOS moram no Google Drive da empresa — aqui só ficam a camada de
permissão do portal (quem vê o quê), o mapa setor→pasta, os gestores de setor,
favoritos e a trilha de auditoria. Versões e lixeira são as do próprio Google
(revisions/trash), consultadas via API.
"""
from django.conf import settings
from django.db import models

HIERARQUIAS = [
    ('PADRAO', 'Padrão'),
    ('ADMINISTRATIVO', 'Administrativo'),
    ('SUPERVISOR', 'Supervisor'),
    ('ADMIN', 'Administração'),
    ('SUPERADMIN', 'Superadmin'),
]


def credencial_storage():
    """Storage PRIVADO para a chave JSON (S3 com ACL privada; local fora do S3)."""
    from django.conf import settings
    if getattr(settings, 'USE_S3', False):
        from .storage import DriveCredentialStorage
        return DriveCredentialStorage()
    return None


def upload_credencial(instance, filename):
    return 'drive/credenciais/service_account.json'


class DriveConfig(models.Model):
    """Registro único (id=1): liga-desliga, limites e apontamentos do Google."""

    ativo = models.BooleanField(default=True, verbose_name='Módulo Drive ativo')

    # A empresa costuma guardar tudo num Drive Compartilhado (Shared Drive). O
    # ID dele ajuda buscas e uploads a mirarem o lugar certo. Opcional.
    shared_drive_id = models.CharField(
        max_length=100, blank=True, default='', verbose_name='ID do Drive Compartilhado',
        help_text='Se a empresa usa um Drive Compartilhado (Team Drive), cole o ID aqui. Opcional.')

    # Credencial enviada pela tela (em vez do .env). Guardada em storage PRIVADO
    # (nunca público — é uma chave). O cliente do Drive lê daqui primeiro.
    sa_json = models.FileField(
        upload_to=upload_credencial, storage=credencial_storage(), blank=True, null=True,
        verbose_name='Credencial (JSON da conta de serviço)')
    sa_client_email = models.CharField(
        max_length=255, blank=True, default='', verbose_name='E-mail da conta de serviço',
        help_text='Extraído da chave; é com este e-mail que se compartilham as pastas.')
    impersonate_email = models.EmailField(
        blank=True, default='', verbose_name='Impersonar (delegação em todo o domínio)',
        help_text='E-mail de um usuário/admin do Workspace para o Drive enxergar TODOS os '
                  'arquivos da conta. Exige delegação em todo o domínio autorizada no Admin.')

    # ── Limites de arquivo (configuráveis) ──────────────────────────────────
    max_file_mb = models.PositiveIntegerField(
        default=100, verbose_name='Tamanho máximo por arquivo (MB)')
    allowed_extensions = models.TextField(
        default='pdf,doc,docx,xls,xlsx,ppt,pptx,jpg,jpeg,png,csv,txt,zip',
        verbose_name='Extensões permitidas',
        help_text='Separadas por vírgula, sem ponto. Ex.: pdf,docx,xlsx')
    storage_cap_gb = models.PositiveIntegerField(
        default=0, verbose_name='Capacidade máxima (GB)',
        help_text='0 = sem limite pelo portal (vale o limite do próprio Google).')

    # ── Lixeira ─────────────────────────────────────────────────────────────
    trash_retention_days = models.PositiveIntegerField(
        default=30, verbose_name='Dias na lixeira antes da exclusão definitiva')

    # ── Notificações ────────────────────────────────────────────────────────
    notify_new = models.BooleanField(default=True, verbose_name='Avisar novo documento no setor')
    notify_updated = models.BooleanField(default=True, verbose_name='Avisar documento atualizado')

    atualizado_em = models.DateTimeField(auto_now=True)
    atualizado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = 'Configuração do Drive'
        verbose_name_plural = 'Configuração do Drive'

    def __str__(self):
        return 'Configuração do Drive'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def extensoes(self):
        return {e.strip().lower().lstrip('.') for e in (self.allowed_extensions or '').split(',') if e.strip()}

    @property
    def max_file_bytes(self):
        return int(self.max_file_mb) * 1024 * 1024


class SectorDriveMapping(models.Model):
    """Liga um setor do portal à sua pasta-raiz no Google Drive + os gestores."""

    sector = models.OneToOneField(
        'users.Sector', on_delete=models.CASCADE, related_name='drive_mapping', verbose_name='Setor')
    folder_id = models.CharField(
        max_length=100, verbose_name='ID da pasta no Google Drive',
        help_text='O ID que aparece na URL do Drive depois de /folders/.')
    folder_name = models.CharField(max_length=255, blank=True, default='', verbose_name='Nome da pasta (cache)')
    managers = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name='drive_setores_geridos',
        verbose_name='Gestores do setor',
        help_text='Gerenciam todos os arquivos/pastas que os usuários do setor enxergam.')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Setor no Drive'
        verbose_name_plural = 'Setores no Drive'
        ordering = ['sector__name']

    def __str__(self):
        return f'{self.sector.name} → {self.folder_id}'


class DrivePermission(models.Model):
    """Quem enxerga o quê: alvo (usuário/grupo/setor/hierarquia) × pasta × nível.

    ``folder_id`` vazio = vale para a raiz do setor inteiro; preenchido = vale
    para aquela subpasta e tudo abaixo dela. O ``nivel`` é cumulativo (ADMIN >
    DELETE > EDIT > UPLOAD > DOWNLOAD > VIEW).
    """

    class Alvo(models.TextChoices):
        USER = 'USER', 'Usuário'
        GROUP = 'GROUP', 'Grupo'
        SECTOR = 'SECTOR', 'Setor'
        HIERARCHY = 'HIERARCHY', 'Hierarquia'

    class Nivel(models.TextChoices):
        VIEW = 'VIEW', 'Visualizar'
        DOWNLOAD = 'DOWNLOAD', 'Download'
        UPLOAD = 'UPLOAD', 'Upload'
        EDIT = 'EDIT', 'Editar'
        DELETE = 'DELETE', 'Excluir'
        ADMIN = 'ADMIN', 'Administrar'

    mapping = models.ForeignKey(
        SectorDriveMapping, on_delete=models.CASCADE, related_name='permissoes', verbose_name='Setor')
    folder_id = models.CharField(max_length=100, blank=True, default='', verbose_name='Pasta (vazio = setor inteiro)')
    folder_name = models.CharField(max_length=255, blank=True, default='')

    alvo = models.CharField(max_length=10, choices=Alvo.choices)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name='drive_permissoes')
    target_group = models.ForeignKey(
        'communications.CommunicationGroup', on_delete=models.CASCADE, null=True, blank=True,
        related_name='drive_permissoes')
    target_sector = models.ForeignKey(
        'users.Sector', on_delete=models.CASCADE, null=True, blank=True, related_name='drive_permissoes_alvo')
    target_hierarchy = models.CharField(max_length=20, blank=True, default='', choices=HIERARQUIAS)

    nivel = models.CharField(max_length=10, choices=Nivel.choices, default=Nivel.VIEW)

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='drive_permissoes_criadas')
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Permissão do Drive'
        verbose_name_plural = 'Permissões do Drive'
        ordering = ['-criado_em']

    def __str__(self):
        return f'{self.get_alvo_display()} · {self.get_nivel_display()} · {self.mapping.sector.name}'

    @property
    def alvo_label(self):
        if self.alvo == self.Alvo.USER:
            return self.target_user.full_name if self.target_user else '—'
        if self.alvo == self.Alvo.GROUP:
            return self.target_group.name if self.target_group else '—'
        if self.alvo == self.Alvo.SECTOR:
            return self.target_sector.name if self.target_sector else '—'
        if self.alvo == self.Alvo.HIERARCHY:
            return dict(HIERARQUIAS).get(self.target_hierarchy, self.target_hierarchy)
        return '—'


class DriveFavorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='drive_favoritos')
    file_id = models.CharField(max_length=100)
    file_name = models.CharField(max_length=255, blank=True, default='')
    mime_type = models.CharField(max_length=120, blank=True, default='')
    sector = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Favorito do Drive'
        verbose_name_plural = 'Favoritos do Drive'
        ordering = ['-criado_em']
        constraints = [models.UniqueConstraint(fields=['user', 'file_id'], name='uniq_drive_favorito')]

    def __str__(self):
        return f'{self.user} ★ {self.file_name}'


class DriveAuditLog(models.Model):
    """Trilha de auditoria (RF34–36): cada ação crítica, com quem/quando/o quê."""

    class Acao(models.TextChoices):
        VIEW = 'VIEW', 'Visualização'
        DOWNLOAD = 'DOWNLOAD', 'Download'
        UPLOAD = 'UPLOAD', 'Upload'
        EDIT = 'EDIT', 'Alteração'
        DELETE = 'DELETE', 'Exclusão'
        MOVE = 'MOVE', 'Movimentação'
        RENAME = 'RENAME', 'Renomeação'
        RESTORE = 'RESTORE', 'Restauração'
        MKDIR = 'MKDIR', 'Nova pasta'
        VERSION = 'VERSION', 'Nova versão'
        PERM = 'PERM', 'Permissão alterada'
        DENY = 'DENY', 'Acesso negado'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='drive_logs')
    acao = models.CharField(max_length=12, choices=Acao.choices, db_index=True)
    file_id = models.CharField(max_length=100, blank=True, default='', db_index=True)
    file_name = models.CharField(max_length=255, blank=True, default='')
    sector = models.ForeignKey(
        'users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='drive_logs')
    folder_id = models.CharField(max_length=100, blank=True, default='')
    detalhe = models.CharField(max_length=255, blank=True, default='')
    ip = models.GenericIPAddressField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = 'Log do Drive'
        verbose_name_plural = 'Logs do Drive'
        ordering = ['-criado_em']
        indexes = [
            models.Index(fields=['acao', '-criado_em']),
            models.Index(fields=['file_id', '-criado_em']),
            models.Index(fields=['user', '-criado_em']),
        ]

    def __str__(self):
        return f'{self.user} · {self.get_acao_display()} · {self.file_name}'
