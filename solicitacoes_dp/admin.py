from django.contrib import admin

from .models import Anexo, Evento, ResponsavelDP, Solicitacao


class AnexoInline(admin.TabularInline):
    model = Anexo
    extra = 0
    readonly_fields = ('nome_original', 'tamanho', 'enviado_por', 'enviado_em', 'removido')
    exclude = ('arquivo',)


class EventoInline(admin.TabularInline):
    model = Evento
    extra = 0
    readonly_fields = ('autor', 'acao', 'texto', 'alteracoes', 'em')


@admin.register(Solicitacao)
class SolicitacaoAdmin(admin.ModelAdmin):
    list_display = ('id', 'colaborador', 'tipo', 'data', 'status', 'enviada_em', 'responsavel')
    list_filter = ('tipo', 'status')
    search_fields = ('colaborador__first_name', 'colaborador__last_name', 'colaborador__email')
    raw_id_fields = ('colaborador', 'responsavel')
    inlines = [AnexoInline, EventoInline]


admin.site.register(ResponsavelDP)
