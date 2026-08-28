from django.contrib import admin

from .models import ConfiguracaoReunioes, ParticipanteReuniao, Reuniao


@admin.register(Reuniao)
class ReuniaoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'inicio', 'organizador', 'status')
    list_filter = ('status',)
    search_fields = ('titulo',)


admin.site.register(ParticipanteReuniao)
admin.site.register(ConfiguracaoReunioes)
