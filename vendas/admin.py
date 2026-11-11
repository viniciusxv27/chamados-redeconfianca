from django.contrib import admin

from .models import ItemPreco, Venda, VendaProduto, VendaServico


class SoLeitura:
    """Venda só se edita/exclui pela tela do SLV (/vendas/<id>/editar/), que grava o histórico
    com motivo. Pelo admin do Django não ficaria rastro — então aqui é só consulta (11/11/2026)."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class VendaProdutoInline(SoLeitura, admin.TabularInline):
    model = VendaProduto
    extra = 0
    raw_id_fields = ['preco']


class VendaServicoInline(SoLeitura, admin.TabularInline):
    model = VendaServico
    extra = 0
    raw_id_fields = ['preco']


@admin.register(Venda)
class VendaAdmin(SoLeitura, admin.ModelAdmin):
    list_display = ['id', 'data_venda', 'loja', 'vendedor', 'cliente_nome',
                    'tipo_venda', 'comprovante_fiscal', 'created_at']
    list_filter = ['comprovante_fiscal', 'estoque_avancado', 'data_venda', 'loja']
    search_fields = ['cliente_nome', 'cliente_cpf', 'pdv_nome']
    raw_id_fields = ['loja', 'vendedor', 'created_by']
    date_hierarchy = 'data_venda'
    inlines = [VendaProdutoInline, VendaServicoInline]


@admin.register(ItemPreco)
class ItemPrecoAdmin(admin.ModelAdmin):
    list_display = ['categoria', 'nome', 'plano', 'sistema', 'cod_sap', 'valor', 'ativo']
    list_filter = ['categoria', 'ativo']
    search_fields = ['nome', 'plano', 'cod_sap', 'cod_sistema']
