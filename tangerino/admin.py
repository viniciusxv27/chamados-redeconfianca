from django.contrib import admin

from .models import RegistroPontoPortal, SincronizacaoTangerino


@admin.register(SincronizacaoTangerino)
class SincronizacaoTangerinoAdmin(admin.ModelAdmin):
    list_display = ('executada_em', 'executada_por', 'casados_cpf', 'casados_nome',
                    'ja_vinculados', 'sem_correspondencia', 'sucesso')
    list_filter = ('sucesso', 'tipo')
    readonly_fields = [f.name for f in SincronizacaoTangerino._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(RegistroPontoPortal)
class RegistroPontoPortalAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'momento', 'atrasado', 'sucesso', 'ip', 'criado_em')
    list_filter = ('sucesso', 'atrasado')
    search_fields = ('usuario__first_name', 'usuario__last_name', 'usuario__email')
    readonly_fields = [f.name for f in RegistroPontoPortal._meta.fields]

    def has_add_permission(self, request):
        return False
