from django.contrib import admin
from .models import (
    Asset, InventoryCategory, Product, ProductMedia, 
    InventoryItem, StockMovement, InventoryManager
)


# ============================================
# NOVO SISTEMA DE INVENTÁRIO
# ============================================

@admin.register(InventoryCategory)
class InventoryCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'product_count', 'created_at']
    search_fields = ['name', 'description']
    ordering = ['name']
    
    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Produtos'


class ProductMediaInline(admin.TabularInline):
    model = ProductMedia
    extra = 1
    fields = ['file', 'media_type', 'is_primary', 'order']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['sku', 'name', 'category', 'size', 'current_stock', 'min_stock', 'is_active', 'created_at']
    list_filter = ['category', 'size', 'is_active', 'created_at']
    search_fields = ['sku', 'name', 'description']
    readonly_fields = ['created_at', 'updated_at']
    list_per_page = 25
    date_hierarchy = 'created_at'
    inlines = [ProductMediaInline]
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('sku', 'name', 'description', 'category')
        }),
        ('Detalhes', {
            'fields': ('size', 'brand', 'model', 'unit')
        }),
        ('Estoque', {
            'fields': ('min_stock', 'is_active')
        }),
        ('Auditoria', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def current_stock(self, obj):
        return obj.current_stock
    current_stock.short_description = 'Estoque Atual'
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ProductMedia)
class ProductMediaAdmin(admin.ModelAdmin):
    list_display = ['product', 'media_type', 'is_primary', 'order', 'created_at']
    list_filter = ['media_type', 'is_primary', 'created_at']
    search_fields = ['product__name', 'product__sku']
    list_per_page = 25


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = [
        'inventory_number', 'product', 'status', 'condition', 
        'location', 'assigned_to', 'assigned_sector', 'created_at'
    ]
    list_filter = ['status', 'condition', 'product__category', 'assigned_sector', 'created_at']
    search_fields = ['inventory_number', 'serial_number', 'batch_number', 'product__name']
    readonly_fields = ['created_at', 'updated_at']
    list_per_page = 25
    date_hierarchy = 'created_at'
    autocomplete_fields = ['product', 'assigned_to', 'assigned_sector']
    
    fieldsets = (
        ('Identificação', {
            'fields': ('product', 'inventory_number', 'serial_number', 'batch_number')
        }),
        ('Status', {
            'fields': ('status', 'condition', 'location')
        }),
        ('Atribuição', {
            'fields': ('assigned_to', 'assigned_sector', 'assigned_date')
        }),
        ('Informações de Compra', {
            'fields': ('purchase_date', 'purchase_price', 'warranty_expiry')
        }),
        ('Mídia e Observações', {
            'fields': ('photo', 'notes')
        }),
        ('Auditoria', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = [
        'inventory_item', 'movement_type', 'reason', 'from_user', 'to_user', 
        'to_sector', 'created_by', 'created_at'
    ]
    list_filter = ['movement_type', 'reason', 'created_at']
    search_fields = [
        'inventory_item__inventory_number', 'inventory_item__product__name',
        'from_user__email', 'to_user__email', 'notes'
    ]
    readonly_fields = ['created_at']
    list_per_page = 25
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('Movimentação', {
            'fields': ('inventory_item', 'movement_type', 'reason')
        }),
        ('Origem', {
            'fields': ('from_user', 'from_sector', 'from_location')
        }),
        ('Destino', {
            'fields': ('to_user', 'to_sector', 'to_location')
        }),
        ('Observações', {
            'fields': ('notes', 'document_reference')
        }),
        ('Auditoria', {
            'fields': ('created_by', 'created_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(InventoryManager)
class InventoryManagerAdmin(admin.ModelAdmin):
    list_display = [
        'user', 'can_manage_products', 'can_manage_items', 
        'can_register_entries', 'can_register_exits', 'can_view_reports', 'is_active', 'created_at'
    ]
    list_filter = ['can_manage_products', 'can_manage_items', 'can_register_entries', 'can_register_exits', 'can_view_reports', 'is_active']
    search_fields = ['user__email', 'user__first_name', 'user__last_name']
    autocomplete_fields = ['user']
    
    fieldsets = (
        ('Usuário', {
            'fields': ('user', 'is_active')
        }),
        ('Permissões', {
            'fields': (
                'can_manage_products', 'can_manage_items', 
                'can_register_entries', 'can_register_exits',
                'can_view_reports', 'can_manage_managers'
            )
        }),
    )


# ============================================
# SISTEMA LEGADO DE ATIVOS (DEPRECATED)
# ============================================

@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    """
    Admin do sistema legado de ativos.
    DEPRECATED: Use o novo sistema de inventário.
    """
    list_display = [
        'patrimonio_numero', 
        'nome', 
        'localizado', 
        'setor', 
        'pdv', 
        'estado_fisico',
        'has_photo',
        'created_by',
        'created_at'
    ]
    list_filter = [
        'estado_fisico', 
        'setor', 
        'created_at',
        'updated_at'
    ]
    search_fields = [
        'patrimonio_numero', 
        'nome', 
        'localizado', 
        'setor', 
        'pdv'
    ]
    readonly_fields = [
        'created_at', 
        'updated_at'
    ]
    list_per_page = 25
    date_hierarchy = 'created_at'
    
    fieldsets = (
        ('⚠️ SISTEMA LEGADO', {
            'description': 'Este é o sistema antigo de ativos. Use o novo sistema de Inventário.',
            'fields': ()
        }),
        ('Informações Básicas', {
            'fields': ('patrimonio_numero', 'nome', 'photo')
        }),
        ('Localização', {
            'fields': ('localizado', 'setor', 'pdv')
        }),
        ('Estado e Observações', {
            'fields': ('estado_fisico', 'observacoes')
        }),
        ('Auditoria', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def has_photo(self, obj):
        """Indica se o asset tem foto"""
        return bool(obj.photo)
    has_photo.boolean = True
    has_photo.short_description = 'Tem Foto'
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
