from django.contrib import admin

from .models import ConfiguracaoRenova, FotoRenova, PrecoAparelho, Renova


@admin.register(PrecoAparelho)
class PrecoAparelhoAdmin(admin.ModelAdmin):
    list_display = ('modelo', 'armazenamento', 'valor_excelente', 'marca', 'ativo', 'ordem')
    list_filter = ('marca', 'ativo')
    list_editable = ('valor_excelente', 'ativo', 'ordem')
    search_fields = ('modelo',)


class FotoRenovaInline(admin.TabularInline):
    model = FotoRenova
    extra = 0
    fields = ('tipo', 'ordem', 'arquivo', 'enviada_em')
    readonly_fields = ('enviada_em',)


@admin.register(Renova)
class RenovaAdmin(admin.ModelAdmin):
    inlines = [FotoRenovaInline]
    list_display = ('__str__', 'loja', 'parecer', 'recebimento', 'criado_por', 'criado_em')
    list_filter = ('recebimento', 'parecer', 'marca')
    search_fields = ('imei1', 'imei2', 'modelo', 'numero_serie', 'vendedor_nome', 'numero_venda')
    raw_id_fields = ('chamado', 'criado_por', 'recebido_por', 'preco_tabela', 'loja')
    readonly_fields = ('criado_em', 'atualizado_em')


@admin.register(ConfiguracaoRenova)
class ConfiguracaoRenovaAdmin(admin.ModelAdmin):
    filter_horizontal = ('habilitados',)
