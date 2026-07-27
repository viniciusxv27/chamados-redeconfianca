from django.contrib import admin

from .models import (
    LimpezaAnswer,
    LimpezaEvaluator,
    LimpezaQuestion,
    LimpezaTemplate,
    LimpezaTodo,
)


class LimpezaQuestionInline(admin.TabularInline):
    model = LimpezaQuestion
    extra = 1
    fields = ('pilar', 'item', 'text', 'detalhamento', 'gravidade', 'contestavel', 'order', 'points')


@admin.register(LimpezaTemplate)
class LimpezaTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    inlines = [LimpezaQuestionInline]


class LimpezaAnswerInline(admin.TabularInline):
    model = LimpezaAnswer
    extra = 0
    readonly_fields = ('question', 'observation', 'photo', 'status', 'answered_by', 'answered_at')


@admin.register(LimpezaTodo)
class LimpezaTodoAdmin(admin.ModelAdmin):
    list_display = ('sector', 'template', 'month', 'year', 'status', 'score_percentage', 'launched_by')
    list_filter = ('status', 'year', 'month', 'sector')
    search_fields = ('sector__name', 'template__name')
    inlines = [LimpezaAnswerInline]


@admin.register(LimpezaEvaluator)
class LimpezaEvaluatorAdmin(admin.ModelAdmin):
    list_display = ('user', 'is_active', 'created_at')
    list_filter = ('is_active',)
    filter_horizontal = ('sectors',)
