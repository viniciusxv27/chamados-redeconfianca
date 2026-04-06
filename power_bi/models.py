from django.contrib.auth.models import Group
from django.db import models

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
    allowed_groups = models.ManyToManyField(
        Group,
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

        if self.allowed_groups.filter(id__in=user.groups.values_list('id', flat=True)).exists():
            return True

        user_sector_ids = list(user.sectors.values_list('id', flat=True))
        if user.sector_id:
            user_sector_ids.append(user.sector_id)

        if user_sector_ids and self.allowed_sectors.filter(id__in=user_sector_ids).exists():
            return True

        if user.hierarchy in (self.allowed_hierarchies or []):
            return True

        return False
