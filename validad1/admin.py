from django.contrib import admin

from .models import VendaD1, VendaD1Contestacao, VendaD1ChatMessage


@admin.register(VendaD1)
class VendaD1Admin(admin.ModelAdmin):
    list_display = (
        'numero_da_venda', 'vendedor', 'pdv', 'valor', 'status',
        'tipo_divergencia', 'acordo_status', 'data_da_venda', 'is_duplicate',
    )
    list_filter = ('status', 'acordo_status', 'tipo_divergencia', 'penalidade', 'is_duplicate', 'data_da_venda')
    search_fields = ('numero_da_venda', 'cpf', 'vendedor', 'pdv')
    readonly_fields = ('first_seen_at', 'last_synced_at')


@admin.register(VendaD1Contestacao)
class VendaD1ContestacaoAdmin(admin.ModelAdmin):
    list_display = ('id', 'venda', 'aberto_por', 'status', 'aberto_em')
    list_filter = ('status',)


@admin.register(VendaD1ChatMessage)
class VendaD1ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'contestacao', 'autor', 'criado_em')
