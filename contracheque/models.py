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


# ─── Informe de Rendimentos ──────────────────────────────────────────────────

def upload_income_report_pdf(instance, filename):
    ext = filename.rsplit('.', 1)[-1].lower()
    new_name = f"informe_{instance.user_id}_{instance.base_year}.{ext}"
    return os.path.join('informes', str(instance.base_year), new_name)


class IncomeReport(models.Model):
    """Informe de Rendimentos (Comprovante de Rendimentos Pagos e IRRF)"""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='income_reports',
        verbose_name='Funcionário',
    )
    base_year = models.PositiveIntegerField(verbose_name='Ano-Calendário')
    exercise_year = models.PositiveIntegerField(verbose_name='Exercício')
    pdf_file = models.FileField(upload_to=upload_income_report_pdf, verbose_name='Arquivo PDF')

    # Dados do beneficiário
    employee_name = models.CharField(max_length=200, blank=True, verbose_name='Nome no Informe')
    cpf = models.CharField(max_length=20, blank=True, verbose_name='CPF')

    # 3. Rendimentos Tributáveis
    total_rendimentos = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Total dos rendimentos')
    contribuicao_previdenciaria = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Contribuição previdenciária oficial')
    contribuicao_previdencia_privada = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Contribuição previdência privada/FAPI')
    pensao_alimenticia = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Pensão alimentícia')
    irrf = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='IRRF')

    # 4. Rendimentos Isentos
    parcela_isenta_aposentadoria = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Parcela isenta aposentadoria')
    parcela_isenta_13_aposentadoria = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Parcela isenta 13º aposentadoria')
    diarias_ajuda_custo = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Diárias e ajuda de custo')
    pensao_moletia_grave = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Pensão por moléstia grave')
    lucros_dividendos = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Lucros e dividendos')
    valores_titular_socio = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Valores pagos titular/sócio')
    indenizacao_rescisao = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Indenizações rescisão')
    juros_mora = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Juros de mora')
    outros_isentos = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Outros isentos')

    # 5. Rendimentos sujeitos a tributação exclusiva
    decimo_terceiro = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='13º salário')
    irrf_13 = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='IRRF sobre 13º')
    outros_exclusivos = models.DecimalField(max_digits=14, decimal_places=2, default=0,
        verbose_name='Outros exclusivos')

    # Página no PDF original
    pdf_page_number = models.PositiveIntegerField(null=True, blank=True, verbose_name='Página no PDF original')

    # Metadados
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='income_reports_uploaded',
        verbose_name='Importado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Informe de Rendimentos'
        verbose_name_plural = 'Informes de Rendimentos'
        ordering = ['-base_year']
        unique_together = ['user', 'base_year']

    def __str__(self):
        return f"{self.user.full_name} – {self.base_year}"

    @property
    def total_isentos(self):
        return (
            self.parcela_isenta_aposentadoria + self.parcela_isenta_13_aposentadoria +
            self.diarias_ajuda_custo + self.pensao_moletia_grave +
            self.lucros_dividendos + self.valores_titular_socio +
            self.indenizacao_rescisao + self.juros_mora + self.outros_isentos
        )
