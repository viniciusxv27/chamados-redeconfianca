from django.contrib import admin

from .models import RelatoBug


@admin.register(RelatoBug)
class RelatoBugAdmin(admin.ModelAdmin):
    list_display = ('id', 'titulo', 'autor', 'status', 'criado_em', 'commit_sha')
    list_filter = ('status',)
    search_fields = ('titulo', 'url', 'descricao')
    raw_id_fields = ('autor', 'decidido_por')
