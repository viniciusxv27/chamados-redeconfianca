from django.contrib import admin

from .models import ItemPreco, Venda, VendaProduto, VendaServico


class VendaProdutoInline(admin.TabularInline):
    model = VendaProduto
    extra = 0
    raw_id_fields = ['preco']


class VendaServicoInline(admin.TabularInline):
    model = VendaServico
    extra = 0
    raw_id_fields = ['preco']


@admin.register(Venda)
class VendaAdmin(admin.ModelAdmin):
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
