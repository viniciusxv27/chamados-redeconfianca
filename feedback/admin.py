from django.contrib import admin
from .models import Feedback, FeedbackAssignment, FeedbackReminderDismissal


@admin.register(FeedbackAssignment)
class FeedbackAssignmentAdmin(admin.ModelAdmin):
    list_display = ('evaluator', 'evaluatee', 'monthly', 'status', 'created_at')
    list_filter = ('status', 'monthly')
    search_fields = (
        'evaluator__first_name', 'evaluator__last_name', 'evaluator__email',
        'evaluatee__first_name', 'evaluatee__last_name', 'evaluatee__email',
    )
    autocomplete_fields = ('evaluator', 'evaluatee', 'created_by')


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ('evaluatee', 'evaluator', 'data', 'average_score', 'created_at')
    list_filter = ('data',)
    search_fields = (
        'evaluator__first_name', 'evaluator__last_name',
        'evaluatee__first_name', 'evaluatee__last_name',
        'setor_area', 'nome_colaborador',
    )
    autocomplete_fields = ('evaluator', 'evaluatee', 'assignment')
    readonly_fields = ('ai_summary', 'ai_summary_generated_at', 'ai_summary_error', 'created_at', 'updated_at')


@admin.register(FeedbackReminderDismissal)
class FeedbackReminderDismissalAdmin(admin.ModelAdmin):
    list_display = ('user', 'key', 'dismissed_at')
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'key')
