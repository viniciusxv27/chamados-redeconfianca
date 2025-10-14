from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from core.utils import upload_prize_image

def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage
    return None


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
    image = models.ImageField(upload_to=upload_prize_image, storage=get_media_storage(), blank=True, null=True, verbose_name="Imagem")
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
        if self.valid_until and self.valid_until < timezone.now().date():
            return False
        if not self.unlimited_stock:
            # Contar apenas resgates aprovados e entregues
            approved_count = self.redemption_set.filter(status__in=['APROVADO', 'ENTREGUE']).count()
            if approved_count >= self.stock:
                return False
        return True
    
    @property
    def availability_percentage(self):
        if self.unlimited_stock:
            return 100
        if self.stock == 0:
            return 0
        # Contar apenas resgates aprovados e entregues
        approved_count = self.redemption_set.filter(status__in=['APROVADO', 'ENTREGUE']).count()
        return max(0, ((self.stock - approved_count) / self.stock) * 100)


class PrizeDiscount(models.Model):
    DISCOUNT_TYPE_CHOICES = [
        ('PERCENTAGE', 'Porcentagem'),
        ('FIXED', 'Valor Fixo'),
    ]
    
    name = models.CharField(max_length=200, verbose_name="Nome do Desconto")
    code = models.CharField(max_length=50, unique=True, verbose_name="Código", help_text="Código único para o desconto")
    description = models.TextField(blank=True, verbose_name="Descrição")
    discount_type = models.CharField(max_length=20, choices=DISCOUNT_TYPE_CHOICES, default='PERCENTAGE', verbose_name="Tipo de Desconto")
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor do Desconto", help_text="Porcentagem (ex: 10 para 10%) ou valor fixo em C$")
    min_purchase_value = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Valor Mínimo de Compra", help_text="Valor mínimo em C$ para aplicar o desconto")
    max_discount_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Desconto Máximo", help_text="Valor máximo de desconto em C$ (apenas para porcentagem)")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    valid_from = models.DateField(verbose_name="Válido de")
    valid_until = models.DateField(verbose_name="Válido até")
    max_uses = models.PositiveIntegerField(null=True, blank=True, verbose_name="Máximo de Usos", help_text="Número máximo de vezes que pode ser usado (deixe vazio para ilimitado)")
    uses_count = models.PositiveIntegerField(default=0, verbose_name="Quantidade de Usos")
    applies_to_categories = models.ManyToManyField(PrizeCategory, blank=True, verbose_name="Categorias Aplicáveis", help_text="Deixe vazio para aplicar a todos os prêmios")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, verbose_name="Criado por")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Desconto"
        verbose_name_plural = "Descontos"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} ({self.code})"
    
    @property
    def is_valid(self):
        """Verifica se o desconto está válido"""
        if not self.is_active:
            return False
        today = timezone.now().date()
        if self.valid_from > today or self.valid_until < today:
            return False
        if self.max_uses and self.uses_count >= self.max_uses:
            return False
        return True
    
    def can_apply_to_prize(self, prize):
        """Verifica se o desconto pode ser aplicado ao prêmio"""
        if not self.is_valid:
            return False
        if self.applies_to_categories.exists():
            return prize.category in self.applies_to_categories.all()
        return True
    
    def calculate_discount(self, prize_value):
        """Calcula o valor do desconto"""
        if prize_value < self.min_purchase_value:
            return Decimal('0.00')
        
        if self.discount_type == 'PERCENTAGE':
            discount = (prize_value * self.discount_value) / Decimal('100')
            if self.max_discount_value:
                discount = min(discount, self.max_discount_value)
            return discount
        else:  # FIXED
            return min(self.discount_value, prize_value)


class Redemption(models.Model):
    STATUS_CHOICES = [
        ('PENDENTE', 'Pendente'),
        ('APROVADO', 'Aprovado'),
        ('ENTREGUE', 'Entregue'),
        ('CANCELADO', 'Cancelado'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    prize = models.ForeignKey(Prize, on_delete=models.CASCADE, verbose_name="Prêmio")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDENTE', verbose_name="Status")
    discount = models.ForeignKey(PrizeDiscount, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Desconto Aplicado")
    original_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Valor Original")
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Valor do Desconto")
    final_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Valor Final")
    redeemed_at = models.DateTimeField(auto_now_add=True, verbose_name="Data do Resgate")
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Data da Aprovação")
    delivered_at = models.DateTimeField(null=True, blank=True, verbose_name="Data da Entrega")
    delivery_notes = models.TextField(blank=True, verbose_name="Observações de Entrega")
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
        ('REFUND', 'Reembolso'),
        ('ADJUSTMENT', 'Ajuste'),
    ]
    
    STATUS_CHOICES = [
        ('PENDING', 'Pendente'),
        ('APPROVED', 'Aprovado'),
        ('REJECTED', 'Rejeitado'),
    ]
    
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="Usuário")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Valor")
    transaction_type = models.CharField(max_length=15, choices=TRANSACTION_TYPES, verbose_name="Tipo")
    description = models.CharField(max_length=200, verbose_name="Descrição")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='APPROVED', verbose_name="Status")
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
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_transactions',
        verbose_name="Aprovado por"
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="Data de Aprovação")
    rejection_reason = models.TextField(blank=True, verbose_name="Motivo da Rejeição")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data")
    
    class Meta:
        verbose_name = "Transação C$"
        verbose_name_plural = "Transações C$"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.full_name} - {self.transaction_type} - C$ {self.amount}"
