from django.contrib import admin

from .models import Comentario, Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('id', 'autor', 'tipo', 'criado_em', 'total_curtidas', 'total_comentarios', 'ativo')
    list_filter = ('tipo', 'ativo')
    raw_id_fields = ('autor', 'apagado_por')


@admin.register(Comentario)
class ComentarioAdmin(admin.ModelAdmin):
    list_display = ('id', 'post', 'autor', 'criado_em', 'ativo')
    raw_id_fields = ('post', 'autor')
