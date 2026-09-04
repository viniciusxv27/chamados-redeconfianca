from django.contrib import admin

from .models import ConfiguracaoCurriculos, Curriculo


@admin.register(Curriculo)
class CurriculoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'cidade', 'situacao', 'enviado_em')
    list_filter = ('situacao', 'cidade')
    search_fields = ('nome', 'cidade', 'cargos', 'busca')
    readonly_fields = ('busca', 'texto', 'enviado_em', 'atualizado_em')


@admin.register(ConfiguracaoCurriculos)
class ConfiguracaoCurriculosAdmin(admin.ModelAdmin):
    filter_horizontal = ('grupos',)
