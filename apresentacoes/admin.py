from django.contrib import admin

from .models import Apresentacao, ConfiguracaoApresentacoes, Midia, TarefaIA, TemplateApresentacao


@admin.register(Apresentacao)
class ApresentacaoAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'dono', 'origem', 'modulo', 'status', 'atualizado_em')
    list_filter = ('origem', 'status')
    search_fields = ('titulo', 'pedido', 'dono__first_name', 'dono__last_name')
    raw_id_fields = ('dono', 'template')
    exclude = ('documento',)


@admin.register(TemplateApresentacao)
class TemplateApresentacaoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'origem', 'status', 'padrao_da_rede', 'principal', 'ativo')
    exclude = ('documento',)


@admin.register(Midia)
class MidiaAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'tipo', 'origem', 'dono', 'apresentacao', 'template', 'criado_em')
    list_filter = ('tipo', 'origem')
    raw_id_fields = ('dono', 'apresentacao', 'template')


@admin.register(TarefaIA)
class TarefaIAAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'usuario', 'apresentacao', 'status', 'criado_em')
    list_filter = ('tipo', 'status')
    raw_id_fields = ('usuario', 'apresentacao', 'template')


@admin.register(ConfiguracaoApresentacoes)
class ConfiguracaoApresentacoesAdmin(admin.ModelAdmin):
    filter_horizontal = ('liberados',)
    exclude = ('canva_client_secret_cifrado',)
