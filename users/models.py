from django.contrib.auth.models import AbstractUser
from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from core.utils import upload_user_profile_photo


def normalize_cpf(value):
    """Normalize CPF to 11 numeric digits (00000000000)."""
    if not value:
        return ''
    digits = ''.join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return ''
    return digits[:11].zfill(11)


class SystemConfig(models.Model):
    """
    Modelo para armazenar configurações do sistema.
    Usa Singleton pattern - sempre haverá apenas uma instância.
    """
    # URLs das Planilhas de Comissionamento
    excel_comissao_url = models.URLField(
        max_length=500,
        verbose_name="Planilha de Comissionamento",
        help_text="URL de compartilhamento do OneDrive para a planilha de comissões",
        default="https://1drv.ms/x/c/871ee1819c7e2faa/IQDp2ONpM_88ToSopGzjlPVdASX6rIFB3_ENXnJcGN3e7Go?e=Q3fDRz"
    )
    excel_vendas_url = models.URLField(
        max_length=500,
        verbose_name="Planilha de Vendas e Metas",
        help_text="URL de compartilhamento do OneDrive para vendas e metas",
        default="https://1drv.ms/x/c/871ee1819c7e2faa/IQAVeQ-dgEiBTYG0UlK7URSLAQ5r634qBo9-GicO2D8ZfmY"
    )
    excel_base_pagamento_url = models.URLField(
        max_length=500,
        verbose_name="Planilha BASE_PAGAMENTO",
        help_text="URL de compartilhamento do OneDrive para base de pagamento",
        default="https://1drv.ms/x/c/871ee1819c7e2faa/IQBHZkNccF89Tb0x1dXfoLhiAT8Q5C_fzHlIyUnc2L2FJVs?e=vAO4OX"
    )
    excel_base_exclusao_url = models.URLField(
        max_length=500,
        verbose_name="Planilha BASE_EXCLUSAO (Comissionamento)",
        help_text="URL de compartilhamento do OneDrive para base de exclusão do comissionamento",
        default="https://1drv.ms/x/c/871ee1819c7e2faa/IQBryBteOg4sS4cBwU1tIgKoATfi6qmYB8eRrIaTpyP8Qhc?e=pye3Sj"
    )
    excel_contestacao_base_exclusao_url = models.URLField(
        max_length=500,
        verbose_name="Planilha BASE_EXCLUSAO (Contestação)",
        help_text="URL de compartilhamento do OneDrive para base de exclusão da contestação",
        default="https://1drv.ms/x/c/871ee1819c7e2faa/IQBryBteOg4sS4cBwU1tIgKoATfi6qmYB8eRrIaTpyP8Qhc?e=pye3Sj"
    )
    contestacao_global_managers = models.ManyToManyField(
        'User',
        blank=True,
        related_name='contestacao_global_access_configs',
        verbose_name='Gestores Globais de Contestação',
        help_text='Usuários liberados para gerenciar tudo em /contestacao'
    )
    display_reference_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Mês de Exibição do Comissionamento",
        help_text="Mês de referência padrão exibido no /users/commission/ quando não houver seleção manual"
    )
    display_reference_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        verbose_name="Ano de Exibição do Comissionamento",
        help_text="Ano de referência padrão exibido no /users/commission/ quando não houver seleção manual"
    )
    
    # Metadados
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última atualização")
    updated_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='config_updates',
        verbose_name="Atualizado por"
    )
    
    class Meta:
        verbose_name = "Configuração do Sistema"
        verbose_name_plural = "Configurações do Sistema"
    
    def __str__(self):
        return "Configurações do Sistema"
    
    def save(self, *args, **kwargs):
        # Garantir que só existe uma instância (Singleton)
        self.pk = 1
        super().save(*args, **kwargs)
    
    @classmethod
    def get_config(cls):
        """Retorna a instância de configuração (cria se não existir)"""
        config, created = cls.objects.get_or_create(pk=1)
        return config

    def get_display_reference_month_year(self, base_date=None):
        """Retorna o mês/ano padrão de exibição configurado; fallback para regra de referência."""
        month = self.display_reference_month
        year = self.display_reference_year

        if month and year and 1 <= month <= 12:
            return year, month

        return CommissionSpreadsheetVersion.get_reference_month_year(base_date=base_date)


