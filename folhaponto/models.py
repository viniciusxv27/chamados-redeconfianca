import os
import uuid

from django.db import models
from django.conf import settings


def get_media_storage():
    """Retorna backend de storage de mídia (S3 em produção)."""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


def upload_folha_pdf(instance, filename):
    ext = filename.rsplit('.', 1)[-1].lower()
    token = uuid.uuid4().hex[:10]
    new_name = f"folhaponto_{instance.user_id}_{instance.year}_{instance.month:02d}_{token}.{ext}"
    return os.path.join('folhas_ponto', str(instance.year), f"{instance.month:02d}",
                        str(instance.user_id), new_name)


class FolhaPonto(models.Model):
    """Folha de Ponto / Cartão de Ponto mensal de um colaborador."""

    MONTH_CHOICES = [
        (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
        (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
        (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='folhas_ponto',
        verbose_name='Funcionário',
    )
    month = models.PositiveSmallIntegerField(choices=MONTH_CHOICES, verbose_name='Mês')
    year = models.PositiveIntegerField(verbose_name='Ano')
    pdf_file = models.FileField(
        upload_to=upload_folha_pdf,
        storage=get_media_storage(),
        verbose_name='Arquivo PDF',
    )

    # Identificação extraída do PDF
    employee_name = models.CharField(max_length=200, blank=True, verbose_name='Nome na Folha')
    cpf = models.CharField(max_length=20, blank=True, verbose_name='CPF')
    job_title = models.CharField(max_length=150, blank=True, verbose_name='Função')
    admission_date = models.CharField(max_length=20, blank=True, verbose_name='Data de Admissão')
    employer_name = models.CharField(max_length=200, blank=True, verbose_name='Empregador')
    period_start = models.CharField(max_length=20, blank=True, verbose_name='Início do período')
    period_end = models.CharField(max_length=20, blank=True, verbose_name='Fim do período')

    # Totais / resumo (armazenados como texto HH:MM)
    total_trabalhadas = models.CharField(max_length=15, blank=True, verbose_name='Horas trabalhadas')
    total_abono = models.CharField(max_length=15, blank=True, verbose_name='Abono')
    total_previstas = models.CharField(max_length=15, blank=True, verbose_name='Horas previstas')
    total_saldo = models.CharField(max_length=15, blank=True, verbose_name='Saldo do período')
    trabalhadas_abono = models.CharField(max_length=15, blank=True, verbose_name='Trabalhadas + Abono')
    faltas_horas = models.CharField(max_length=15, blank=True, verbose_name='Faltas em horas')
    saldo_anterior = models.CharField(max_length=15, blank=True, verbose_name='Saldo anterior banco de horas')
    saldo_acumulado = models.CharField(max_length=15, blank=True, verbose_name='Saldo acumulado')
    horas_extras = models.CharField(max_length=15, blank=True, verbose_name='Horas extras totais')
    atrasos = models.CharField(max_length=15, blank=True, verbose_name='Atrasos')
    dias_faltosos = models.PositiveIntegerField(default=0, verbose_name='Dias faltosos')

    # Marcações diárias detalhadas
    daily_records = models.JSONField(default=list, blank=True, verbose_name='Marcações diárias')

    # Página(s) no PDF original
    pdf_page_number = models.PositiveIntegerField(null=True, blank=True,
        verbose_name='Página inicial no PDF original')

    # Assinatura digital
    signed_at = models.DateTimeField(null=True, blank=True, verbose_name='Assinado em')
    signature_image = models.TextField(blank=True, verbose_name='Assinatura (base64)')
    signature_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP da assinatura')
    signature_user_agent = models.TextField(blank=True, verbose_name='User-Agent da assinatura')
    signature_hash = models.CharField(max_length=64, blank=True, verbose_name='Hash de verificação')

    # Metadados
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='folhas_ponto_uploaded',
        verbose_name='Importado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Folha de Ponto'
        verbose_name_plural = 'Folhas de Ponto'
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
