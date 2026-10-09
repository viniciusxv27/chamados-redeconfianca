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
