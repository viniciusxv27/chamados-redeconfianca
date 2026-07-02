from django.contrib import admin
from .models import (
    ClimateSurveyParticipation,
    ClimateSurveyResponse,
    ExitInterviewAccessPermission,
    ExitInterviewParticipation,
    ExitInterviewResponse,
    Feedback,
    FeedbackAssignment,
    FeedbackReminderDismissal,
    SurveyManagerPermission,
    SurveySettings,
)


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


@admin.register(ClimateSurveyParticipation)
class ClimateSurveyParticipationAdmin(admin.ModelAdmin):
    list_display = ('user', 'sector', 'status', 'last_step', 'started_at', 'completed_at')
    list_filter = ('survey_key', 'status', 'sector')
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'sector__name', 'last_step')
    autocomplete_fields = ('user', 'sector')
    readonly_fields = ('started_at', 'updated_at', 'completed_at')


@admin.register(ClimateSurveyResponse)
class ClimateSurveyResponseAdmin(admin.ModelAdmin):
    list_display = ('user', 'sector', 'survey_key', 'duration_seconds', 'submitted_at')
    list_filter = ('survey_key', 'sector', 'submitted_at')
    search_fields = ('sector__name', 'user__first_name', 'user__last_name', 'user__email')
    autocomplete_fields = ('sector', 'user')
    readonly_fields = ('answers', 'submitted_at')


@admin.register(SurveyManagerPermission)
class SurveyManagerPermissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'granted_by', 'created_at')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
    autocomplete_fields = ('user', 'granted_by')
    readonly_fields = ('created_at',)


@admin.register(SurveySettings)
class SurveySettingsAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'climate_menu_visible', 'updated_at')
    readonly_fields = ('updated_at',)


@admin.register(ExitInterviewAccessPermission)
class ExitInterviewAccessPermissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'granted_by', 'created_at')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
    autocomplete_fields = ('user', 'granted_by')
    readonly_fields = ('created_at',)


@admin.register(ExitInterviewParticipation)
class ExitInterviewParticipationAdmin(admin.ModelAdmin):
    list_display = ('user', 'sector', 'status', 'last_step', 'started_at', 'completed_at')
    list_filter = ('survey_key', 'status', 'sector')
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'sector__name', 'last_step')
    autocomplete_fields = ('user', 'sector')
    readonly_fields = ('started_at', 'updated_at', 'completed_at')


@admin.register(ExitInterviewResponse)
class ExitInterviewResponseAdmin(admin.ModelAdmin):
    list_display = ('user', 'sector', 'survey_key', 'duration_seconds', 'submitted_at')
    list_filter = ('survey_key', 'sector', 'submitted_at')
    search_fields = ('user__first_name', 'user__last_name', 'user__email', 'sector__name')
    autocomplete_fields = ('user', 'sector')
    readonly_fields = ('answers', 'submitted_at')
