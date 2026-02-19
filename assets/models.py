from django.db import models
from django.conf import settings
from django.utils import timezone
from django.core.validators import MinValueValidator
from decimal import Decimal


def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage()
    return None


def upload_product_media(instance, filename):
    """Upload path para fotos/vídeos de produtos"""
    import os
    import uuid
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"product_{uuid.uuid4()}{ext}"
    return f"inventory/products/{unique_filename}"


def upload_inventory_item_media(instance, filename):
    """Upload path para fotos de itens de inventário"""
    import os
    import uuid
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"item_{uuid.uuid4()}{ext}"
    return f"inventory/items/{unique_filename}"


class InventoryCategory(models.Model):
    """Categorias de produtos no estoque"""
    name = models.CharField(max_length=100, verbose_name='Nome')
    description = models.TextField(blank=True, verbose_name='Descrição')
    color = models.CharField(max_length=7, default='#3B82F6', verbose_name='Cor')
    icon = models.CharField(max_length=50, default='fas fa-box', verbose_name='Ícone')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    
    class Meta:
        verbose_name = 'Categoria de Inventário'
        verbose_name_plural = 'Categorias de Inventário'
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Product(models.Model):
    """Modelo/Produto no estoque (ex: MacBook Pro 14")"""
    SIZE_CHOICES = [
        ('', 'Não aplicável'),
        ('PP', 'PP'),
        ('P', 'P'),
        ('M', 'M'),
        ('G', 'G'),
        ('GG', 'GG'),
        ('XGG', 'XGG'),
        ('UNICO', 'Tamanho Único'),
        ('PERSONALIZADO', 'Personalizado'),
    ]
    
    name = models.CharField(max_length=200, verbose_name='Nome do Produto')
    sku = models.CharField(max_length=50, unique=True, verbose_name='Código SKU', 
                          help_text='Código único do produto')
    description = models.TextField(blank=True, verbose_name='Descrição')
    category = models.ForeignKey(
        InventoryCategory, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='products',
        verbose_name='Categoria'
    )
    brand = models.CharField(max_length=100, blank=True, verbose_name='Marca')
    model = models.CharField(max_length=100, blank=True, verbose_name='Modelo')
    size = models.CharField(
        max_length=20, 
        choices=SIZE_CHOICES, 
        blank=True, 
        verbose_name='Tamanho'
    )
    size_custom = models.CharField(
        max_length=50, 
        blank=True, 
        verbose_name='Tamanho Personalizado',
        help_text='Especifique o tamanho se for personalizado'
    )
    unit_of_measure = models.CharField(
        max_length=20, 
        default='UN', 
        verbose_name='Unidade de Medida',
        help_text='Ex: UN, KG, L, M, CX'
    )
    min_stock = models.PositiveIntegerField(
        default=0, 
        verbose_name='Estoque Mínimo',
        help_text='Quantidade mínima para alerta'
    )
    max_stock = models.PositiveIntegerField(
        default=0, 
        blank=True,
        verbose_name='Estoque Máximo',
        help_text='Quantidade máxima recomendada (0 = sem limite)'
    )
    unit_cost = models.DecimalField(
        max_digits=10, 
        decimal_places=2, 
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Custo Unitário'
    )
    location = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name='Localização no Almoxarifado',
        help_text='Ex: Prateleira A-1, Corredor 3'
    )
    notes = models.TextField(blank=True, verbose_name='Observações')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    
    # Campos de auditoria
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='products_created',
        verbose_name='Criado por'
    )
    
    class Meta:
        verbose_name = 'Produto'
        verbose_name_plural = 'Produtos'
        ordering = ['name']
    
    def __str__(self):
        return f"{self.sku} - {self.name}"
    
    @property
    def current_stock(self):
        """Retorna a quantidade atual em estoque"""
        return self.inventory_items.filter(status='available').count()
    
    @property
    def total_items(self):
        """Retorna o total de itens (incluindo em uso)"""
        return self.inventory_items.count()
    
    @property
    def is_low_stock(self):
        """Verifica se está abaixo do estoque mínimo"""
        return self.current_stock < self.min_stock
    
    def get_size_display_full(self):
        """Retorna o tamanho completo"""
        if self.size == 'PERSONALIZADO' and self.size_custom:
            return self.size_custom
        return self.get_size_display() if self.size else ''
    
    @property
    def primary_image(self):
        """Retorna a imagem principal do produto"""
        media = self.media.filter(media_type='image', is_primary=True).first()
        if not media:
            media = self.media.filter(media_type='image').first()
        return media


