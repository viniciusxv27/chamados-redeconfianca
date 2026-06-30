from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Sector, CommissionMonthlyTotal, AParteCommissionConfig


@admin.register(CommissionMonthlyTotal)
class CommissionMonthlyTotalAdmin(admin.ModelAdmin):
    list_display = ('person_name', 'role', 'month', 'year', 'total_commission', 'synced_at', 'synced_by')
    list_filter = ('year', 'month', 'role')
    search_fields = ('person_name',)
    ordering = ('-year', '-month', 'person_name')


@admin.register(AParteCommissionConfig)
class AParteCommissionConfigAdmin(admin.ModelAdmin):
    list_display = ('user', 'base_salary', 'is_active', 'updated_at', 'updated_by')
    list_filter = ('is_active',)
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
    raw_id_fields = ('user',)


@admin.register(Sector)
class SectorAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('name', 'description')
    ordering = ('name',)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'hierarchy', 'primary_sector', 'status', 'is_active', 'created_at')
    list_filter = ('hierarchy', 'status', 'is_active', 'sectors', 'sector', 'created_at')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)

    # Adicionar campos personalizados aos fieldsets
    fieldsets = UserAdmin.fieldsets + (
        ('Informações Adicionais', {
            'fields': ('sector', 'sectors', 'hierarchy', 'balance_cs', 'phone', 'disc_profile', 'uniform_size_shirt', 'uniform_size_pants', 'profile_picture')
        }),
        ('Situação do Colaborador', {
            'fields': ('status', 'inactivation_reason', 'leave_reason', 'leave_attachment')
        }),
    )
    
    # Campos para múltiplos setores
    filter_horizontal = ('sectors',)
    
    def primary_sector(self, obj):
        return obj.primary_sector
    primary_sector.short_description = 'Setor Principal'
