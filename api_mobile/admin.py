from django.contrib import admin

from .models import TokenMobile


@admin.register(TokenMobile)
class TokenMobileAdmin(admin.ModelAdmin):
    list_display = ('user', 'dispositivo', 'plataforma', 'versao_app', 'prefixo', 'criado_em', 'ultimo_uso', 'revogado_em')
    list_filter = ('plataforma', 'revogado_em')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'dispositivo', 'prefixo')
    readonly_fields = ('user', 'chave_hash', 'prefixo', 'dispositivo', 'plataforma', 'versao_app',
                       'criado_em', 'ultimo_uso')
    actions = ['revogar']

    @admin.action(description='Encerrar as sessões selecionadas')
    def revogar(self, request, queryset):
        for token in queryset:
            token.revogar()

    def has_add_permission(self, request):
        return False