class ProductMedia(models.Model):
    """Fotos e vídeos do produto (modelo)"""
    MEDIA_TYPE_CHOICES = [
        ('image', 'Imagem'),
        ('video', 'Vídeo'),
    ]
    
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='media',
        verbose_name='Produto'
    )
    media_type = models.CharField(
        max_length=10,
        choices=MEDIA_TYPE_CHOICES,
        default='image',
        verbose_name='Tipo de Mídia'
    )
    file = models.FileField(
        upload_to=upload_product_media,
        storage=get_media_storage(),
        verbose_name='Arquivo'
    )
    title = models.CharField(max_length=100, blank=True, verbose_name='Título')
    is_primary = models.BooleanField(default=False, verbose_name='Imagem Principal')
    order = models.PositiveIntegerField(default=0, verbose_name='Ordem')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    
    class Meta:
        verbose_name = 'Mídia do Produto'
        verbose_name_plural = 'Mídias do Produto'
        ordering = ['order', '-is_primary', 'created_at']
    
    def __str__(self):
        return f"{self.product.name} - {self.get_media_type_display()}"
    
    def save(self, *args, **kwargs):
        # Se marcado como principal, desmarca os outros
        if self.is_primary:
            ProductMedia.objects.filter(
                product=self.product, 
                is_primary=True
            ).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)


class InventoryItem(models.Model):
    """Item individual de inventário com número único"""
    STATUS_CHOICES = [
        ('available', 'Disponível'),
        ('in_use', 'Em Uso'),
        ('maintenance', 'Em Manutenção'),
        ('damaged', 'Danificado'),
        ('disposed', 'Descartado'),
    ]
    
    CONDITION_CHOICES = [
        ('new', 'Novo'),
        ('excellent', 'Excelente'),
        ('good', 'Bom'),
        ('fair', 'Regular'),
        ('poor', 'Ruim'),
    ]
    
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='inventory_items',
        verbose_name='Produto'
    )
    inventory_number = models.CharField(
        max_length=50, 
        unique=True, 
        verbose_name='Número de Inventário',
        help_text='Código único do item'
    )
    serial_number = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name='Número de Série'
    )
    batch_number = models.CharField(
        max_length=50, 
        blank=True, 
        verbose_name='Número do Lote'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='available',
        verbose_name='Status'
    )
    condition = models.CharField(
        max_length=20,
        choices=CONDITION_CHOICES,
        default='new',
        verbose_name='Condição'
    )
    purchase_date = models.DateField(
        null=True, 
        blank=True, 
        verbose_name='Data de Compra'
    )
    purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0.00'))],
        verbose_name='Preço de Compra'
    )
    warranty_expiry = models.DateField(
        null=True, 
        blank=True, 
        verbose_name='Validade da Garantia'
    )
    location = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name='Localização Atual'
    )
    notes = models.TextField(blank=True, verbose_name='Observações')
    photo = models.ImageField(
        upload_to=upload_inventory_item_media,
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Foto do Item'
    )
    
    # Campos de responsável atual
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_inventory_items',
        verbose_name='Responsável Atual'
    )
    assigned_sector = models.ForeignKey(
        'users.Sector',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_inventory_items',
        verbose_name='Setor Atual'
    )
    assigned_date = models.DateTimeField(
        null=True, 
        blank=True, 
        verbose_name='Data de Atribuição'
    )
    
    # Campos de auditoria
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='inventory_items_created',
        verbose_name='Criado por'
    )
    
    class Meta:
        verbose_name = 'Item de Inventário'
        verbose_name_plural = 'Itens de Inventário'
        ordering = ['inventory_number']
    
    def __str__(self):
        return f"{self.inventory_number} - {self.product.name}"
    
    @property
    def is_warranty_valid(self):
        """Verifica se a garantia ainda é válida"""
        if self.warranty_expiry:
            return self.warranty_expiry >= timezone.now().date()
        return False


