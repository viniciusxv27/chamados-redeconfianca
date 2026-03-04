import os
from django.db import models
from django.conf import settings
from django.utils import timezone


def upload_payslip_pdf(instance, filename):
    ext = filename.rsplit('.', 1)[-1].lower()
    new_name = f"contracheque_{instance.user_id}_{instance.year}_{instance.month:02d}.{ext}"
    return os.path.join('contracheques', str(instance.year), new_name)


class Payslip(models.Model):
    """Recibo de Pagamento / Contracheque"""

    MONTH_CHOICES = [
        (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
        (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
        (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='payslips',
        verbose_name='Funcionário',
    )
    month = models.PositiveSmallIntegerField(choices=MONTH_CHOICES, verbose_name='Mês')
    year = models.PositiveIntegerField(verbose_name='Ano')
    pdf_file = models.FileField(upload_to=upload_payslip_pdf, verbose_name='Arquivo PDF')

    # Dados extraídos do PDF
    employee_name = models.CharField(max_length=200, blank=True, verbose_name='Nome no Contracheque')
    cpf = models.CharField(max_length=20, blank=True, verbose_name='CPF')
    job_title = models.CharField(max_length=150, blank=True, verbose_name='Cargo')
    admission_date = models.CharField(max_length=20, blank=True, verbose_name='Data de Admissão')
    department = models.CharField(max_length=200, blank=True, verbose_name='Departamento')

    # Valores
    base_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Salário Base')
    total_earnings = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total de Proventos')
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Total de Descontos')
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Líquido')
    fgts_base = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Base FGTS')
    fgts_deposit = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Depósito FGTS')
    irrf_base = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Base IRRF')
    inss_base = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name='Base INSS')

    # Detalhes em JSON
    earnings_detail = models.JSONField(default=list, blank=True, verbose_name='Proventos (detalhado)')
    deductions_detail = models.JSONField(default=list, blank=True, verbose_name='Descontos (detalhado)')

    # Página no PDF original (para extração de página individual)
    pdf_page_number = models.PositiveIntegerField(null=True, blank=True, verbose_name='Página no PDF original',
        help_text='Número da página (0-indexed) do funcionário no PDF original')

    # Assinatura digital
    signed_at = models.DateTimeField(null=True, blank=True, verbose_name='Assinado em')
    signature_image = models.TextField(blank=True, verbose_name='Assinatura (base64)',
        help_text='Imagem da assinatura em base64 (PNG)')
    signature_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP da assinatura')
    signature_user_agent = models.TextField(blank=True, verbose_name='User-Agent da assinatura')
    signature_hash = models.CharField(max_length=64, blank=True, verbose_name='Hash de verificação',
        help_text='SHA-256 do conteúdo assinado para garantir integridade')

    # Metadados
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='payslips_uploaded',
        verbose_name='Importado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Contracheque'
        verbose_name_plural = 'Contracheques'
        ordering = ['-year', '-month']
        unique_together = ['user', 'month', 'year']

    def __str__(self):
        month_name = dict(self.MONTH_CHOICES).get(self.month, self.month)
        return f"{self.user.full_name} – {month_name}/{self.year}"

    @property
    def is_signed(self):
        return bool(self.signed_at)

    @property
    def month_name(self):
        return dict(self.MONTH_CHOICES).get(self.month, str(self.month))

    @property
    def period_display(self):
        return f"{self.month_name}/{self.year}"
