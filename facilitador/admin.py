from django.contrib import admin

from .models import Recurso


@admin.register(Recurso)
class RecursoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'tipo', 'categoria', 'ativo', 'acessos', 'atualizado_em')
    list_filter = ('tipo', 'ativo', 'categoria')
    search_fields = ('titulo', 'descricao', 'categoria', 'url', 'nome_original')
