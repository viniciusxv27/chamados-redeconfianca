from django.db import models

from communications.models import CommunicationGroup
from users.models import Sector, User


class PowerBIReport(models.Model):
    HIERARCHY_CHOICES = User.HIERARCHY_CHOICES

    name = models.CharField(max_length=120, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descricao')
    icon_class = models.CharField(
        max_length=80,
        default='fas fa-chart-line',
        verbose_name='Icone (classe Font Awesome)'
    )
    embed_url = models.URLField(max_length=1000, verbose_name='Link do Power BI (embed)')
    allow_open_in_new_tab = models.BooleanField(default=False, verbose_name='Nova guia permitida')
    allowed_groups = models.ManyToManyField(
        CommunicationGroup,
        blank=True,
        related_name='power_bi_reports',
        verbose_name='Grupos permitidos'
    )
    allowed_sectors = models.ManyToManyField(
        Sector,
        blank=True,
        related_name='power_bi_reports',
        verbose_name='Setores permitidos'
    )
    allowed_users = models.ManyToManyField(
        User,
        blank=True,
        related_name='power_bi_reports',
        verbose_name='Usuarios permitidos'
    )
    allowed_hierarchies = models.JSONField(default=list, blank=True, verbose_name='Hierarquias permitidas')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    sort_order = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Relatorio Power BI'
        verbose_name_plural = 'Relatorios Power BI'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name

    def has_visibility_rules(self):
        return (
            self.allowed_groups.exists()
            or self.allowed_sectors.exists()
            or self.allowed_users.exists()
            or bool(self.allowed_hierarchies)
        )

    def is_visible_to(self, user):
        if not self.is_active:
            return False

        if user.is_superuser or user.hierarchy == 'SUPERADMIN':
            return True

        if not self.has_visibility_rules():
            return True

        if self.allowed_users.filter(id=user.id).exists():
            return True

        if self.allowed_groups.filter(id__in=user.communication_groups.values_list('id', flat=True)).exists():
            return True

        user_sector_ids = list(user.sectors.values_list('id', flat=True))
        if user.sector_id:
            user_sector_ids.append(user.sector_id)

        if user_sector_ids and self.allowed_sectors.filter(id__in=user_sector_ids).exists():
            return True

        if user.hierarchy in (self.allowed_hierarchies or []):
            return True

        return False


class GoalUpload(models.Model):
    year = models.PositiveSmallIntegerField(verbose_name='Ano')
    month = models.PositiveSmallIntegerField(verbose_name='Mes')
    fixa_as_percentage = models.BooleanField(default=False, verbose_name='FIXA em percentual')
    source_file_name = models.CharField(max_length=255, blank=True, verbose_name='Arquivo origem')
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='goal_uploads',
        verbose_name='Enviado por'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Carga de Metas'
        verbose_name_plural = 'Cargas de Metas'
        ordering = ['-year', '-month', '-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['year', 'month'], name='unique_goal_upload_by_month_year')
        ]

    def __str__(self):
        return f'{self.month:02d}/{self.year}'


class GoalEntry(models.Model):
    SHEET_CN_REAL = 'METAS_CN_REAL'
    SHEET_PDV_REAL = 'META_PDV_REAL'
    SHEET_CHOICES = [
        (SHEET_CN_REAL, 'METAS CN REAL'),
        (SHEET_PDV_REAL, 'META PDV REAL'),
    ]

    upload = models.ForeignKey(
        GoalUpload,
        on_delete=models.CASCADE,
        related_name='entries',
        verbose_name='Carga'
    )
    sheet_type = models.CharField(max_length=20, choices=SHEET_CHOICES, verbose_name='Sheet')
    user_name = models.CharField(max_length=255, blank=True, verbose_name='Nome do usuario')
    store_name = models.CharField(max_length=255, blank=True, verbose_name='Loja')
    pilar = models.CharField(max_length=255, blank=True, verbose_name='Pilar')
    goal_value = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, verbose_name='Meta')
    row_number = models.PositiveIntegerField(default=0, verbose_name='Linha na planilha')
    row_data = models.JSONField(default=dict, blank=True, verbose_name='Dados da linha')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Item de Meta'
        verbose_name_plural = 'Itens de Metas'
        ordering = ['sheet_type', 'row_number']
        indexes = [
            models.Index(fields=['sheet_type']),
            models.Index(fields=['user_name']),
            models.Index(fields=['store_name']),
            models.Index(fields=['pilar']),
        ]

    def __str__(self):
        return f'{self.upload} - {self.sheet_type} - {self.user_name or self.store_name or "sem identificacao"}'


class PowerBIAccessLog(models.Model):
    report = models.ForeignKey(
        PowerBIReport,
        on_delete=models.CASCADE,
        related_name='access_logs',
        verbose_name='Relatorio'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='power_bi_access_logs',
        verbose_name='Usuario'
    )
    accessed_at = models.DateTimeField(auto_now_add=True, verbose_name='Data de acesso')

    class Meta:
        verbose_name = 'Log de Acesso Power BI'
        verbose_name_plural = 'Logs de Acesso Power BI'
        ordering = ['-accessed_at']
        indexes = [
            models.Index(fields=['accessed_at']),
            models.Index(fields=['user', 'accessed_at']),
            models.Index(fields=['report', 'accessed_at']),
        ]

    def __str__(self):
        return f'{self.user} -> {self.report} ({self.accessed_at:%d/%m/%Y %H:%M})'
