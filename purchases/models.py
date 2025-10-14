from django.db import models
from django.conf import settings
from django.utils import timezone
from suppliers.models import Supplier

from suppliers.models import Supplier


class PaymentMethod(models.Model):
    """Modelo para gerenciar formas de pagamento"""
    
    name = models.CharField(
        max_length=100,
        verbose_name="Nome",
        help_text="Ex: Cartão de Crédito, Boleto, PIX, etc."
    )
    description = models.TextField(
        blank=True,
        verbose_name="Descrição",
        help_text="Detalhes sobre esta forma de pagamento"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Ativo"
    )
    requires_approval = models.BooleanField(
        default=False,
        verbose_name="Requer Aprovação",
        help_text="Compras com esta forma de pagamento precisam de aprovação"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Forma de Pagamento"
        verbose_name_plural = "Formas de Pagamento"
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Purchase(models.Model):
    """Model para compras vinculadas aos fornecedores"""
    
    STATUS_CHOICES = [
        ('SOLICITADA', 'Solicitada'),
        ('APROVADA', 'Aprovada'),
        ('EM_PROCESSAMENTO', 'Em Processamento'),
        ('ENVIADA', 'Enviada'),
        ('ENTREGUE', 'Entregue'),
        ('CANCELADA', 'Cancelada'),
    ]
    
    PRIORITY_CHOICES = [
        ('BAIXA', 'Baixa'),
        ('MEDIA', 'Média'), 
        ('ALTA', 'Alta'),
        ('URGENTE', 'Urgente'),
    ]
    
    # Relacionamento com fornecedor
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.CASCADE,
        related_name='purchases',
        verbose_name="Fornecedor"
    )
    
    # Informações básicas da compra
    description = models.TextField(
        verbose_name="Descrição",
        help_text="Detalhes dos itens/serviços solicitados"
    )
    quantity = models.PositiveIntegerField(
        verbose_name="Quantidade",
        default=1
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Preço Unitário"
    )
    total_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Preço Total"
    )
    
    # Status e controle
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='SOLICITADA',
        verbose_name="Status"
    )
    priority = models.CharField(
        max_length=10,
        choices=PRIORITY_CHOICES,
        default='MEDIA',
        verbose_name="Prioridade"
    )
    
    # Datas
    request_date = models.DateField(
        default=timezone.now,
        verbose_name="Data da Solicitação"
    )
    expected_delivery = models.DateField(
        null=True,
        blank=True,
        verbose_name="Entrega Prevista"
    )
    delivery_date = models.DateField(
        null=True,
        blank=True,
        verbose_name="Data de Entrega"
    )
    
    # Usuário responsável
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='requested_purchases',
        verbose_name="Solicitado por"
    )
    
    # Forma de Pagamento
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchases',
        verbose_name="Forma de Pagamento"
    )
    
    # Observações
    notes = models.TextField(
        blank=True,
        verbose_name="Observações",
        help_text="Observações adicionais sobre a compra"
    )
    
    # Livelo (Programa de Pontos)
    accumulated_livelo_points = models.BooleanField(
        default=False,
        verbose_name="Acumulou Pontos Livelo"
    )
    livelo_points_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Quantidade de Pontos Livelo"
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Compra"
        verbose_name_plural = "Compras"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.supplier.name} - {self.description[:50]}"
    
    @property
    def status_class(self):
        """Retorna classe CSS baseada no status"""
        classes = {
            'SOLICITADA': 'bg-yellow-100 text-yellow-800',
            'APROVADA': 'bg-blue-100 text-blue-800',
            'EM_PROCESSAMENTO': 'bg-purple-100 text-purple-800',
            'ENVIADA': 'bg-indigo-100 text-indigo-800',
            'ENTREGUE': 'bg-green-100 text-green-800',
            'CANCELADA': 'bg-red-100 text-red-800',
        }
        return classes.get(self.status, 'bg-gray-100 text-gray-800')
    
    @property
    def is_overdue(self):
        """Verifica se a compra está atrasada"""
        if self.status in ['ENTREGUE', 'CANCELADA']:
            return False
        if self.expected_delivery:
            return timezone.now().date() > self.expected_delivery
        return False
    
    def save(self, *args, **kwargs):
        # Calcular preço total automaticamente
        if self.unit_price and self.quantity:
            self.total_price = self.unit_price * self.quantity
        super().save(*args, **kwargs)