from django.contrib import admin

from .models import Carteira, MetaProporcional, MovimentacaoHC, ObservacaoHC, VagaHC


@admin.register(Carteira)
class CarteiraAdmin(admin.ModelAdmin):
    list_display = ('nome', 'coordenador', 'coordenador_nome', 'ordem')
    filter_horizontal = ('lojas',)


@admin.register(MovimentacaoHC)
class MovimentacaoHCAdmin(admin.ModelAdmin):
    list_display = ('data', 'resumo', 'situacao', 'criado_por', 'aplicado_por')
    list_filter = ('situacao',)
    search_fields = ('resumo', 'observacao')


admin.site.register(ObservacaoHC)
admin.site.register(VagaHC)


@admin.register(MetaProporcional)
class MetaProporcionalAdmin(admin.ModelAdmin):
    list_display = ('colaborador', 'percentual', 'atualizado_por', 'atualizado_em')
    list_filter = ('percentual',)
    raw_id_fields = ('colaborador', 'atualizado_por')
