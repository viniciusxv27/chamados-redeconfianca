from django.contrib import admin

from .models import LogFinanceiro


@admin.register(LogFinanceiro)
class LogFinanceiroAdmin(admin.ModelAdmin):
    """Só leitura: a trilha não se edita nem se apaga."""

    list_display = ('quando', 'usuario_nome', 'acao', 'area', 'descricao', 'valor', 'ip')
    list_filter = ('acao', 'area')
    search_fields = ('descricao', 'usuario_nome', 'ip')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
