from django.contrib import admin

from .models import (ConfiguracaoTangerino, FeriasLancamento, MarcacaoPonto,
                     RegistroPontoPortal, SincronizacaoTangerino)


@admin.register(ConfiguracaoTangerino)
class ConfiguracaoTangerinoAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'ativo', 'restrito_ao_grupo', 'grupo',
                    'permitir_bater_ponto', 'atualizado_em')

    def has_add_permission(self, request):
        return not ConfiguracaoTangerino.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MarcacaoPonto)
class MarcacaoPontoAdmin(admin.ModelAdmin):
    list_display = ('nome_funcionario', 'dia', 'entrada', 'saida', 'status',
                    'plataforma', 'sincronizado_em')
    list_filter = ('dia', 'status', 'plataforma', 'editado')
    search_fields = ('nome_funcionario', 'usuario__first_name', 'usuario__last_name')
    date_hierarchy = 'dia'
    readonly_fields = [f.name for f in MarcacaoPonto._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(FeriasLancamento)
class FeriasLancamentoAdmin(admin.ModelAdmin):
    list_display = ('nome_funcionario', 'inicio', 'fim', 'dias', 'status', 'sincronizado_em')
    list_filter = ('status', 'origem')
    search_fields = ('nome_funcionario', 'usuario__first_name', 'usuario__last_name')
    readonly_fields = [f.name for f in FeriasLancamento._meta.fields]

    def has_add_permission(self, request):
        return False


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
