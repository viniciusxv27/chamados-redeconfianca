from django.conf import settings
from django.db import models


class SimulatorFactorSet(models.Model):
    ROLE_CHOICES = [
        ('consultor', 'Consultor'),
        ('gerente', 'Gerente'),
        ('coordenador', 'Coordenador'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, unique=True)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='simulator_factor_updates',
        verbose_name='Atualizado por',
    )

    class Meta:
        verbose_name = 'Fatores do Simulador'
        verbose_name_plural = 'Fatores do Simulador'

    def __str__(self):
        return f"Fatores ({self.get_role_display()})"


class CoordinatorStoreAccess(models.Model):
    coordinator = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='coordinator_store_access',
        verbose_name='Coordenador',
    )
    sectors = models.ManyToManyField(
        'users.Sector',
        blank=True,
        related_name='coordinator_access',
        verbose_name='Lojas permitidas',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='coordinator_access_updates',
        verbose_name='Atualizado por',
    )

    class Meta:
        verbose_name = 'Acesso de Lojas do Coordenador'
        verbose_name_plural = 'Acessos de Lojas dos Coordenadores'

    def __str__(self):
        return f"Lojas do coordenador: {self.coordinator.get_full_name() or self.coordinator.email}"