class CommissionSpreadsheetVersion(models.Model):
    """Versões das planilhas de comissionamento por mês/ano de referência."""

    year = models.PositiveSmallIntegerField(verbose_name="Ano de Referência")
    month = models.PositiveSmallIntegerField(verbose_name="Mês de Referência")

    excel_comissao_url = models.URLField(
        max_length=500,
        verbose_name="Planilha de Comissionamento",
        help_text="URL de compartilhamento do OneDrive para a planilha de comissões",
    )
    excel_vendas_url = models.URLField(
        max_length=500,
        verbose_name="Planilha de Vendas e Metas",
        help_text="URL de compartilhamento do OneDrive para vendas e metas",
    )
    excel_base_pagamento_url = models.URLField(
        max_length=500,
        verbose_name="Planilha BASE_PAGAMENTO",
        help_text="URL de compartilhamento do OneDrive para base de pagamento",
    )
    excel_base_exclusao_url = models.URLField(
        max_length=500,
        verbose_name="Planilha BASE_EXCLUSAO (Comissionamento)",
        help_text="URL de compartilhamento do OneDrive para base de exclusão do comissionamento",
    )
    CONTESTACAO_PHASE_CHOICES = [
        ('antes', 'Antes da Contestação'),
        ('pos', 'Pós Contestação'),
    ]
    contestacao_phase = models.CharField(
        max_length=10,
        choices=CONTESTACAO_PHASE_CHOICES,
        default='pos',
        verbose_name='Fase de Contestação',
        help_text='Indica se a versão é antes ou pós contestação',
    )

    STATUS_DRAFT = 'draft'
    STATUS_RELEASED = 'released'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Rascunho'),
        (STATUS_RELEASED, 'Liberada'),
    ]
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
        db_index=True,
        verbose_name='Situação',
        help_text='Rascunho fica visível apenas para superadmins; só vai ao ar quando liberada.',
    )
    released_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='commission_version_releases',
        verbose_name='Liberada por',
    )
    released_at = models.DateTimeField(null=True, blank=True, verbose_name='Liberada em')

    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última atualização")
    updated_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='commission_version_updates',
        verbose_name="Atualizado por"
    )

    class Meta:
        verbose_name = "Versão de Planilha de Comissionamento"
        verbose_name_plural = "Versões de Planilhas de Comissionamento"
        ordering = ['-year', '-month']
        constraints = [
            models.UniqueConstraint(
                fields=['year', 'month', 'contestacao_phase'],
                name='unique_commission_version_by_month_year_phase',
            )
        ]

    def __str__(self):
        return f"{self.month:02d}/{self.year} ({self.get_contestacao_phase_display()})"

    @property
    def is_released(self):
        return self.status == self.STATUS_RELEASED

    @staticmethod
    def get_reference_month_year(base_date=None):
        """Regra de referência: sempre 2 meses atrás."""
        current = (base_date or timezone.now()).date()
        month = current.month - 2
        year = current.year
        while month <= 0:
            month += 12
            year -= 1
        return year, month

    @classmethod
    def get_reference_version(cls, base_date=None):
        year, month = cls.get_reference_month_year(base_date=base_date)
        return cls.objects.filter(
            year=year, month=month, status=cls.STATUS_RELEASED
        ).first()


