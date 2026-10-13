from django.contrib import admin

from .models import ContaEmail


@admin.register(ContaEmail)
class ContaEmailAdmin(admin.ModelAdmin):
    list_display = ('email', 'usuario', 'ultimo_acesso', 'senha_recusada')
    search_fields = ('email', 'usuario__first_name', 'usuario__last_name', 'usuario__username')
    # A senha cifrada não aparece nem para o admin: não há o que fazer com ela.
    exclude = ('senha_cifrada',)
    readonly_fields = ('credencial_em', 'ultimo_acesso', 'ultimo_erro', 'criada_em', 'atualizada_em')
