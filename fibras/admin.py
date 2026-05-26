from django.contrib import admin

from .models import (
    Fibra, FibraStatusHistory, FibraIncidente, FibraChat, FibraChatMessage,
    PlanilhaOrdemInconsistente,
)


@admin.register(Fibra)
class FibraAdmin(admin.ModelAdmin):
    list_display = ('numero_da_venda', 'cliente', 'vendedor', 'pdv', 'valor', 'status', 'data_da_venda')
    list_filter = ('status', 'pilar', 'servico_tecnico', 'data_da_venda')
    search_fields = ('numero_da_venda', 'cpf', 'cliente', 'vendedor', 'pdv')
    readonly_fields = ('first_seen_at', 'last_synced_at')


@admin.register(FibraStatusHistory)
class FibraStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ('fibra', 'status_anterior', 'status_novo', 'alterado_por', 'alterado_em')
    list_filter = ('status_novo',)
    search_fields = ('fibra__numero_da_venda',)


@admin.register(FibraIncidente)
class FibraIncidenteAdmin(admin.ModelAdmin):
    list_display = ('id', 'fibra', 'aberto_por', 'status', 'aberto_em')
    list_filter = ('status',)
    search_fields = ('fibra__numero_da_venda',)


@admin.register(FibraChat)
class FibraChatAdmin(admin.ModelAdmin):
    list_display = ('id', 'fibra', 'aberto_em', 'encerrado_em')


@admin.register(FibraChatMessage)
class FibraChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'chat', 'autor', 'criado_em')
    search_fields = ('chat__fibra__numero_da_venda', 'texto')


@admin.register(PlanilhaOrdemInconsistente)
class PlanilhaOrdemInconsistenteAdmin(admin.ModelAdmin):
    list_display = ('ordem', 'status_raw', 'occurrences', 'first_seen_at', 'last_seen_at')
    search_fields = ('ordem',)
    readonly_fields = ('first_seen_at', 'last_seen_at')
