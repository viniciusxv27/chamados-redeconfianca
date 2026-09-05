from django.contrib import admin

from .models import (DriveAuditLog, DriveConfig, DriveFavorite, DrivePermission,
                     SectorDriveMapping)


@admin.register(DriveConfig)
class DriveConfigAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'ativo', 'max_file_mb', 'trash_retention_days', 'atualizado_em')

    def has_add_permission(self, request):
        return not DriveConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SectorDriveMapping)
class SectorDriveMappingAdmin(admin.ModelAdmin):
    list_display = ('sector', 'folder_id', 'folder_name', 'ativo', 'atualizado_em')
    search_fields = ('sector__name', 'folder_id', 'folder_name')
    filter_horizontal = ('managers',)
    raw_id_fields = ('sector',)


@admin.register(DrivePermission)
class DrivePermissionAdmin(admin.ModelAdmin):
    list_display = ('mapping', 'alvo', 'alvo_label', 'nivel', 'folder_name', 'criado_em')
    list_filter = ('alvo', 'nivel')
    search_fields = ('mapping__sector__name', 'target_user__first_name', 'target_group__name')
    raw_id_fields = ('mapping', 'target_user', 'target_group', 'target_sector', 'criado_por')


@admin.register(DriveFavorite)
class DriveFavoriteAdmin(admin.ModelAdmin):
    list_display = ('user', 'file_name', 'sector', 'criado_em')
    search_fields = ('user__first_name', 'file_name')
    raw_id_fields = ('user', 'sector')


@admin.register(DriveAuditLog)
class DriveAuditLogAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'user', 'acao', 'file_name', 'sector', 'ip')
    list_filter = ('acao', 'criado_em')
    search_fields = ('user__first_name', 'file_name', 'file_id')
    date_hierarchy = 'criado_em'
    readonly_fields = [f.name for f in DriveAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False
