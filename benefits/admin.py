from django.contrib import admin
from .models import Benefit, BenefitRedeem


@admin.register(Benefit)
class BenefitAdmin(admin.ModelAdmin):
    list_display = ['title', 'status', 'is_featured', 'views_count', 'redeems_count', 'valid_until', 'created_at']
    list_filter = ['status', 'is_featured', 'created_at']
    search_fields = ['title', 'description', 'coupon_code']
    readonly_fields = ['created_by', 'created_at', 'updated_at', 'views_count', 'redeems_count']
    
    fieldsets = (
        ('Informações Principais', {
            'fields': ('title', 'description', 'full_description', 'image')
        }),
        ('Cupom', {
            'fields': ('coupon_code',)
        }),
        ('Configurações', {
            'fields': ('status', 'is_featured', 'valid_from', 'valid_until')
        }),
        ('Estatísticas', {
            'fields': ('views_count', 'redeems_count'),
            'classes': ('collapse',)
        }),
        ('Metadados', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # Se está criando (não editando)
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(BenefitRedeem)
class BenefitRedeemAdmin(admin.ModelAdmin):
    list_display = ['benefit', 'user', 'redeemed_at']
    list_filter = ['redeemed_at', 'benefit']
    search_fields = ['benefit__title', 'user__username', 'user__first_name', 'user__last_name']
    readonly_fields = ['benefit', 'user', 'redeemed_at']
    
    def has_add_permission(self, request):
        return False  # Não permitir adicionar manualmente