class StockMovement(models.Model):
    """Movimentação de estoque (entrada e saída)"""
    MOVEMENT_TYPE_CHOICES = [
        ('entry', 'Entrada'),
        ('exit', 'Saída'),
        ('transfer', 'Transferência'),
        ('adjustment', 'Ajuste'),
        ('return', 'Devolução'),
    ]
    
    REASON_CHOICES = [
        # Entradas
        ('purchase', 'Compra'),
        ('donation', 'Doação'),
        ('return_from_user', 'Devolução de Usuário'),
        ('return_from_maintenance', 'Retorno de Manutenção'),
        ('inventory_adjustment', 'Ajuste de Inventário'),
        ('initial_stock', 'Estoque Inicial'),
        # Saídas
        ('assigned_to_user', 'Atribuído a Usuário'),
        ('assigned_to_sector', 'Atribuído a Setor'),
        ('maintenance', 'Enviado para Manutenção'),
        ('disposed', 'Descarte'),
        ('lost', 'Perda/Extravio'),
        ('damaged', 'Dano'),
        ('other', 'Outro'),
    ]
    
    inventory_item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name='movements',
        verbose_name='Item de Inventário'
    )
    movement_type = models.CharField(
        max_length=20,
        choices=MOVEMENT_TYPE_CHOICES,
        verbose_name='Tipo de Movimentação'
    )
    reason = models.CharField(
        max_length=30,
        choices=REASON_CHOICES,
        verbose_name='Motivo'
    )
    
    # Campos de destino/origem
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements_from',
        verbose_name='De (Usuário)'
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements_to',
        verbose_name='Para (Usuário)'
    )
    from_sector = models.ForeignKey(
        'users.Sector',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements_from',
        verbose_name='De (Setor)'
    )
    to_sector = models.ForeignKey(
        'users.Sector',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='stock_movements_to',
        verbose_name='Para (Setor)'
    )
    from_location = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name='De (Local)'
    )
    to_location = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name='Para (Local)'
    )
    
    notes = models.TextField(blank=True, verbose_name='Observações')
    document_reference = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name='Referência do Documento',
        help_text='Ex: Número da NF, requisição, etc.'
    )
    
    # Campos de auditoria
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data/Hora')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='stock_movements_created',
        verbose_name='Registrado por'
    )
    
    class Meta:
        verbose_name = 'Movimentação de Estoque'
        verbose_name_plural = 'Movimentações de Estoque'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.get_movement_type_display()} - {self.inventory_item.inventory_number}"


