from django.contrib import admin
from .models import Prize, PrizeCategory, Redemption, CSTransaction


@admin.register(PrizeCategory)
class PrizeCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'icon', 'color', 'active', 'created_at']
    list_filter = ['active', 'color', 'created_at']
    search_fields = ['name', 'description']
    list_editable = ['active']
    ordering = ['name']


@admin.register(Prize)
class PrizeAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'value_cs', 'priority', 'stock', 'unlimited_stock', 'is_active', 'redeemed_count']
    list_filter = ['category', 'priority', 'is_active', 'unlimited_stock', 'created_at']
    search_fields = ['name', 'description']
    list_editable = ['is_active', 'priority']
    readonly_fields = ['redeemed_count', 'created_at', 'updated_at']
    ordering = ['-priority', 'name']
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('name', 'description', 'category', 'image')
        }),
        ('Configurações', {
            'fields': ('value_cs', 'priority', 'is_active')
        }),
        ('Estoque', {
            'fields': ('stock', 'unlimited_stock', 'redeemed_count')
        }),
        ('Detalhes', {
            'fields': ('terms', 'valid_until')
        }),
        ('Metadados', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # Se é um novo objeto
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Redemption)
class RedemptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'prize', 'status', 'redeemed_at', 'approved_by']
    list_filter = ['status', 'redeemed_at', 'approved_at', 'delivered_at']
    search_fields = ['user__first_name', 'user__last_name', 'user__email', 'prize__name']
    readonly_fields = ['redeemed_at', 'approved_at', 'delivered_at']
    ordering = ['-redeemed_at']
    
    fieldsets = (
        ('Resgate', {
            'fields': ('user', 'prize', 'status')
        }),
        ('Aprovação', {
            'fields': ('approved_by', 'approved_at')
        }),
        ('Datas', {
            'fields': ('redeemed_at', 'delivered_at')
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if change and 'status' in form.changed_data:
            if obj.status in ['APPROVED', 'ENTREGUE'] and not obj.approved_by:
                obj.approved_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(CSTransaction)
class CSTransactionAdmin(admin.ModelAdmin):
    list_display = ['user', 'amount', 'transaction_type', 'description', 'created_at']
    list_filter = ['transaction_type', 'created_at']
    search_fields = ['user__first_name', 'user__last_name', 'user__email', 'description']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    
    fieldsets = (
        ('Transação', {
            'fields': ('user', 'amount', 'transaction_type', 'description')
        }),
        ('Relacionamentos', {
            'fields': ('related_communication', 'related_redemption', 'related_ticket')
        }),
        ('Metadados', {
            'fields': ('created_by', 'created_at')
        }),
    )
