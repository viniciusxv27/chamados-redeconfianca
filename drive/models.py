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

    # ── Conectar a própria conta Google (OAuth) ─────────────────────────────
    # Conta de serviço só enxerga o que foi compartilhado com ela; para ver
    # TODOS os arquivos de uma conta, a saída documentada do Google é delegação
    # em todo o domínio — que exige Google Workspace. Quem tem uma conta Google
    # comum (gmail.com) não tem Admin console, então esse caminho não existe.
    # Aqui o dono da conta autoriza o portal uma vez pelo consentimento do
    # Google e o portal passa a agir como ele, com acesso ao Meu Drive inteiro.
    class Modo(models.TextChoices):
        SA = 'SA', 'Conta de serviço (pastas compartilhadas)'
        OAUTH = 'OAUTH', 'Minha conta Google (todos os meus arquivos)'

    modo = models.CharField(
        max_length=6, choices=Modo.choices, default=Modo.SA,
        verbose_name='Como o portal acessa o Drive')

    oauth_client_id = models.CharField(
        max_length=255, blank=True, default='', verbose_name='ID do cliente OAuth')
    # Segredos: ficam só no banco e NUNCA voltam para a tela (a tela mostra
    # apenas se estão preenchidos). Mesma regra da chave da conta de serviço.
    oauth_client_secret = models.CharField(
        max_length=255, blank=True, default='', verbose_name='Segredo do cliente OAuth')
    oauth_refresh_token = models.TextField(
        blank=True, default='', verbose_name='Refresh token')
    oauth_email = models.EmailField(
        blank=True, default='', verbose_name='Conta conectada')
    oauth_conectado_em = models.DateTimeField(
        null=True, blank=True, verbose_name='Conectado em')
    oauth_conectado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='Conectado por')

    @property
    def usa_conta_propria(self):
        return self.modo == self.Modo.OAUTH

    @property
    def oauth_pronto(self):
        """Tem tudo para falar com o Google pela conta do dono?"""
        return bool(self.oauth_client_id and self.oauth_client_secret
                    and self.oauth_refresh_token)

    # ── Limites de arquivo (configuráveis) ──────────────────────────────────
    max_file_mb = models.PositiveIntegerField(
        default=100, verbose_name='Tamanho máximo por arquivo (MB)')
    allowed_extensions = models.TextField(
        # heic/heif/webp entram porque é o que o celular tira hoje: sem eles, a
        # foto batida no iPhone volta com "extensão não permitida".
        default='pdf,doc,docx,xls,xlsx,ppt,pptx,jpg,jpeg,png,webp,heic,heif,gif,csv,txt,zip',
        verbose_name='Extensões permitidas',
        help_text='Separadas por vírgula, sem ponto. Ex.: pdf,docx,xlsx. Vazio aceita qualquer uma.')
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

    @property
    def e_pasta_liberada(self):
        return False

    @property
    def rotulo(self):
        return self.sector.name if self.sector_id else (self.folder_name or 'Setor')

    def url_lista(self, folder_id=''):
        """A listagem desta raiz (a pasta do setor ou uma subpasta dela)."""
        from django.urls import reverse
        if folder_id and folder_id != self.folder_id:
            return reverse('drive:browse_folder', args=[self.sector_id, folder_id])
        return reverse('drive:browse', args=[self.sector_id])

    def url_envio(self):
        from django.urls import reverse
        return reverse('drive:upload', args=[self.sector_id])

    def url_nova_pasta(self):
        from django.urls import reverse
        return reverse('drive:mkdir', args=[self.sector_id])


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


