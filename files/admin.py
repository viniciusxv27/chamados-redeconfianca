from django.contrib import admin
from .models import FileCategory, SharedFile, FileDownload, Folder, MovimentacaoArquivo


class SemExclusao:
    """/files/ nunca apaga de vez: excluir é mandar para a lixeira, pela tela."""

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Folder)
class FolderAdmin(SemExclusao, admin.ModelAdmin):
    list_display = ('name', 'parent', 'visibility', 'excluido_em', 'created_at')
    list_filter = ('visibility', 'is_active')
    search_fields = ('name',)
    filter_horizontal = ('allowed_users',)


@admin.register(MovimentacaoArquivo)
class MovimentacaoArquivoAdmin(SemExclusao, admin.ModelAdmin):
    list_display = ('quando', 'acao', 'tipo', 'item_nome', 'usuario', 'pessoa', 'ip')
    list_filter = ('acao', 'tipo', 'quando')
    search_fields = ('item_nome', 'detalhe', 'usuario__first_name', 'usuario__last_name')
    readonly_fields = [f.name for f in MovimentacaoArquivo._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(FileCategory)
class FileCategoryAdmin(SemExclusao, admin.ModelAdmin):
    list_display = ('name', 'description', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description')
    ordering = ('name',)


@admin.register(SharedFile)
class SharedFileAdmin(SemExclusao, admin.ModelAdmin):
    list_display = ('title', 'category', 'uploaded_by', 'visibility', 'downloads', 'is_active', 'created_at')
    list_filter = ('category', 'visibility', 'is_active', 'created_at', 'uploaded_by')
    search_fields = ('title', 'description', 'uploaded_by__first_name', 'uploaded_by__last_name')
    readonly_fields = ('file_size', 'downloads', 'created_at', 'updated_at')
    filter_horizontal = ('allowed_users',)
    ordering = ('-created_at',)
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('title', 'description', 'file', 'category')
        }),
        ('Visibilidade', {
            'fields': ('visibility', 'target_sector', 'target_user', 'allowed_users')
        }),
        ('Metadados', {
            'fields': ('uploaded_by', 'file_size', 'downloads', 'is_active'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(FileDownload)
class FileDownloadAdmin(SemExclusao, admin.ModelAdmin):
    list_display = ('file', 'user', 'downloaded_at', 'ip_address')
    list_filter = ('downloaded_at', 'file__category')
    search_fields = ('file__title', 'user__first_name', 'user__last_name', 'user__email')
    readonly_fields = ('file', 'user', 'downloaded_at', 'ip_address')
    ordering = ('-downloaded_at',)
    
    def has_add_permission(self, request):
        return False  # Não permitir adicionar logs manualmente
