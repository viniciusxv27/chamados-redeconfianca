from django.contrib import admin

from .models import ConfiguracaoContagem, ContagemCaixaDia, ImportacaoContagem


@admin.register(ContagemCaixaDia)
class ContagemCaixaDiaAdmin(admin.ModelAdmin):
    list_display = ('loja', 'data', 'valor_sap', 'valor_vivogo', 'div', 'sit',
                    'valor_real', 'saldo')
    list_filter = ('loja', 'data')
    date_hierarchy = 'data'
    search_fields = ('loja__name',)

    @admin.display(description='Divergência')
    def div(self, obj):
        return obj.divergencia

    @admin.display(description='Status')
    def sit(self, obj):
        return obj.get_status_display() if hasattr(obj, 'get_status_display') else obj.status


@admin.register(ConfiguracaoContagem)
class ConfiguracaoContagemAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not ConfiguracaoContagem.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ImportacaoContagem)
class ImportacaoContagemAdmin(admin.ModelAdmin):
    list_display = ('executada_em', 'executada_por', 'linhas_lidas', 'dias_criados',
                    'dias_atualizados', 'sucesso')
    readonly_fields = [f.name for f in ImportacaoContagem._meta.fields]

    def has_add_permission(self, request):
        return False


from .models import CategoriaSangria, Sangria  # noqa: E402


@admin.register(CategoriaSangria)
class CategoriaSangriaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativa', 'ordem')
    list_editable = ('ativa', 'ordem')


@admin.register(Sangria)
class SangriaAdmin(admin.ModelAdmin):
    list_display = ('data', 'loja', 'categoria', 'descricao', 'valor', 'conferida', 'registrada_por')
    list_filter = ('conferida', 'categoria', 'loja')
    date_hierarchy = 'data'
    search_fields = ('descricao', 'favorecido', 'loja__name')
    raw_id_fields = ('registrada_por', 'conferida_por')
    readonly_fields = ('criada_em', 'atualizada_em')


from .models import DocumentoPisCofins, FornecedorPisCofins  # noqa: E402


@admin.register(FornecedorPisCofins)
class FornecedorPisCofinsAdmin(admin.ModelAdmin):
    list_display = ('razao_social', 'cnpj', 'situacao', 'simples', 'municipio', 'uf', 'consultado_em')
    search_fields = ('razao_social', 'nome_fantasia', 'cnpj')
    list_filter = ('situacao', 'simples', 'uf')


@admin.register(DocumentoPisCofins)
class DocumentoPisCofinsAdmin(admin.ModelAdmin):
    list_display = ('competencia', 'tipo', 'numero', 'fornecedor', 'valor', 'loja', 'registrado_por', 'criado_em')
    list_filter = ('tipo', 'competencia')
    search_fields = ('numero', 'descricao', 'fornecedor__razao_social', 'fornecedor__cnpj')
    raw_id_fields = ('fornecedor', 'registrado_por')
    readonly_fields = ('criado_em', 'atualizado_em')


from .models import NotaRecebida, SincronizacaoDFe  # noqa: E402


@admin.register(NotaRecebida)
class NotaRecebidaAdmin(admin.ModelAdmin):
    list_display = ('emissao', 'numero', 'emitente_nome', 'emitente_cnpj', 'valor', 'situacao', 'documento', 'ignorada')
    list_filter = ('situacao', 'ignorada', 'cnpj_destinatario')
    search_fields = ('chave', 'emitente_nome', 'emitente_cnpj', 'numero')
    raw_id_fields = ('documento', 'ignorada_por')
    readonly_fields = ('recebida_em', 'atualizada_em')


@admin.register(SincronizacaoDFe)
class SincronizacaoDFeAdmin(admin.ModelAdmin):
    list_display = ('cnpj', 'ult_nsu', 'max_nsu', 'ultima_consulta', 'proxima_consulta', 'ultimo_cstat', 'ultima_mensagem')
