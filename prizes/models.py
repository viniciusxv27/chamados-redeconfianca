from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal


class PrizeCategory(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nome da Categoria")
    description = models.TextField(blank=True, verbose_name="Descrição")
    icon = models.CharField(max_length=50, default="fas fa-gift", verbose_name="Ícone Font Awesome")
    color = models.CharField(max_length=20, default="blue", verbose_name="Cor")
    active = models.BooleanField(default=True, verbose_name="Ativa")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Categoria de Prêmio"
        verbose_name_plural = "Categorias de Prêmios"
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Prize(models.Model):
    PRIORITY_CHOICES = [
        ('BAIXA', 'Baixa'),
        ('NORMAL', 'Normal'),
        ('ALTA', 'Alta'),
        ('DESTAQUE', 'Destaque'),
    ]
    
    name = models.CharField(max_length=200, verbose_name="Nome")
    description = models.TextField(verbose_name="Descrição")
    category = models.ForeignKey(PrizeCategory, on_delete=models.CASCADE, null=True, blank=True, verbose_name="Categoria")
    value_cs = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor em C$")
    image = models.ImageField(upload_to='prizes/', blank=True, null=True, verbose_name="Imagem")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='NORMAL', verbose_name="Prioridade")
    stock = models.PositiveIntegerField(default=0, verbose_name="Estoque")
    unlimited_stock = models.BooleanField(default=False, verbose_name="Estoque Ilimitado")
    redeemed_count = models.PositiveIntegerField(default=0, verbose_name="Quantidade Resgatada")
    terms = models.TextField(blank=True, verbose_name="Termos e Condições")
    valid_until = models.DateField(blank=True, null=True, verbose_name="Válido até")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="Criado por")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Prêmio"
        verbose_name_plural = "Prêmios"
        ordering = ['-priority', 'name']
    
    def __str__(self):
        return f"{self.name} - C$ {self.value_cs}"
    
    @property
    def available(self):
        if not self.is_active:
            return False
        if not self.unlimited_stock and self.redeemed_count >= self.stock:
            return False
        if self.valid_until and self.valid_until < timezone.now().date():
            return False
        return True
    
    @property
    def availability_percentage(self):
        if self.unlimited_stock:
            return 100
        if self.stock == 0:
            return 0
        return max(0, ((self.stock - self.redeemed_count) / self.stock) * 100)


class Redemption(models.Model):
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('APROVADO', 'Aprovado'),
        ('ENTREGUE', 'Entregue'),
        ('CANCELADO', 'Cancelado'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    prize = models.ForeignKey(Prize, on_delete=models.CASCADE, verbose_name="Prêmio")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDENTE', verbose_name="Status")
    redeemed_at = models.DateTimeField(auto_now_add=True, verbose_name="Data do Resgate")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Data da Aprovação")
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name="Data da Entrega")
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='approved_redemptions',
        verbose_name="Aprovado por"
    )
    notes = models.TextField(blank=True, verbose_name="Observações")
    
    class Meta:
        verbose_name = "Resgate"
        verbose_name_plural = "Resgates"
        ordering = ['-redeemed_at']
    
    def __str__(self):
        return f"{self.user.full_name} - {self.prize.name}"


class CSTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('CREDIT', 'Crédito'),
        ('DEBIT', 'Débito'),
        ('REDEMPTION', 'Resgate'),
        ('ADJUSTMENT', 'Ajuste'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor")
    transaction_type = models.CharField(max_length=15, choices=TRANSACTION_TYPES, verbose_name="Tipo")
    description = models.CharField(max_length=200, verbose_name="Descrição")
    related_redemption = models.ForeignKey(
        Redemption, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        verbose_name="Resgate Relacionado"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_transactions',
        verbose_name="Criado por"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    
    class Meta:
        verbose_name = "Transação C$"
        verbose_name_plural = "Transações C$"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.full_name} - {self.transaction_type} - C$ {self.amount}"
