from django.db import models
from django.conf import settings
from django.utils import timezone


class ExclusionRecord(models.Model):
    """Registro importado da planilha BASE_EXCLUSAO."""
    filial = models.CharField(max_length=100, verbose_name='Filial')
    vendedor = models.CharField(max_length=200, verbose_name='Vendedor')
    receita = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Receita')
    pilar = models.CharField(max_length=50, verbose_name='Pilar')
    coordenacao = models.CharField(max_length=200, blank=True, default='', verbose_name='Coordenação')
    numero_venda = models.CharField(max_length=100, blank=True, default='', verbose_name='Nº da Venda')
    data_venda = models.CharField(max_length=50, blank=True, default='', verbose_name='Data da Venda')
    nome_cliente = models.CharField(max_length=300, blank=True, default='', verbose_name='Nome Cliente')
    cpf_cnpj = models.CharField(max_length=30, blank=True, default='', verbose_name='CPF/CNPJ')
    plano_produto = models.CharField(max_length=300, blank=True, default='', verbose_name='Plano/Produto')
    numero_acesso = models.CharField(max_length=100, blank=True, default='', verbose_name='Número Acesso')
    imported_at = models.DateTimeField(auto_now_add=True, verbose_name='Importado em')

    class Meta:
        verbose_name = 'Registro de Exclusão'
        verbose_name_plural = 'Registros de Exclusão'
        ordering = ['-imported_at']
        indexes = [
            models.Index(fields=['filial']),
            models.Index(fields=['vendedor']),
            models.Index(fields=['pilar']),
        ]

    def __str__(self):
        return f'{self.vendedor} – {self.pilar} R${self.receita} ({self.filial})'


class Contestation(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('accepted', 'Aprovada pelo Gestor'),
        ('rejected', 'Rejeitada'),
        ('confirmed', 'Confirmada pelo Gerente'),
        ('denied', 'Negada pelo Gerente'),
    ]
    PAYMENT_CHOICES = [
        ('not_applicable', 'N/A'),
        ('pending_payment', 'Aguardando Pagamento'),
        ('paid', 'Pago'),
    ]

    exclusion = models.ForeignKey(
        ExclusionRecord, on_delete=models.CASCADE,
        related_name='contestations', verbose_name='Registro de Exclusão'
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='contestations_created', verbose_name='Solicitante'
    )
    reason = models.TextField(verbose_name='Motivo da Contestação')
    attachment = models.FileField(
        upload_to='contestacoes/%Y/%m/', blank=True, null=True,
        verbose_name='Anexo/Evidência'
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name='Status')
    payment_status = models.CharField(
        max_length=20, choices=PAYMENT_CHOICES, default='not_applicable',
        verbose_name='Status do Pagamento'
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contestations_reviewed',
        verbose_name='Analisado por'
    )
    review_notes = models.TextField(blank=True, default='', verbose_name='Observações da Análise')
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='Data da Análise')
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='contestations_confirmed',
        verbose_name='Confirmado por'
    )
    confirmed_at = models.DateTimeField(null=True, blank=True, verbose_name='Data da Confirmação')
    confirmation_notes = models.TextField(blank=True, default='', verbose_name='Observações da Confirmação')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')

    class Meta:
        verbose_name = 'Contestação'
        verbose_name_plural = 'Contestações'
        ordering = ['-created_at']

    def __str__(self):
        return f'Contestação #{self.pk} – {self.exclusion.vendedor} ({self.get_status_display()})'

    def approve(self, reviewer, notes=''):
        """Gestor aprova — aguarda confirmação do gerente."""
        self.status = 'accepted'
        self.reviewed_by = reviewer
        self.review_notes = notes
        self.reviewed_at = timezone.now()
        self.save()

    def reject(self, reviewer, notes=''):
        """Gestor rejeita — fim do fluxo."""
        self.status = 'rejected'
        self.reviewed_by = reviewer
        self.review_notes = notes
        self.reviewed_at = timezone.now()
        self.save()

    def confirm(self, requester, notes=''):
        """Gerente confirma após aprovação do gestor."""
        self.status = 'confirmed'
        self.payment_status = 'pending_payment'
        self.confirmed_by = requester
        self.confirmation_notes = notes
        self.confirmed_at = timezone.now()
        self.save()

    def deny_confirmation(self, requester, notes=''):
        """Gerente nega após aprovação do gestor."""
        self.status = 'denied'
        self.confirmed_by = requester
        self.confirmation_notes = notes
        self.confirmed_at = timezone.now()
        self.save()

    def mark_paid(self, reviewer):
        self.payment_status = 'paid'
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.save()
