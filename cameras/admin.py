from django.contrib import admin

from .models import AcessoCamera, AlertaCamera, Gravador


@admin.register(Gravador)
class GravadorAdmin(admin.ModelAdmin):
    # Senha e token não aparecem nem aqui.
    list_display = ('loja', 'coordenador', 'tem_equipamento', 'ultimo_contato')


@admin.register(AlertaCamera)
class AlertaCameraAdmin(admin.ModelAdmin):
    list_display = ('gravador', 'tipo', 'confianca', 'status', 'criado_em')
    list_filter = ('status', 'tipo')


@admin.register(AcessoCamera)
class AcessoCameraAdmin(admin.ModelAdmin):
    list_display = ('user', 'acao', 'gravador', 'em')