class CommissionUserReferenceHistory(models.Model):
    """Snapshot dos dados de comissionamento por usuário e referência (mês/ano)."""

    ROLE_CHOICES = [
        ('cn', 'CN'),
        ('gerente', 'Gerente'),
        ('coordenador', 'Coordenador'),
    ]

    year = models.PositiveSmallIntegerField(verbose_name="Ano de Referência")
    month = models.PositiveSmallIntegerField(verbose_name="Mês de Referência")
    user_name = models.CharField(max_length=255, verbose_name="Nome do Usuário")
    user = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='commission_reference_history',
        verbose_name="Usuário Vinculado"
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, verbose_name="Papel")
    sheet_name = models.CharField(max_length=120, verbose_name="Sheet de Origem")
    source_version = models.ForeignKey(
        CommissionSpreadsheetVersion,
        on_delete=models.CASCADE,
        related_name='user_history_entries',
        verbose_name="Versão de Origem"
    )
    row_data = models.JSONField(default=dict, blank=True, verbose_name="Dados da Linha")
    captured_at = models.DateTimeField(auto_now_add=True, verbose_name="Capturado em")
    updated_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='commission_history_updates',
        verbose_name="Atualizado por"
    )

    class Meta:
        verbose_name = "Histórico de Comissionamento por Usuário"
        verbose_name_plural = "Históricos de Comissionamento por Usuário"
        ordering = ['-year', '-month', 'user_name']
        constraints = [
            models.UniqueConstraint(
                fields=['year', 'month', 'sheet_name', 'user_name'],
                name='unique_commission_user_history_by_reference_and_sheet'
            )
        ]

    def __str__(self):
        return f"{self.user_name} - {self.sheet_name} ({self.month:02d}/{self.year})"


class CommissionMonthlyTotal(models.Model):
    """Tabela de totais de comissionamento por pessoa/mês/ano.

    Populada pelo botão "Sincronizar tabela" em /users/manage/system-config/.
    Guarda o valor total que cada pessoa vai receber do comissionamento no mês.
    """

    ROLE_CHOICES = [
        ('cn', 'CN'),
        ('gerente', 'Gerente'),
        ('coordenador', 'Coordenador'),
        ('aparte', 'A parte'),
    ]

    year = models.PositiveSmallIntegerField(verbose_name="Ano")
    month = models.PositiveSmallIntegerField(verbose_name="Mês")
    person_name = models.CharField(max_length=255, verbose_name="Nome da Pessoa")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cn', verbose_name="Papel")
    total_commission = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        verbose_name="Valor Total do Comissionamento"
    )
    source_version = models.ForeignKey(
        CommissionSpreadsheetVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='monthly_totals',
        verbose_name="Versão de Origem"
    )
    synced_at = models.DateTimeField(auto_now=True, verbose_name="Sincronizado em")
    synced_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='commission_total_syncs',
        verbose_name="Sincronizado por"
    )

    class Meta:
        verbose_name = "Total de Comissionamento por Mês"
        verbose_name_plural = "Totais de Comissionamento por Mês"
        ordering = ['-year', '-month', 'person_name']
        constraints = [
            models.UniqueConstraint(
                fields=['year', 'month', 'person_name', 'role'],
                name='unique_commission_monthly_total'
            )
        ]

    def __str__(self):
        return f"{self.person_name} - {self.month:02d}/{self.year}: R$ {self.total_commission}"


# Fatores padrão do comissionamento "A parte" (planilha Comissionamento Eduarda).
# Cada pilar mapeia para faixas [limite_inferior_atingimento, limite_superior, taxa].
APARTE_DEFAULT_FACTORS = {
    'movel': [[0.85, 0.8999, 0.03], [0.90, 0.9499, 0.05], [0.95, 0.9999, 0.12], [1.0, 999, 0.25]],
    'fixa': [[0.85, 0.8999, 0.03], [0.90, 0.9499, 0.06], [0.95, 0.9999, 0.15], [1.0, 999, 0.32]],
    'smartphones': [[0.85, 0.8999, 0.015], [0.90, 0.9499, 0.03], [0.95, 0.9999, 0.05], [1.0, 999, 0.10]],
    'eletronicos': [[0.85, 0.8999, 0.015], [0.90, 0.9499, 0.03], [0.95, 0.9999, 0.05], [1.0, 999, 0.10]],
    'essenciais': [[0.85, 0.8999, 0.015], [0.90, 0.9499, 0.03], [0.95, 0.9999, 0.05], [1.0, 999, 0.12]],
    'seguros': [[0.85, 0.8999, 0.015], [0.90, 0.9499, 0.03], [0.95, 0.9999, 0.06], [1.0, 999, 0.15]],
    'sva': [[0.85, 0.8999, 0.01], [0.90, 0.9499, 0.015], [0.95, 0.9999, 0.02], [1.0, 999, 0.03]],
}


def default_aparte_factors():
    """Callable usado como default do JSONField (cópia profunda dos fatores)."""
    import copy
    return copy.deepcopy(APARTE_DEFAULT_FACTORS)


