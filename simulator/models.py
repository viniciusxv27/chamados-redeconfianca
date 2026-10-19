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


class SniperAssignment(models.Model):
    """Vincula um SNIPER (membro do grupo SNIPER) a um coordenador específico
    (membro do grupo COORDENADORES). O sniper recebe 75% da comissão desse
    coordenador no simulador."""
    sniper = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sniper_assignment',
        verbose_name='Sniper',
    )
    coordinator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sniper_coordinator_assignments',
        verbose_name='Coordenador',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sniper_assignment_updates',
        verbose_name='Atualizado por',
    )

    class Meta:
        verbose_name = 'Atribuição de Sniper'
        verbose_name_plural = 'Atribuições de Snipers'

    def __str__(self):
        sniper_name = self.sniper.get_full_name() or self.sniper.email
        coord_name = self.coordinator.get_full_name() or self.coordinator.email
        return f"Sniper {sniper_name} → Coordenador {coord_name}"


class ProjecaoSimulador(models.Model):
    """A projeção do simulador de cada pessoa, gravada uma vez por dia (08:00).

    É a mesma conta da tabela da rede (/simulator/tabela/, ``simulator/tabela.py``):
    o motor rodado no modo Realizado e no modo Projeção para cada colaborador.
    Cada dia gera um retrato novo — a linha do dia é refeita se rodar de novo.
    """
    referencia = models.DateField('Dia', db_index=True)
    user = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, blank=True,
                             related_name='projecoes_simulador')
    nome = models.CharField(max_length=200)
    papel = models.CharField(max_length=30, blank=True)
    papel_rotulo = models.CharField(max_length=60, blank=True)
    loja = models.CharField(max_length=120, blank=True)
    setor = models.ForeignKey('users.Sector', on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    coordenador = models.CharField(max_length=200, blank=True)
    realizado = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    projecao = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    diferenca = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    motivo = models.CharField(max_length=500, blank=True)
    fatores = models.JSONField(default=list, blank=True)
    tem_dados = models.BooleanField(default=False)
    erro = models.CharField(max_length=200, blank=True)
    gerado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-referencia', '-projecao']
        constraints = [models.UniqueConstraint(fields=['referencia', 'user'], name='projecao_simulador_dia_pessoa')]
        verbose_name = 'Projeção do simulador (diária)'
        verbose_name_plural = 'Projeções do simulador (diárias)'

    def __str__(self):
        return f'{self.referencia:%d/%m/%Y} · {self.nome}: R$ {self.projecao}'
