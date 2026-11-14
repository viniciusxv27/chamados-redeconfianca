from django.contrib import admin

from .models import AcessoUniformes, ItemUniforme, MovimentoUniforme


@admin.register(AcessoUniformes)
class AcessoUniformesAdmin(admin.ModelAdmin):
    list_display = ('user', 'liberado_por', 'criado_em')
    raw_id_fields = ('user', 'liberado_por')


@admin.register(ItemUniforme)
class ItemUniformeAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'tamanho', 'descricao', 'saldo', 'saldo_usado', 'estoque_minimo', 'ativo')
    list_filter = ('tipo', 'ativo')
    readonly_fields = ('saldo', 'saldo_usado')


@admin.register(MovimentoUniforme)
class MovimentoUniformeAdmin(admin.ModelAdmin):
    """Só leitura: o livro do estoque muda pelas telas do módulo (servicos.py)."""
    list_display = ('registrado_em', 'tipo', 'item', 'condicao', 'quantidade', 'delta', 'saldo_apos',
                    'colaborador', 'registrado_por')
    list_filter = ('tipo', 'condicao')
    raw_id_fields = ('item', 'colaborador', 'registrado_por', 'data_alterada_por')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
