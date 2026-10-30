from django.contrib import admin

from .models import AcessoRotinaAdm, ItemChecklist, MarcacaoChecklist


@admin.register(AcessoRotinaAdm)
class AcessoRotinaAdmAdmin(admin.ModelAdmin):
    list_display = ('user', 'grupo', 'papel', 'atualizado_em')
    list_filter = ('papel',)


@admin.register(ItemChecklist)
class ItemChecklistAdmin(admin.ModelAdmin):
    list_display = ('loja', 'titulo', 'horario', 'ordem', 'ativo')
    list_filter = ('ativo', 'loja')


@admin.register(MarcacaoChecklist)
class MarcacaoChecklistAdmin(admin.ModelAdmin):
    list_display = ('item', 'data', 'feito_por', 'feito_em')
    list_filter = ('data',)
