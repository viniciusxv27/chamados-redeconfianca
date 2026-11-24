from django.contrib import admin

from .models import Advertencia, ConfigCarreira, LojaApta, PedidoPromocao, PerfilCarreira

admin.site.register(ConfigCarreira)
admin.site.register(Advertencia)
admin.site.register(LojaApta)


@admin.register(PerfilCarreira)
class PerfilCarreiraAdmin(admin.ModelAdmin):
    list_display = ('user', 'nivel', 'ultima_promocao', 'resultado_media', 'satisfacao_nota', 'retrato_em')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')


@admin.register(PedidoPromocao)
class PedidoPromocaoAdmin(admin.ModelAdmin):
    list_display = ('user', 'situacao', 'criado_em', 'decidido_por', 'decidido_em')
    list_filter = ('situacao',)
