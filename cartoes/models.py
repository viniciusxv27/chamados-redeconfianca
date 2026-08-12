import os
import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


def get_media_storage():
    """Backend de storage de mídia (S3/MinIO em produção; local caso contrário).

    Mesmo padrão de folhaponto/limpeza: só usa MediaStorage quando USE_S3 está on.
    """
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage()
    return None


def upload_cartao_gasto_foto(instance, filename):
    ext = os.path.splitext(filename)[1].lower() or '.jpg'
    return f"cartoes/gastos/gasto_{uuid.uuid4().hex}{ext}"


_DIGITS4 = RegexValidator(r'^\d{4}$', 'Informe exatamente 4 dígitos.')


class Cartao(models.Model):
    """Cartão de crédito corporativo, gerenciado por um usuário responsável."""

    BANDEIRA_CHOICES = [
        ('VISA', 'Visa'),
        ('MASTERCARD', 'Mastercard'),
        ('ELO', 'Elo'),
        ('AMEX', 'American Express'),
    ]

    apelido = models.CharField(max_length=60, blank=True, verbose_name='Apelido',
                               help_text='Nome amigável para identificar o cartão (opcional).')
    first4 = models.CharField(max_length=4, validators=[_DIGITS4], verbose_name='4 primeiros dígitos')
    last4 = models.CharField(max_length=4, validators=[_DIGITS4], verbose_name='4 últimos dígitos')
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='cartoes', verbose_name='Responsável',
    )
    validade_mes = models.PositiveSmallIntegerField(verbose_name='Mês de validade')
    validade_ano = models.PositiveSmallIntegerField(verbose_name='Ano de validade')
    bandeira = models.CharField(max_length=15, choices=BANDEIRA_CHOICES, verbose_name='Bandeira')
    ativo = models.BooleanField(default=True, verbose_name='Ativo')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cartoes_criados', verbose_name='Criado por',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Cartão'
        verbose_name_plural = 'Cartões'
        ordering = ['apelido', 'last4']

    def __str__(self):
        return f"{self.get_bandeira_display()} ••••{self.last4}"

    @property
    def numero_mascarado(self):
        return f"{self.first4} •••• •••• {self.last4}"

    @property
    def validade_display(self):
        return f"{self.validade_mes:02d}/{str(self.validade_ano)[-2:]}"

    @property
    def titulo(self):
        return self.apelido or self.numero_mascarado


class Gasto(models.Model):
    """Um gasto lançado num cartão. Ao ser confirmado, abre um chamado (cat. 99)."""

    ORIGEM_CHOICES = [('MANUAL', 'Manual'), ('FOTO', 'Foto')]

    cartao = models.ForeignKey(Cartao, on_delete=models.CASCADE, related_name='gastos', verbose_name='Cartão')
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='gastos_cartao', verbose_name='Lançado por',
    )
    valor = models.DecimalField(max_digits=12, decimal_places=2, verbose_name='Valor (R$)')
    estabelecimento = models.CharField(max_length=200, blank=True, verbose_name='Estabelecimento')
    data_gasto = models.DateField(verbose_name='Data do gasto')
    categoria_gasto = models.CharField(max_length=120, blank=True, verbose_name='Categoria do gasto')
    descricao = models.TextField(blank=True, verbose_name='Descrição')
    foto = models.ImageField(
        upload_to=upload_cartao_gasto_foto, storage=get_media_storage(),
        blank=True, null=True, verbose_name='Foto do comprovante',
    )
    origem = models.CharField(max_length=10, choices=ORIGEM_CHOICES, default='MANUAL', verbose_name='Origem')
    ia_dados = models.JSONField(default=dict, blank=True, verbose_name='Dados extraídos pela IA')
    ticket = models.ForeignKey(
        'tickets.Ticket', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cartao_gastos', verbose_name='Chamado gerado',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Gasto'
        verbose_name_plural = 'Gastos'
        ordering = ['-data_gasto', '-created_at']

    def __str__(self):
        return f"{self.estabelecimento or 'Gasto'} — R$ {self.valor}"
