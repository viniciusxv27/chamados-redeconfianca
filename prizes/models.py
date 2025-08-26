from django.db import models
from django.conf import settings
from decimal import Decimal


class Prize(models.Model):
    name = models.CharField(max_length=200, verbose_name="Nome")
    description = models.TextField(verbose_name="Descrição")
    value_cs = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor em C$")
    image = models.ImageField(upload_to='prizes/', blank=True, null=True, verbose_name="Imagem")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    stock = models.PositiveIntegerField(default=0, verbose_name="Estoque")
    unlimited_stock = models.BooleanField(default=False, verbose_name="Estoque Ilimitado")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Prêmio"
        verbose_name_plural = "Prêmios"
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} - C$ {self.value_cs}"
    
    @property
    def available(self):
        return self.is_active and (self.unlimited_stock or self.stock > 0)


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
