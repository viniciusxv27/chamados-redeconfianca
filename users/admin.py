from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Sector


@admin.register(Sector)
class SectorAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('name', 'description')
    ordering = ('name',)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'hierarchy', 'primary_sector', 'is_active', 'created_at')
    list_filter = ('hierarchy', 'is_active', 'sectors', 'sector', 'created_at')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    
    # Adicionar campos personalizados aos fieldsets
    fieldsets = UserAdmin.fieldsets + (
        ('Informações Adicionais', {
            'fields': ('sector', 'sectors', 'hierarchy', 'balance_cs', 'phone', 'disc_profile', 'uniform_size_shirt', 'uniform_size_pants', 'profile_picture')
        }),
    )
    
    # Campos para múltiplos setores
    filter_horizontal = ('sectors',)
    
    def primary_sector(self, obj):
        return obj.primary_sector
    primary_sector.short_description = 'Setor Principal'
