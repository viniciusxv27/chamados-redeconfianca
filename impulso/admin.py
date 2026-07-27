from django.contrib import admin

from .models import (
    ConclusaoConteudo, ConteudoConectar, Ideia, ImpulsoFeedback, Meta,
    MetaAnexo, MetaComentario, ProjetoFoco, TarefaProjeto,
)


class MetaAnexoInline(admin.TabularInline):
    model = MetaAnexo
    extra = 0


class MetaComentarioInline(admin.TabularInline):
    model = MetaComentario
    extra = 0


@admin.register(Meta)
class MetaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'colaborador', 'gestor', 'periodicidade', 'status',
                    'prazo', 'nota_qualidade', 'nota_prazo')
    list_filter = ('status', 'periodicidade', 'prazo')
    search_fields = ('titulo', 'descricao', 'colaborador__first_name',
                     'colaborador__last_name', 'colaborador__email')
    date_hierarchy = 'created_at'
    inlines = [MetaAnexoInline, MetaComentarioInline]
    raw_id_fields = ('gestor', 'colaborador', 'avaliado_por', 'created_by')


@admin.register(ImpulsoFeedback)
class ImpulsoFeedbackAdmin(admin.ModelAdmin):
    list_display = ('colaborador', 'gestor', 'referencia_mes', 'criado_em')
    list_filter = ('referencia_mes',)
    search_fields = ('colaborador__first_name', 'colaborador__last_name')
    raw_id_fields = ('gestor', 'colaborador')


class ConclusaoConteudoInline(admin.TabularInline):
    model = ConclusaoConteudo
    extra = 0
    raw_id_fields = ('user',)


@admin.register(ConteudoConectar)
class ConteudoConectarAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'tipo', 'obrigatorio', 'criado_por',
                    'criado_por_equipe', 'ativo', 'criado_em')
    list_filter = ('tipo', 'obrigatorio', 'ativo', 'criado_por_equipe')
    search_fields = ('titulo', 'descricao')
    filter_horizontal = ('obrigatorio_para',)
    inlines = [ConclusaoConteudoInline]
    raw_id_fields = ('criado_por',)


class TarefaProjetoInline(admin.TabularInline):
    model = TarefaProjeto
    extra = 0
    raw_id_fields = ('responsavel', 'criado_por')


@admin.register(ProjetoFoco)
class ProjetoFocoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo', 'criado_por', 'criado_em')
    list_filter = ('ativo',)
    search_fields = ('nome', 'descricao')
    filter_horizontal = ('membros',)
    inlines = [TarefaProjetoInline]
    raw_id_fields = ('criado_por',)


@admin.register(Ideia)
class IdeiaAdmin(admin.ModelAdmin):
    list_display = ('autor', 'setor_impacto', 'status', 'criado_em')
    list_filter = ('status',)
    search_fields = ('descricao', 'motivo', 'setor_impacto')
    raw_id_fields = ('autor',)