class AParteCommissionConfig(models.Model):
    """Configuração do comissionamento "A parte" por usuário.

    O salário base é o multiplicador dos fatores por pilar (comissão_pilar =
    fator(atingimento_rede) × salário base). Persiste até ser alterado.
    """

    user = models.OneToOneField(
        'User',
        on_delete=models.CASCADE,
        related_name='aparte_commission_config',
        verbose_name="Usuário"
    )
    base_salary = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal('0.00'),
        verbose_name="Salário Base",
        help_text="Valor base sobre o qual os fatores de cada pilar são aplicados"
    )
    factors = models.JSONField(
        default=default_aparte_factors, blank=True,
        verbose_name="Fatores por Pilar",
        help_text="Faixas de atingimento da rede por pilar: [min, max, taxa]"
    )
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última atualização")
    updated_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='aparte_config_updates',
        verbose_name="Atualizado por"
    )

    class Meta:
        verbose_name = "Configuração de Comissionamento A parte"
        verbose_name_plural = "Configurações de Comissionamento A parte"
        ordering = ['user__first_name', 'user__last_name']

    def __str__(self):
        return f"A parte: {self.user.get_full_name()} (base R$ {self.base_salary})"


def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage()
    return None


def upload_sector_team_logo(instance, filename):
    """Define o caminho de upload para logos/escudos de times dos setores"""
    import os
    from django.utils import timezone
    ext = filename.split('.')[-1]
    new_filename = f"sector_{instance.id or 'new'}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    return os.path.join('sectors', 'team_logos', new_filename)


def upload_user_document(instance, filename):
    """Define o caminho de upload para documentos de colaboradores (pré-cadastro)."""
    import os
    from django.utils import timezone
    ext = filename.split('.')[-1] if '.' in filename else 'bin'
    new_filename = f"doc_{timezone.now().strftime('%Y%m%d_%H%M%S_%f')}.{ext}"
    return os.path.join('user_documents', str(instance.user_id or 'new'), new_filename)


