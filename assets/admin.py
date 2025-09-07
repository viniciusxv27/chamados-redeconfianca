from django.contrib import admin
from .models import Asset


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
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
        if not change:  # Se é um novo objeto
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
