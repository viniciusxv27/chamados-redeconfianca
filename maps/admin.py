from django.contrib import admin

from .models import ConfiguracaoMapa, PosicaoRegistrada


@admin.register(PosicaoRegistrada)
class PosicaoRegistradaAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'momento', 'origem', 'latitude', 'longitude', 'precisao_metros')
    list_filter = ('origem', 'momento')
    search_fields = ('usuario__first_name', 'usuario__last_name', 'usuario__email')
    date_hierarchy = 'momento'


@admin.register(ConfiguracaoMapa)
class ConfiguracaoMapaAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'intervalo_minutos', 'atualizado_em')

    def has_add_permission(self, request):
        # Configuração única: mais de uma linha só criaria dúvida sobre qual vale.
        return not ConfiguracaoMapa.objects.exists()
