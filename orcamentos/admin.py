from django.contrib import admin

from .models import BaseMailing, ContatoMailing, GestorOrcamentos, LiberacaoOrcamentos


@admin.register(BaseMailing)
class BaseMailingAdmin(admin.ModelAdmin):
    list_display = ('nome', 'loja', 'status', 'importados', 'duplicados', 'invalidos', 'criado_em')
    list_filter = ('status', 'loja')
    exclude = ('linhas',)


@admin.register(ContatoMailing)
class ContatoMailingAdmin(admin.ModelAdmin):
    list_display = ('nome', 'numero', 'loja', 'status', 'trabalhado_por', 'trabalhado_em')
    list_filter = ('status', 'loja')
    search_fields = ('nome', 'numero')


admin.site.register(GestorOrcamentos)
admin.site.register(LiberacaoOrcamentos)
