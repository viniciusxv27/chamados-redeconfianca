from django.contrib import admin
from .models import ExclusionRecord, Contestation


@admin.register(ExclusionRecord)
class ExclusionRecordAdmin(admin.ModelAdmin):
    list_display = ('vendedor', 'filial', 'pilar', 'receita', 'coordenacao', 'imported_at')
    list_filter = ('pilar', 'filial', 'imported_at')
    search_fields = ('vendedor', 'filial', 'nome_cliente', 'cpf_cnpj')
    readonly_fields = ('imported_at',)


@admin.register(Contestation)
class ContestationAdmin(admin.ModelAdmin):
    list_display = ('pk', 'exclusion', 'requester', 'status', 'payment_status', 'created_at', 'reviewed_by')
    list_filter = ('status', 'payment_status', 'created_at')
    search_fields = ('exclusion__vendedor', 'requester__first_name', 'requester__last_name', 'reason')
    raw_id_fields = ('exclusion', 'requester', 'reviewed_by')
    readonly_fields = ('created_at', 'updated_at', 'reviewed_at')