class PastaLiberada(models.Model):
    """Uma pasta do Drive liberada direto para alguém, fora do mapa de setores.

    A permissão normal (``DrivePermission``) pendura tudo num setor: a pasta
    precisa estar dentro da pasta-raiz daquele setor. Aqui a pasta é escolhida
    de qualquer lugar do Drive e passa a ser uma **raiz por si só** — aparece em
    /drive para quem recebeu, com o mesmo nível cumulativo das outras
    permissões, e navega como qualquer outra pasta (ela e tudo abaixo dela).

    Serve para o caso de sempre: "esta pasta aqui, só para esta pessoa", sem
    precisar inventar um setor no portal para ela.
    """

    class Alvo(models.TextChoices):
        USER = 'USER', 'Usuário'
        GROUP = 'GROUP', 'Grupo'
        SECTOR = 'SECTOR', 'Setor'
        HIERARCHY = 'HIERARCHY', 'Hierarquia'

    folder_id = models.CharField(max_length=100, verbose_name='Pasta no Google Drive')
    folder_name = models.CharField(max_length=255, blank=True, default='', verbose_name='Nome da pasta')
    caminho = models.CharField(
        max_length=500, blank=True, default='', verbose_name='Onde fica',
        help_text='Caminho da pasta no Drive, guardado para a tela mostrar de onde ela veio.')

    alvo = models.CharField(max_length=10, choices=Alvo.choices, default=Alvo.USER)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name='drive_pastas_liberadas', verbose_name='Pessoa')
    target_group = models.ForeignKey(
        'communications.CommunicationGroup', on_delete=models.CASCADE, null=True, blank=True,
        related_name='drive_pastas_liberadas', verbose_name='Grupo')
    target_sector = models.ForeignKey(
        'users.Sector', on_delete=models.CASCADE, null=True, blank=True,
        related_name='drive_pastas_liberadas', verbose_name='Setor')
    target_hierarchy = models.CharField(max_length=20, blank=True, default='', choices=HIERARQUIAS)

    nivel = models.CharField(max_length=10, choices=DrivePermission.Nivel.choices,
                             default=DrivePermission.Nivel.VIEW, verbose_name='Nível')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='drive_pastas_liberadas_criadas')
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pasta liberada do Drive'
        verbose_name_plural = 'Pastas liberadas do Drive'
        ordering = ['folder_name', '-criado_em']
        indexes = [models.Index(fields=['ativo', 'alvo'])]

    def __str__(self):
        return f'{self.folder_name or self.folder_id} → {self.alvo_label}'

    # ── Faz as vezes de um mapeamento de setor ──────────────────────────────
    # O motor de arquivos do Drive (prévia, download, versões, renomear…)
    # trabalha com "a raiz de onde o arquivo veio". Uma pasta liberada é uma
    # raiz sem setor: responde às mesmas perguntas, com setor vazio.
    @property
    def sector(self):
        return None

    @property
    def sector_id(self):
        return None

    @property
    def e_pasta_liberada(self):
        return True

    @property
    def rotulo(self):
        return self.folder_name or 'Pasta liberada'

    def url_lista(self, folder_id=''):
        from django.urls import reverse
        if folder_id and folder_id != self.folder_id:
            return reverse('drive:browse_pasta_folder', args=[self.id, folder_id])
        return reverse('drive:browse_pasta', args=[self.id])

    def url_envio(self):
        from django.urls import reverse
        return reverse('drive:upload_pasta', args=[self.id])

    def url_nova_pasta(self):
        from django.urls import reverse
        return reverse('drive:mkdir_pasta', args=[self.id])

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
    # Favorito de dentro de uma pasta liberada: guarda a raiz para o link da
    # tela de favoritos voltar para lá (sem setor, ele iria parar no Meu Drive).
    pasta = models.ForeignKey('drive.PastaLiberada', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='favoritos', verbose_name='Pasta liberada')
    criado_em = models.DateTimeField(auto_now_add=True)

    @property
    def e_pasta(self):
        """Favorito de pasta abre a listagem dela, não a prévia de arquivo."""
        return self.mime_type == 'application/vnd.google-apps.folder'

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
        USO_LOCAL = 'USO_LOCAL', 'Uso local'

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


class EdicaoLocal(models.Model):
    """Um arquivo do Drive aberto para edição no computador de alguém.

    O token (aqui só o hash) vale para ESTE arquivo e ESTA pessoa, por tempo
    limitado. Guarda a trava do Office enquanto o arquivo está aberto e o md5 da
    versão que a pessoa abriu — para um salvamento não passar por cima da
    alteração de outra pessoa. Ver drive/edicao_local.py.
    """

    class Modo(models.TextChoices):
        OFFICE = 'office', 'No Office (Word, Excel, PowerPoint)'
        ARQUIVO = 'arquivo', 'Cópia no computador'

    token_hash = models.CharField(max_length=64, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='drive_edicoes_locais')
    file_id = models.CharField(max_length=100, db_index=True)
    file_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=120, blank=True, default='')
    sector = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    meu_drive = models.BooleanField(default=False)
    modo = models.CharField(max_length=10, choices=Modo.choices)
    pode_salvar = models.BooleanField(default=False)
    md5_base = models.CharField(max_length=64, blank=True, default='')

    criado_em = models.DateTimeField(auto_now_add=True)
    expira_em = models.DateTimeField()
    encerrado_em = models.DateTimeField(null=True, blank=True)
    aberto_em = models.DateTimeField(null=True, blank=True)
    permissao_conferida_em = models.DateTimeField(null=True, blank=True)

    lock_token = models.CharField(max_length=80, blank=True, default='')
    lock_expira_em = models.DateTimeField(null=True, blank=True)

    salvamentos = models.PositiveIntegerField(default=0)
    ultimo_salvamento_em = models.DateTimeField(null=True, blank=True)
    conflito_file_id = models.CharField(max_length=100, blank=True, default='')
    ultimo_conflito_nome = models.CharField(max_length=255, blank=True, default='')
    ultimo_conflito_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Edição no computador'
        verbose_name_plural = 'Edições no computador'
        ordering = ['-criado_em']
        indexes = [models.Index(fields=['file_id', 'lock_expira_em'])]

    def __str__(self):
        return f'{self.user} · {self.file_name} · {self.get_modo_display()}'
