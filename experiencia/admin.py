from django.contrib import admin

from .models import (
    ExperienciaAnswer,
    ExperienciaEvaluator,
    ExperienciaQuestion,
    ExperienciaTemplate,
    ExperienciaTodo,
)


class ExperienciaQuestionInline(admin.TabularInline):
    model = ExperienciaQuestion
    extra = 1
    fields = ('pilar', 'item', 'text', 'detalhamento', 'gravidade', 'contestavel', 'order', 'points')


@admin.register(ExperienciaTemplate)
class ExperienciaTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name',)
    inlines = [ExperienciaQuestionInline]


class ExperienciaAnswerInline(admin.TabularInline):
    model = ExperienciaAnswer
    extra = 0
    readonly_fields = ('question', 'observation', 'photo', 'status', 'answered_by', 'answered_at')


@admin.register(ExperienciaTodo)
class ExperienciaTodoAdmin(admin.ModelAdmin):
    list_display = ('sector', 'template', 'month', 'year', 'status', 'score_percentage', 'launched_by')
    list_filter = ('status', 'year', 'month', 'sector')
    search_fields = ('sector__name', 'template__name')
    inlines = [ExperienciaAnswerInline]


@admin.register(ExperienciaEvaluator)
class ExperienciaEvaluatorAdmin(admin.ModelAdmin):
    list_display = ('user', 'is_active', 'created_at')
    list_filter = ('is_active',)
    filter_horizontal = ('sectors',)
