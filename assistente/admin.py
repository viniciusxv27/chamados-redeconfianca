from django.contrib import admin

from .models import AssistenteConexao


@admin.register(AssistenteConexao)
class AssistenteConexaoAdmin(admin.ModelAdmin):
    list_display = ('user', 'key_hint', 'modelo', 'conectado_em', 'ultimo_ok_em')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
    # A chave nunca aparece nem é editável pela tela — só o "final" para conferência.
    readonly_fields = ('api_key_cifrada', 'key_hint', 'conectado_em', 'ultimo_ok_em',
                       'criado_em', 'atualizado_em')

    def has_add_permission(self, request):
        return False
