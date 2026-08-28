from django.contrib import admin

from .models import (ConfiguracaoTangerino, Escala, EscalaConfig, EscalaDia,
                     FeriasLancamento, JornadaTrabalho, MarcacaoPonto,
                     RegistroPontoPortal, SaldoHoras, SincronizacaoTangerino)


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
    list_display = ('nome', 'data', 'hora_entrada1', 'hora_saida1', 'hora_entrada2',
                    'hora_saida2', 'previsto', 'total_hhmm', 'em_aberto')
    list_filter = ('data', 'em_aberto', 'plataforma', 'editado')
    search_fields = ('nome', 'usuario__first_name', 'usuario__last_name')
    date_hierarchy = 'data'
    readonly_fields = [f.name for f in MarcacaoPonto._meta.fields]

    def has_add_permission(self, request):
        return False

    # Colunas só com a hora: a data já está na própria linha.
    @admin.display(description='Entrada 1', ordering='entrada1')
    def hora_entrada1(self, obj):
        return obj.entrada1.strftime('%H:%M') if obj.entrada1 else '—'

    @admin.display(description='Saída 1', ordering='saida1')
    def hora_saida1(self, obj):
        return obj.saida1.strftime('%H:%M') if obj.saida1 else '—'

    @admin.display(description='Entrada 2', ordering='entrada2')
    def hora_entrada2(self, obj):
        return obj.entrada2.strftime('%H:%M') if obj.entrada2 else '—'

    @admin.display(description='Saída 2', ordering='saida2')
    def hora_saida2(self, obj):
        return obj.saida2.strftime('%H:%M') if obj.saida2 else '—'

    @admin.display(description='Previsto', ordering='previsto_segundos')
    def previsto(self, obj):
        if not obj.previsto_segundos:
            return 'folga'
        return f'{obj.previsto_segundos // 3600:02d}:{obj.previsto_segundos % 3600 // 60:02d}'

    @admin.display(description='Trabalhado')
    def total_hhmm(self, obj):
        return obj.total_hhmm


@admin.register(SaldoHoras)
class SaldoHorasAdmin(admin.ModelAdmin):
    list_display = ('nome', 'previsto', 'trabalhado', 'diferenca', 'aproveita',
                    'saldo', 'janela', 'usuario')
    list_filter = ('periodo_inicio', 'periodo_fim', 'analise_inicio')
    search_fields = ('nome', 'email', 'usuario__first_name', 'usuario__last_name')
    ordering = ('saldo_minutos',)          # os mais devedores primeiro
    readonly_fields = [f.name for f in SaldoHoras._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.display(description='Saldo', ordering='saldo_minutos')
    def saldo(self, obj):
        return obj.saldo_hhmm

    @admin.display(description='Período')
    def periodo(self, obj):
        return f'{obj.periodo_inicio:%d/%m/%Y} a {obj.periodo_fim:%d/%m/%Y}'

    @admin.display(description='Tinha que trabalhar', ordering='previsto_minutos')
    def previsto(self, obj):
        return obj.previsto_hhmm if obj.tem_analise else '—'

    @admin.display(description='Trabalhou', ordering='trabalhado_minutos')
    def trabalhado(self, obj):
        return obj.trabalhado_hhmm if obj.tem_analise else '—'

    @admin.display(description='Diferença')
    def diferenca(self, obj):
        return obj.diferenca_hhmm if obj.tem_analise else '—'

    @admin.display(description='%')
    def aproveita(self, obj):
        pct = obj.aproveitamento
        return f'{pct}%' if pct is not None else '—'

    @admin.display(description='Janela do previsto')
    def janela(self, obj):
        if not obj.tem_analise:
            return 'sem marcações'
        return f'{obj.analise_inicio:%d/%m} a {obj.analise_fim:%d/%m/%Y}'


@admin.register(FeriasLancamento)
class FeriasLancamentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'inicio', 'fim', 'dias', 'status', 'sincronizado_em')
    list_filter = ('status', 'origem')
    search_fields = ('nome', 'usuario__first_name', 'usuario__last_name')
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


@admin.register(JornadaTrabalho)
class JornadaTrabalhoAdmin(admin.ModelAdmin):
    """As escalas contratadas, espelhadas do Tangerino.

    É a base do "quantas horas deveria ter trabalhado". Só leitura: quem manda
    na escala é o Tangerino.
    """

    list_display = ('nome', 'horas_semana', 'semana', 'tangerino_id', 'sincronizado_em')
    search_fields = ('nome',)
    ordering = ('-segundos_semana',)
    readonly_fields = [f.name for f in JornadaTrabalho._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.display(description='Semana')
    def semana(self, obj):
        return obj.resumo_semana


class EscalaDiaInline(admin.TabularInline):
    model = EscalaDia
    extra = 0


@admin.register(Escala)
class EscalaAdmin(admin.ModelAdmin):
    """Escala semanal montada no portal (não vem do Tangerino)."""

    list_display = ('colaborador', 'semana_inicio', 'criado_por', 'atualizado_em')
    list_filter = ('semana_inicio',)
    search_fields = ('colaborador__first_name', 'colaborador__last_name', 'colaborador__email')
    date_hierarchy = 'semana_inicio'
    raw_id_fields = ('colaborador', 'criado_por', 'atualizado_por')
    inlines = [EscalaDiaInline]


@admin.register(EscalaConfig)
class EscalaConfigAdmin(admin.ModelAdmin):
    filter_horizontal = ('gestores',)

    def has_add_permission(self, request):
        return not EscalaConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
