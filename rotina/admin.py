from django.contrib import admin

from .models import (
    AtividadeModelo, AtividadeRotina, AvisoRotina, AvisoWhatsApp, ModeloRotina, RotinaGerencial,
)

CAMPOS_ATIVIDADE = ('dia_semana', 'inicio', 'fim', 'titulo', 'categoria', 'bloqueada', 'minutos_whatsapp', 'descricao')


class AtividadeModeloInline(admin.TabularInline):
    model = AtividadeModelo
    extra = 0
    fields = CAMPOS_ATIVIDADE


@admin.register(ModeloRotina)
class ModeloRotinaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ativo', 'com_domingo', 'atualizado_em')
    list_filter = ('ativo', 'com_domingo')
    search_fields = ('nome',)
    readonly_fields = ('criado_por', 'criado_em', 'atualizado_em')
    inlines = [AtividadeModeloInline]


class AtividadeRotinaInline(admin.TabularInline):
    model = AtividadeRotina
    extra = 0
    fields = CAMPOS_ATIVIDADE + ('criada_pela_pessoa',)


@admin.register(RotinaGerencial)
class RotinaGerencialAdmin(admin.ModelAdmin):
    list_display = ('user', 'ativa', 'pode_criar', 'com_domingo', 'avisar_whatsapp', 'modelo_origem',
                    'atualizado_em')
    list_filter = ('ativa', 'pode_criar', 'com_domingo', 'avisar_whatsapp', 'modelo_origem')
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
    list_display = ('titulo', 'user', 'data', 'tipo', 'inicio', 'criado_em')
    list_filter = ('tipo', 'data')
    search_fields = ('titulo', 'user__first_name', 'user__last_name', 'user__email')
    raw_id_fields = ('user', 'atividade')
    date_hierarchy = 'data'


@admin.register(AvisoWhatsApp)
class AvisoWhatsAppAdmin(admin.ModelAdmin):
    """Só consulta: o registro é a prova de que o aviso saiu (ou por que não saiu)."""

    list_display = ('titulo', 'user', 'data', 'tipo', 'inicio', 'enviado', 'enviado_em', 'detalhe')
    list_filter = ('enviado', 'tipo', 'data')
    search_fields = ('titulo', 'user__first_name', 'user__last_name', 'user__email')
    raw_id_fields = ('user', 'atividade')
    date_hierarchy = 'data'
    readonly_fields = ('user', 'atividade', 'data', 'tipo', 'titulo', 'inicio', 'enviado', 'detalhe',
                       'criado_em', 'enviado_em')

    def has_add_permission(self, request):
        return False
