from django.contrib import admin

from .models import AcessoGestaoMetas, Competencia, HistoricoMeta, ImportacaoMetas, LojaMeta, MetaLoja, Realizado


@admin.register(AcessoGestaoMetas)
class AcessoGestaoMetasAdmin(admin.ModelAdmin):
    list_display = ('user', 'grupo', 'papel', 'atualizado_em')


@admin.register(LojaMeta)
class LojaMetaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'pdv_vivo', 'regiao', 'setor', 'ordem', 'ativa')
    list_filter = ('regiao', 'ativa')


@admin.register(Competencia)
class CompetenciaAdmin(admin.ModelAdmin):
    list_display = ('mes', 'status', 'criada_por', 'fechada_em')


@admin.register(Realizado)
class RealizadoAdmin(admin.ModelAdmin):
    list_display = ('loja', 'mes', 'pilar', 'valor', 'qtd_tv', 'qtd_fixa', 'qtd_total', 'fonte')
    list_filter = ('pilar', 'fonte', 'mes')


@admin.register(MetaLoja)
class MetaLojaAdmin(admin.ModelAdmin):
    list_display = ('competencia', 'loja', 'pilar', 'meta', 'meta_vivo', 'nova_meta')
    list_filter = ('competencia', 'pilar')


@admin.register(HistoricoMeta)
class HistoricoMetaAdmin(admin.ModelAdmin):
    list_display = ('quando', 'competencia', 'loja_nome', 'pilar', 'campo', 'antes', 'depois', 'por_nome')


@admin.register(ImportacaoMetas)
class ImportacaoMetasAdmin(admin.ModelAdmin):
    list_display = ('criado_em', 'tipo', 'status', 'arquivo_nome', 'competencia')
    exclude = ('dados',)