class InventoryManager(models.Model):
    """Usuários responsáveis pelo almoxarifado"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='inventory_manager_profile',
        verbose_name='Usuário'
    )
    can_manage_products = models.BooleanField(
        default=True, 
        verbose_name='Pode gerenciar produtos'
    )
    can_manage_items = models.BooleanField(
        default=True, 
        verbose_name='Pode gerenciar itens'
    )
    can_register_entries = models.BooleanField(
        default=True, 
        verbose_name='Pode registrar entradas'
    )
    can_register_exits = models.BooleanField(
        default=True, 
        verbose_name='Pode registrar saídas'
    )
    can_view_reports = models.BooleanField(
        default=True, 
        verbose_name='Pode visualizar relatórios'
    )
    can_manage_managers = models.BooleanField(
        default=False, 
        verbose_name='Pode gerenciar outros gestores'
    )
    can_approve_requests = models.BooleanField(
        default=True,
        verbose_name='Pode aprovar/reprovar solicitações'
    )
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    
    # Campos de auditoria
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='inventory_managers_created',
        verbose_name='Criado por'
    )
    
    class Meta:
        verbose_name = 'Gestor de Inventário'
        verbose_name_plural = 'Gestores de Inventário'
        ordering = ['user__first_name', 'user__last_name']
    
    def __str__(self):
        return f"{self.user.get_full_name() or self.user.email}"


class ItemRequest(models.Model):
    """Solicitação de itens do almoxarifado (pode conter múltiplos itens)"""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('counterproposal', 'Contraproposta'),
        ('accepted', 'Aceita pelo Solicitante'),
        ('approved', 'Aprovada'),
        ('rejected', 'Rejeitada'),
        ('delivered', 'Entregue'),
        ('cancelled', 'Cancelada'),
    ]
    
    reason = models.TextField(
        verbose_name='Motivo da Solicitação',
        help_text='Descreva o motivo pelo qual precisa destes itens'
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status'
    )
    
    # Quem solicitou
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='item_requests_made',
        verbose_name='Solicitado por'
    )
    requested_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Data da Solicitação'
    )
    
    # Quem aprovou/reprovou
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='item_requests_reviewed',
        verbose_name='Revisado por'
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data da Revisão'
    )
    review_notes = models.TextField(
        blank=True,
        verbose_name='Observações da Revisão'
    )
    
    # Contraproposta
    counterproposal_notes = models.TextField(
        blank=True,
        verbose_name='Observações da Contraproposta',
        help_text='Explicação do motivo da contraproposta'
    )
    counterproposal_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='item_requests_counterproposed',
        verbose_name='Contraproposta por'
    )
    counterproposal_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data da Contraproposta'
    )
    
    # Resposta à contraproposta
    counterproposal_response_notes = models.TextField(
        blank=True,
        verbose_name='Observações da Resposta à Contraproposta'
    )
    counterproposal_responded_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data da Resposta à Contraproposta'
    )
    
    # Entrega
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='item_requests_delivered',
        verbose_name='Entregue por'
    )
    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data da Entrega'
    )
    delivery_notes = models.TextField(
        blank=True,
        verbose_name='Observações da Entrega'
    )
    
    class Meta:
        verbose_name = 'Solicitação de Item'
        verbose_name_plural = 'Solicitações de Itens'
        ordering = ['-requested_at']
    
    def __str__(self):
        count = self.items.count()
        return f"#{self.pk} - {count} item(ns) - {self.get_status_display()}"
    
    @property
    def can_approve(self):
        return self.status in ['pending', 'accepted']
    
    @property
    def can_counterpropose(self):
        return self.status == 'pending'
    
    @property
    def can_respond_counterproposal(self):
        return self.status == 'counterproposal'
    
    @property
    def can_deliver(self):
        return self.status == 'approved'
    
    @property
    def can_cancel(self):
        return self.status in ['pending', 'approved', 'counterproposal', 'accepted']
    
    @property
    def has_enough_stock(self):
        """Verifica se há estoque suficiente para todos os itens"""
        for item in self.items.select_related('product').all():
            qty = item.effective_quantity
            if qty > 0 and item.product.current_stock < qty:
                return False
        return True
    
    @property
    def total_items_count(self):
        """Total de itens diferentes na solicitação"""
        return self.items.count()
    
    @property
    def total_quantity(self):
        """Quantidade total somando todos os itens"""
        from django.db.models import Sum
        return self.items.aggregate(total=Sum('quantity'))['total'] or 0


class ItemRequestItem(models.Model):
    """Item individual dentro de uma solicitação"""
    request = models.ForeignKey(
        ItemRequest,
        on_delete=models.CASCADE,
        related_name='items',
        verbose_name='Solicitação'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='request_items',
        verbose_name='Produto'
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name='Quantidade Solicitada'
    )
    # Contraproposta: quantidade proposta pelo gestor
    proposed_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name='Quantidade Proposta',
        help_text='Quantidade sugerida pelo gestor na contraproposta (0 = item removido)'
    )
    
    class Meta:
        verbose_name = 'Item da Solicitação'
        verbose_name_plural = 'Itens da Solicitação'
        unique_together = ['request', 'product']
    
    def __str__(self):
        return f"{self.product.name} x{self.quantity}"
    
    @property
    def effective_quantity(self):
        """Quantidade efetiva: proposta aceita ou quantidade original"""
        if self.request.status in ['accepted', 'approved', 'delivered'] and self.proposed_quantity is not None:
            return self.proposed_quantity
        return self.quantity
    
    @property
    def has_enough_stock(self):
        return self.product.current_stock >= self.effective_quantity
    
    @property
    def quantity_changed(self):
        """Verifica se a quantidade foi alterada na contraproposta"""
        return self.proposed_quantity is not None and self.proposed_quantity != self.quantity


# ============================================================================
# MODELO LEGADO - MANTIDO PARA COMPATIBILIDADE (pode ser removido no futuro)
# ============================================================================
class Asset(models.Model):
    """Modelo legado de ativo imobilizado - DEPRECATED"""
    ESTADO_FISICO_CHOICES = [
        ('excelente', 'Excelente'),
        ('bom', 'Bom'),
        ('regular', 'Regular'),
        ('ruim', 'Ruim'),
        ('pessimo', 'Péssimo'),
    ]

    patrimonio_numero = models.CharField(max_length=20, unique=True, verbose_name='N° Patrimônio')
    nome = models.CharField(max_length=200, verbose_name='Nome')
    imei_serial = models.CharField(max_length=100, blank=True, null=True, verbose_name='IMEI/Serial')
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
    photo = models.ImageField(
        upload_to='assets/', 
        blank=True, 
        null=True, 
        verbose_name='Foto do Asset'
    )
    
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
        verbose_name = 'Ativo (Legado)'
        verbose_name_plural = 'Ativos (Legado)'
        ordering = ['patrimonio_numero']

    def __str__(self):
        return f"{self.patrimonio_numero} - {self.nome}"
