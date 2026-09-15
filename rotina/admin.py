from django.contrib import admin

from .models import AtividadeModelo, AtividadeRotina, AvisoRotina, ModeloRotina, RotinaGerencial

CAMPOS_ATIVIDADE = ('dia_semana', 'inicio', 'fim', 'titulo', 'categoria', 'bloqueada', 'descricao')


class AtividadeModeloInline(admin.TabularInline):
    model = AtividadeModelo
    extra = 0
    fields = CAMPOS_ATIVIDADE


@admin.register(ModeloRotina)
class ModeloRotinaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo', 'atualizado_em')
    list_filter = ('ativo',)
    search_fields = ('nome',)
    readonly_fields = ('criado_por', 'criado_em', 'atualizado_em')
    inlines = [AtividadeModeloInline]


class AtividadeRotinaInline(admin.TabularInline):
    model = AtividadeRotina
    extra = 0
    fields = CAMPOS_ATIVIDADE + ('criada_pela_pessoa',)


@admin.register(RotinaGerencial)
class RotinaGerencialAdmin(admin.ModelAdmin):
    list_display = ('user', 'ativa', 'pode_criar', 'modelo_origem', 'atualizado_em')
    list_filter = ('ativa', 'pode_criar', 'modelo_origem')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
    raw_id_fields = ('user',)
    readonly_fields = ('criado_por', 'atualizado_por', 'criado_em', 'atualizado_em')
    inlines = [AtividadeRotinaInline]


@admin.register(AtividadeModelo)
class AtividadeModeloAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'modelo', 'dia_semana', 'inicio', 'fim', 'categoria', 'bloqueada')
    list_filter = ('modelo', 'dia_semana', 'categoria', 'bloqueada')
    search_fields = ('titulo',)


@admin.register(AtividadeRotina)
class AtividadeRotinaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'rotina', 'dia_semana', 'inicio', 'fim', 'categoria', 'bloqueada',
                    'criada_pela_pessoa')
    list_filter = ('dia_semana', 'categoria', 'bloqueada', 'criada_pela_pessoa')
    search_fields = ('titulo', 'rotina__user__first_name', 'rotina__user__last_name')
    raw_id_fields = ('rotina',)


@admin.register(AvisoRotina)
class AvisoRotinaAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'user', 'data', 'inicio', 'criado_em')
    list_filter = ('data',)
    search_fields = ('titulo', 'user__first_name', 'user__last_name', 'user__email')
    raw_id_fields = ('user', 'atividade')
    date_hierarchy = 'data'
