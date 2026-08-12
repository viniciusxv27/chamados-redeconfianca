from django.contrib import admin

from .models import Cartao, Gasto


@admin.register(Cartao)
class CartaoAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'apelido', 'responsavel', 'bandeira',
                    'validade_display', 'ativo', 'created_at']
    list_filter = ['bandeira', 'ativo']
    search_fields = ['apelido', 'last4', 'first4',
                     'responsavel__first_name', 'responsavel__last_name', 'responsavel__email']
    raw_id_fields = ['responsavel', 'created_by']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Gasto)
class GastoAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'cartao', 'valor', 'data_gasto', 'criado_por', 'ticket', 'created_at']
    list_filter = ['data_gasto', 'origem']
    search_fields = ['estabelecimento', 'descricao', 'cartao__last4']
    raw_id_fields = ['cartao', 'criado_por', 'ticket']
    readonly_fields = ['created_at', 'ia_dados']
