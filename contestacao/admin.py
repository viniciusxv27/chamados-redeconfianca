from django.contrib import admin
from .models import ExclusionRecord, Contestation, ContestationHistory


@admin.register(ExclusionRecord)
class ExclusionRecordAdmin(admin.ModelAdmin):
    list_display = ('vendedor', 'filial', 'pilar', 'receita', 'coordenacao', 'numero_acesso', 'cpf_cnpj', 'imported_at')
    list_filter = ('pilar', 'filial', 'imported_at')
    search_fields = ('vendedor', 'filial', 'nome_cliente', 'cpf_cnpj', 'numero_acesso')
    readonly_fields = ('imported_at',)


@admin.register(Contestation)
class ContestationAdmin(admin.ModelAdmin):
    list_display = ('pk', 'exclusion', 'requester', 'status', 'payment_status', 'created_at', 'reviewed_by')
    list_filter = ('status', 'payment_status', 'created_at')
    search_fields = ('exclusion__vendedor', 'requester__first_name', 'requester__last_name', 'reason')
    raw_id_fields = ('exclusion', 'requester', 'reviewed_by')
    readonly_fields = ('created_at', 'updated_at', 'reviewed_at')


@admin.register(ContestationHistory)
class ContestationHistoryAdmin(admin.ModelAdmin):
    list_display = ('pk', 'action', 'user', 'contestation', 'created_at')
    list_filter = ('action', 'created_at')
    search_fields = ('user__first_name', 'user__last_name', 'notes')
    raw_id_fields = ('contestation', 'user')
    readonly_fields = ('created_at',)
