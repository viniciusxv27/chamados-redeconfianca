from django.db import models
from django.conf import settings
from core.utils import upload_asset_photo


class Asset(models.Model):
    ESTADO_FISICO_CHOICES = [
        ('excelente', 'Excelente'),
        ('bom', 'Bom'),
        ('regular', 'Regular'),
        ('ruim', 'Ruim'),
        ('pessimo', 'Péssimo'),
    ]

    patrimonio_numero = models.CharField(max_length=20, unique=True, verbose_name='N° Patrimônio')
    nome = models.CharField(max_length=200, verbose_name='Nome')
    localizado = models.CharField(max_length=200, verbose_name='Localizado')
    setor = models.CharField(max_length=100, verbose_name='Setor')
    pdv = models.CharField(max_length=50, verbose_name='PDV')
    estado_fisico = models.CharField(
        max_length=20, 
        choices=ESTADO_FISICO_CHOICES, 
        default='bom',
        verbose_name='Estado Físico'
    )
    observacoes = models.TextField(blank=True, null=True, verbose_name='Observações')
    photo = models.ImageField(upload_to=upload_asset_photo, blank=True, null=True, verbose_name='Foto do Asset')
    
    # Campos de auditoria
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='assets_created',
        verbose_name='Criado por'
    )

    class Meta:
        verbose_name = 'Ativo'
        verbose_name_plural = 'Ativos'
        ordering = ['patrimonio_numero']

    def __str__(self):
        return f"{self.patrimonio_numero} - {self.nome}"
