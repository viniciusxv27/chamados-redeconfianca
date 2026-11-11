from django.contrib import admin

from .models import AcessoContasPagar, Categoria, ContaBancaria, LogContasPagar


@admin.register(ContaBancaria)
class ContaBancariaAdmin(admin.ModelAdmin):
    list_display = ('banco', 'agencia', 'conta', 'ordem', 'ativo')


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'categoria')
    search_fields = ('tipo', 'categoria')


@admin.register(AcessoContasPagar)
class AcessoContasPagarAdmin(admin.ModelAdmin):
    list_display = ('user', 'papel', 'criado_em')


@admin.register(LogContasPagar)
class LogContasPagarAdmin(admin.ModelAdmin):
    """Só leitura: é o registro de quem publicou/excluiu cada dia."""
    list_display = ('em', 'acao', 'user', 'data_pagamento', 'qtd_titulos', 'valor')
    list_filter = ('acao',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
