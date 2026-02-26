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
    def month_name(self):
        return dict(self.MONTH_CHOICES).get(self.month, str(self.month))

    @property
    def period_display(self):
        return f"{self.month_name}/{self.year}"