class Sector(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nome")
    description = models.TextField(blank=True, verbose_name="Descrição")
    team_logo = models.ImageField(
        upload_to=upload_sector_team_logo,
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name="Escudo/Logo do Time",
        help_text="Escudo ou logo do time do setor para o sistema de apostas"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Setor"
        verbose_name_plural = "Setores"
        ordering = ['name']
    
    def __str__(self):
        return self.name


class User(AbstractUser):
    HIERARCHY_CHOICES = [
        ('PADRAO', 'Padrão'),
        ('ADMINISTRATIVO', 'Administrativo'),
        ('SUPERVISOR', 'Supervisor'),
        ('ADMIN', 'Administração'),
        ('SUPERADMIN', 'Superadmin'),
    ]
    
    email = models.EmailField(unique=True)
    sectors = models.ManyToManyField(Sector, blank=True, verbose_name="Setores", related_name="users")
    sector = models.ForeignKey(Sector, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Setor Principal", help_text="Setor principal para compatibilidade", related_name="primary_users")
    hierarchy = models.CharField(max_length=20, choices=HIERARCHY_CHOICES, default='PADRAO', verbose_name="Hierarquia")
    balance_cs = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="Saldo C$")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Telefone")
    disc_profile = models.CharField(max_length=5, blank=True, verbose_name="Perfil DISC", help_text="Perfil comportamental DISC (até 5 letras)")
    uniform_size_shirt = models.CharField(max_length=3, blank=True, verbose_name="Tamanho uniforme (Blusa)", help_text="Ex: P, M, G, GG")
    uniform_size_pants = models.CharField(max_length=3, blank=True, verbose_name="Tamanho uniforme (Calça)", help_text="Ex: P, M, G, GG, 38, 40")
    pcn = models.CharField(max_length=20, blank=True, verbose_name="PCN", help_text="Percentual CN importado das metas (sheet METAS CN REAL)")

    # Campos de dados pessoais/RH
    cpf = models.CharField(max_length=14, blank=True, verbose_name="CPF")
    pis = models.CharField(max_length=20, blank=True, verbose_name="PIS")
    birth_date = models.DateField(null=True, blank=True, verbose_name="Data de Nascimento")
    admission_date = models.DateField(null=True, blank=True, verbose_name="Data de Admissão")
    has_experience_window = models.BooleanField(default=False, verbose_name="Se Há Janela de Experiencia")
    demission_date = models.DateField(null=True, blank=True, verbose_name="Data de Demissão")
    job_title = models.CharField(max_length=100, blank=True, verbose_name="Cargo")
    login_code = models.CharField(max_length=20, blank=True, verbose_name="Login/Código")
    pdv = models.CharField(max_length=100, blank=True, verbose_name="PDV")
    neighborhood = models.CharField(max_length=100, blank=True, verbose_name="Bairro")
    city = models.CharField(max_length=100, blank=True, verbose_name="Cidade")
    cep = models.CharField(max_length=9, blank=True, default='', verbose_name="CEP")

    # Documento de identidade (RG)
    rg = models.CharField(max_length=20, blank=True, default='', verbose_name="RG")
    rg_issue_date = models.DateField(null=True, blank=True, verbose_name="Data de Emissão do RG")
    rg_issuer = models.CharField(max_length=50, blank=True, default='', verbose_name="Órgão Emissor do RG")

    # Título de eleitor
    voter_title = models.CharField(max_length=20, blank=True, default='', verbose_name="Título de Eleitor")
    voter_zone = models.CharField(max_length=10, blank=True, default='', verbose_name="Zona Eleitoral")
    voter_section = models.CharField(max_length=10, blank=True, default='', verbose_name="Seção Eleitoral")

    # Filiação
    father_name = models.CharField(max_length=150, blank=True, default='', verbose_name="Nome do Pai")
    mother_name = models.CharField(max_length=150, blank=True, default='', verbose_name="Nome da Mãe")

    # Cor/raça (classificação IBGE)
    SKIN_COLOR_CHOICES = [
        ('BRANCA', 'Branca'),
        ('PRETA', 'Preta'),
        ('PARDA', 'Parda'),
        ('AMARELA', 'Amarela'),
        ('INDIGENA', 'Indígena'),
    ]
    skin_color = models.CharField(
        max_length=10,
        choices=SKIN_COLOR_CHOICES,
        blank=True,
        default='',
        verbose_name="Cor/Raça"
    )

    avatar = models.ImageField(upload_to='avatars/', storage=get_media_storage(), blank=True, null=True, verbose_name="Avatar")
    profile_picture = models.ImageField(upload_to=upload_user_profile_photo, storage=get_media_storage(), blank=True, null=True)
    is_active = models.BooleanField(default=True, verbose_name="Ativo")

    # Situação operacional/RH do colaborador
    STATUS_ATIVO = 'ATIVO'
    STATUS_INATIVO = 'INATIVO'
    STATUS_AFASTADO = 'AFASTADO'
    STATUS_FERIAS = 'FERIAS'
    STATUS_CHOICES = [
        (STATUS_ATIVO, 'Ativo'),
        (STATUS_INATIVO, 'Inativo'),
        (STATUS_AFASTADO, 'Afastado'),
        (STATUS_FERIAS, 'Férias'),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ATIVO,
        db_index=True,
        verbose_name="Situação",
        help_text="Situação do colaborador: ativo, inativo, afastado ou de férias"
    )
    inactivation_reason = models.TextField(
        blank=True,
        default='',
        verbose_name="Motivo da Inativação"
    )
    leave_reason = models.TextField(
        blank=True,
        default='',
        verbose_name="Motivo do Afastamento"
    )
    leave_return_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Data de Retorno do Afastamento",
        help_text="Data prevista de retorno do colaborador afastado"
    )
    leave_attachment = models.FileField(
        upload_to='afastamentos/%Y/%m/',
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name="Anexo do Afastamento"
    )
    vacation_start_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Início das Férias"
    )
    vacation_end_date = models.DateField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Fim das Férias"
    )

    @property
    def is_on_vacation(self):
        """Está de férias hoje (status Férias e data de hoje dentro do período)."""
        if self.status != self.STATUS_FERIAS:
            return False
        if not self.vacation_start_date or not self.vacation_end_date:
            # Marcado como férias sem período definido: considera em andamento.
            return True
        today = timezone.localdate()
        return self.vacation_start_date <= today <= self.vacation_end_date

    @property
    def vacation_days_remaining(self):
        """Dias restantes de férias (None quando não há período definido)."""
        if self.status != self.STATUS_FERIAS or not self.vacation_end_date:
            return None
        return (self.vacation_end_date - timezone.localdate()).days

    # Pré-cadastro (onboarding via link enviado ao novo colaborador)
    PRE_REG_NONE = 'NONE'
    PRE_REG_PENDING = 'PENDING'
    PRE_REG_COMPLETED = 'COMPLETED'
    PRE_REG_APPROVED = 'APPROVED'
    PRE_REG_REJECTED = 'REJECTED'
    PRE_REGISTRATION_CHOICES = [
        (PRE_REG_NONE, 'Cadastro normal'),
        (PRE_REG_PENDING, 'Pré-cadastro pendente'),
        (PRE_REG_COMPLETED, 'Pré-cadastro concluído'),
        (PRE_REG_APPROVED, 'Pré-cadastro aprovado'),
        (PRE_REG_REJECTED, 'Pré-cadastro reprovado'),
    ]
    pre_registration_status = models.CharField(
        max_length=20,
        choices=PRE_REGISTRATION_CHOICES,
        default=PRE_REG_NONE,
        db_index=True,
        verbose_name="Situação do Pré-cadastro"
    )
    pre_registration_token = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        verbose_name="Token de Pré-cadastro"
    )
    pre_registration_created_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Data do Pré-cadastro"
    )
    pre_registration_completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Data de Conclusão do Pré-cadastro"
    )
    pre_registration_rejection_reason = models.TextField(
        blank=True,
        default='',
        verbose_name="Motivo da Reprovação do Pré-cadastro"
    )
    pre_registration_extra_documents = models.ManyToManyField(
        'RequiredDocument',
        blank=True,
        related_name='adjustment_requests',
        verbose_name="Documentos Adicionais Solicitados"
    )
    pre_registration_reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Data da Análise do Pré-cadastro"
    )
    pre_registration_reviewed_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_pre_registrations',
        verbose_name="Analisado por"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name']
    
    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
        ordering = ['first_name', 'last_name']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"

    def save(self, *args, **kwargs):
        self.cpf = normalize_cpf(self.cpf)
        super().save(*args, **kwargs)
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
    
    @property
    def primary_sector(self):
        """Retorna o setor principal ou o primeiro setor da lista"""
        if self.sector:
            return self.sector
        return self.sectors.first()
    
    @property
    def all_sectors(self):
        """Retorna todos os setores do usuário"""
        return self.sectors.all()
    
    @property
    def sectors_display(self):
        """Retorna uma string com todos os setores separados por vírgula"""
        sectors = self.sectors.all()
        if sectors:
            return ", ".join([sector.name for sector in sectors])
        return "Nenhum setor"
    
    def is_in_sector(self, sector):
        """Verifica se o usuário pertence a um setor específico"""
        return self.sectors.filter(id=sector.id).exists()
    
    def add_sector(self, sector):
        """Adiciona um setor ao usuário"""
        self.sectors.add(sector)
        # Se não tem setor principal, define este como principal
        if not self.sector:
            self.sector = sector
            self.save()
    
    def remove_sector(self, sector):
        """Remove um setor do usuário"""
        self.sectors.remove(sector)
        # Se o setor removido era o principal, define outro como principal
        if self.sector == sector:
            self.sector = self.sectors.first()
            self.save()
    
    def can_manage_users(self):
        return self.hierarchy in ['ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO']
    
    @property
    def calculated_balance_cs(self):
        """Calcula o saldo C$ baseado apenas em transações aprovadas"""
        from prizes.models import CSTransaction
        from django.db.models import Sum, Q
        
        # Somar créditos aprovados
        credits = CSTransaction.objects.filter(
            user=self,
            transaction_type__in=['CREDIT', 'ADJUSTMENT'],
            status='APPROVED',
            amount__gt=0
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Somar débitos aprovados (valores negativos ou tipos DEBIT/REDEMPTION)
        debits = CSTransaction.objects.filter(
            user=self,
            status='APPROVED'
        ).filter(
            Q(transaction_type__in=['DEBIT', 'REDEMPTION']) |
            Q(amount__lt=0)
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        return abs(credits) - abs(debits)

    def can_manage_prizes(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']

    def can_manage_cs(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_view_all_tickets(self):
        return self.hierarchy in ['ADMIN', 'SUPERADMIN']
    
    def can_view_sector_tickets(self):
        return self.hierarchy in ['PADRAO', 'ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_create_communications(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_edit_sector_categories(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_upload_files(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_access_management_panel(self):
        return self.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_access_admin_panel(self):
        return self.hierarchy in ['ADMIN', 'SUPERADMIN']
    
    def can_view_reports(self):
        return self.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_manage_webhooks(self):
        return self.hierarchy in ['SUPERADMIN']

    def can_create_contestations(self):
        """Permite criação de contestações para SUPERVISOR+ ou PADRAO no grupo GERENTES."""
        if self.is_superuser:
            return True
        if self.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN']:
            return True
        if self.hierarchy == 'PADRAO':
            return self.communication_groups.filter(name__iexact='GERENTES').exists()
        return False
    
    def can_delete_users(self):
        return self.hierarchy in ['SUPERADMIN']
    
    def can_delete_tickets(self):
        return self.hierarchy in ['SUPERADMIN']

    def can_delete_ticket_attachments(self):
        """Permite remover anexos de chamados para SUPERVISOR e hierarquias acima."""
        return self.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN']

    def is_pre_registration_pending(self):
        """Colaborador criado via pré-cadastro que ainda não concluiu o preenchimento."""
        return self.pre_registration_status == self.PRE_REG_PENDING

    def needs_pre_registration_adjustment(self):
        """Pré-cadastro reprovado: o colaborador precisa corrigir e reenviar."""
        return self.pre_registration_status == self.PRE_REG_REJECTED

    def is_pre_registration_awaiting_review(self):
        """Pré-cadastro concluído e ainda sem análise do gestor."""
        return self.pre_registration_status == self.PRE_REG_COMPLETED

    def can_review_pre_registrations(self):
        """Quem pode aprovar/reprovar um pré-cadastro concluído."""
        return self.can_manage_users()

    def can_manage_required_documents(self):
        """Somente SUPERADMIN configura quais documentos são exigidos no pré-cadastro."""
        return self.hierarchy == 'SUPERADMIN'

    @classmethod
    def hierarchy_rank(cls, hierarchy):
        """Índice da hierarquia (0 = mais baixo). Retorna -1 se for inválida."""
        for index, (key, _label) in enumerate(cls.HIERARCHY_CHOICES):
            if key == hierarchy:
                return index
        return -1

    @property
    def hierarchy_level(self):
        """Nível numérico da hierarquia do próprio usuário."""
        return self.hierarchy_rank(self.hierarchy)

    def assignable_hierarchy_choices(self):
        """Hierarquias que este usuário pode atribuir (somente até o seu próprio nível)."""
        if self.is_superuser:
            return list(self.HIERARCHY_CHOICES)
        max_rank = self.hierarchy_level
        return [choice for index, choice in enumerate(self.HIERARCHY_CHOICES) if index <= max_rank]

    def can_assign_hierarchy(self, hierarchy):
        """Impede atribuir hierarquia acima da própria (evita autopromoção/escalonamento)."""
        if self.is_superuser:
            return True
        rank = self.hierarchy_rank(hierarchy)
        return rank != -1 and rank <= self.hierarchy_level

    def can_manage_hierarchy_of(self, other):
        """Não permite editar/gerenciar usuários de hierarquia superior à própria."""
        if self.is_superuser or not other or self.pk == other.pk:
            return True
        return self.hierarchy_level >= self.hierarchy_rank(other.hierarchy)


class RequiredDocument(models.Model):
    """Documento exigido no pré-cadastro. Lista global configurável pelo SUPERADMIN."""
    name = models.CharField(max_length=150, verbose_name="Nome do Documento")
    description = models.CharField(max_length=255, blank=True, default='', verbose_name="Descrição")
    is_required = models.BooleanField(default=True, verbose_name="Obrigatório")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    order = models.PositiveIntegerField(default=0, verbose_name="Ordem de Exibição")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Documento Exigido"
        verbose_name_plural = "Documentos Exigidos"
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class UserDocument(models.Model):
    """Documento enviado por um colaborador (no pré-cadastro ou pelo gestor)."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='documents',
        verbose_name="Colaborador"
    )
    document_type = models.ForeignKey(
        RequiredDocument,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploads',
        verbose_name="Tipo de Documento"
    )
    document_name = models.CharField(
        max_length=150,
        blank=True,
        default='',
        verbose_name="Nome do Documento"
    )
    file = models.FileField(
        upload_to=upload_user_document,
        storage=get_media_storage(),
        verbose_name="Arquivo"
    )
    original_filename = models.CharField(max_length=255, blank=True, default='')
    file_size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=255, blank=True, default='')
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='uploaded_user_documents',
        verbose_name="Enviado por"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Documento do Colaborador"
        verbose_name_plural = "Documentos dos Colaboradores"
        ordering = ['document_name', '-uploaded_at']

    def __str__(self):
        return f"{self.user} - {self.display_name}"

    @property
    def display_name(self):
        if self.document_name:
            return self.document_name
        if self.document_type_id and self.document_type:
            return self.document_type.name
        return self.original_filename or 'Documento'

    @property
    def file_extension(self):
        import os
        source = self.original_filename or (self.file.name if self.file else '')
        return os.path.splitext(source)[1].lower()

    @property
    def is_image(self):
        return self.file_extension in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']

    @property
    def is_pdf(self):
        return self.file_extension == '.pdf'

    @property
    def file_size_formatted(self):
        size = self.file_size or 0
        if size < 1024:
            return f"{size} bytes"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        return f"{size / (1024 * 1024):.1f} MB"


class UserSession(models.Model):
    """Rastreia as sessões ativas de cada usuário (IP, localização e dispositivo).

    Cada registro está vinculado a uma sessão do Django (``django_session``)
    através de ``session_key``, permitindo listar quem está logado e derrubar
    sessões individualmente.
    """
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='active_sessions',
        verbose_name="Usuário"
    )
    session_key = models.CharField(
        max_length=40, unique=True, db_index=True, verbose_name="Chave da Sessão"
    )
    ip_address = models.GenericIPAddressField(
        null=True, blank=True, verbose_name="Endereço IP"
    )
    user_agent = models.TextField(blank=True, verbose_name="User Agent")
    location = models.CharField(
        max_length=255, blank=True, verbose_name="Localização"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Início da Sessão")
    last_activity = models.DateTimeField(auto_now=True, verbose_name="Última Atividade")

    class Meta:
        verbose_name = "Sessão de Usuário"
        verbose_name_plural = "Sessões de Usuários"
        ordering = ['-last_activity']

    def __str__(self):
        return f"{self.user.email} - {self.ip_address or 'IP desconhecido'}"

    @property
    def device_summary(self):
        """Resumo legível do navegador e sistema operacional a partir do user agent."""
        ua = (self.user_agent or '').lower()

        if 'windows' in ua:
            os_name = 'Windows'
        elif 'iphone' in ua or 'ipad' in ua:
            os_name = 'iOS'
        elif 'android' in ua:
            os_name = 'Android'
        elif 'mac os' in ua or 'macintosh' in ua:
            os_name = 'macOS'
        elif 'linux' in ua:
            os_name = 'Linux'
        elif ua:
            os_name = 'Outro'
        else:
            os_name = 'Desconhecido'

        if 'edg' in ua:
            browser = 'Edge'
        elif 'opr' in ua or 'opera' in ua:
            browser = 'Opera'
        elif 'chrome' in ua and 'chromium' not in ua:
            browser = 'Chrome'
        elif 'firefox' in ua:
            browser = 'Firefox'
        elif 'safari' in ua:
            browser = 'Safari'
        elif ua:
            browser = 'Navegador'
        else:
            browser = 'Desconhecido'

        return f"{browser} · {os_name}"

    @property
    def is_mobile(self):
        ua = (self.user_agent or '').lower()
        return any(k in ua for k in ['iphone', 'android', 'mobile', 'ipad'])

    @property
    def location_display(self):
        return self.location or 'Localização desconhecida'
