from django.contrib import admin

from .models import (
    AtribuicaoCurso, CobrancaWhatsapp, Comprovante, ConfiguracaoCursos, Curso, GrupoWhatsappLoja,
)


@admin.register(Curso)
class CursoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'tipo', 'prazo', 'publicado', 'criado_por')
    list_filter = ('tipo', 'publicado')
    search_fields = ('titulo',)


@admin.register(Comprovante)
class ComprovanteAdmin(admin.ModelAdmin):
    list_display = ('colaborador', 'curso', 'status', 'enviado_em')
    list_filter = ('status', 'curso')
    search_fields = ('colaborador__first_name', 'colaborador__last_name')


admin.site.register(ConfiguracaoCursos)
admin.site.register(AtribuicaoCurso)


@admin.register(GrupoWhatsappLoja)
class GrupoWhatsappLojaAdmin(admin.ModelAdmin):
    list_display = ('setor', 'grupo', 'ativo')
    list_editable = ('grupo', 'ativo')


@admin.register(CobrancaWhatsapp)
class CobrancaWhatsappAdmin(admin.ModelAdmin):
    list_display = ('setor', 'origem', 'pessoas', 'enviado', 'criado_em')
    list_filter = ('origem', 'enviado')
