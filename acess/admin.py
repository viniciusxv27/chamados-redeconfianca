from django.contrib import admin

from .models import LogAcess


@admin.register(LogAcess)
class LogAcessAdmin(admin.ModelAdmin):
    list_display = ('quando', 'usuario_nome', 'acao', 'categoria', 'descricao')
    list_filter = ('categoria',)
    search_fields = ('descricao', 'usuario_nome')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
