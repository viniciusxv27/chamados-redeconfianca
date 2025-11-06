from django.db import models
from django.conf import settings
from core.storage import get_media_storage


class Benefit(models.Model):
    """Modelo para benefícios do Clube de Benefícios"""
    
    STATUS_CHOICES = [
        ('active', 'Ativo'),
        ('inactive', 'Inativo'),
        ('expired', 'Expirado'),
    ]
    
    # Dados principais
    title = models.CharField(max_length=200, verbose_name="Título")
    description = models.TextField(verbose_name="Descrição Resumida", help_text="Descrição curta que aparece no card")
    full_description = models.TextField(verbose_name="Descrição Completa", help_text="Descrição detalhada que aparece ao abrir o benefício")
    coupon_code = models.CharField(max_length=100, verbose_name="Código do Cupom", help_text="Cupom que será exibido ao usuário")
    
    # Imagem
    image = models.ImageField(
        upload_to='benefits/',
        verbose_name="Imagem",
        help_text="Imagem do benefício (recomendado: 800x600px)",
        storage=get_media_storage(),
        blank=True,
        null=True
    )
    
    # Controle
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active', verbose_name="Status")
    is_featured = models.BooleanField(default=False, verbose_name="Destacado", help_text="Benefício em destaque aparece primeiro")
    
    # Metadados
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_benefits',
        verbose_name="Criado por"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Atualizado em")
    
    # Validade
    valid_from = models.DateField(null=True, blank=True, verbose_name="Válido de")
    valid_until = models.DateField(null=True, blank=True, verbose_name="Válido até")
    
    # Contadores
    views_count = models.PositiveIntegerField(default=0, verbose_name="Visualizações")
    redeems_count = models.PositiveIntegerField(default=0, verbose_name="Resgates")
    
    class Meta:
        verbose_name = "Benefício"
        verbose_name_plural = "Benefícios"
        ordering = ['-is_featured', '-created_at']
    
    def __str__(self):
        return self.title
    
    def increment_views(self):
        """Incrementa o contador de visualizações"""
        self.views_count += 1
        self.save(update_fields=['views_count'])
    
    def increment_redeems(self):
        """Incrementa o contador de resgates"""
        self.redeems_count += 1
        self.save(update_fields=['redeems_count'])


class BenefitRedeem(models.Model):
    """Registro de resgate de benefícios pelos usuários"""
    
    benefit = models.ForeignKey(
        Benefit,
        on_delete=models.CASCADE,
        related_name='redeems',
        verbose_name="Benefício"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='benefit_redeems',
        verbose_name="Usuário"
    )
    redeemed_at = models.DateTimeField(auto_now_add=True, verbose_name="Resgatado em")
    
    class Meta:
        verbose_name = "Resgate de Benefício"
        verbose_name_plural = "Resgates de Benefícios"
        ordering = ['-redeemed_at']
        unique_together = ['benefit', 'user']  # Um usuário pode resgatar cada benefício apenas uma vez
    
    def __str__(self):
        return f"{self.user.get_full_name()} - {self.benefit.title}"

