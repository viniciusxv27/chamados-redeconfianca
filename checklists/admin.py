from django.contrib import admin
from .models import (
    ChecklistTemplate, 
    ChecklistTask, 
    ChecklistTaskInstructionMedia,
    ChecklistAssignment, 
    ChecklistExecution, 
    ChecklistTaskExecution,
    ChecklistTaskEvidence,
    ChecklistAssignmentApprover,
    ChecklistPendingAssignment
)


class ChecklistTaskInline(admin.TabularInline):
    model = ChecklistTask
    extra = 1
    fields = ['title', 'description', 'order', 'is_required']


class ChecklistTaskInstructionMediaInline(admin.TabularInline):
    model = ChecklistTaskInstructionMedia
    extra = 1
    fields = ['media_type', 'file', 'title', 'order']


class ChecklistTaskEvidenceInline(admin.TabularInline):
    model = ChecklistTaskEvidence
    extra = 0
    fields = ['evidence_type', 'file', 'uploaded_at']
    readonly_fields = ['uploaded_at']
    can_delete = True


@admin.register(ChecklistTemplate)
class ChecklistTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'sector', 'created_by', 'is_active', 'created_at']
    list_filter = ['is_active', 'sector', 'created_at']
    search_fields = ['name', 'description']
    inlines = [ChecklistTaskInline]
    
    def save_model(self, request, obj, form, change):
        if not change:  # Se está criando
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ChecklistTask)
class ChecklistTaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'template', 'order', 'is_required']
    list_filter = ['template', 'is_required']
    search_fields = ['title', 'description']
    inlines = [ChecklistTaskInstructionMediaInline]


@admin.register(ChecklistTaskInstructionMedia)
class ChecklistTaskInstructionMediaAdmin(admin.ModelAdmin):
    list_display = ['task', 'media_type', 'title', 'order', 'created_at']
    list_filter = ['media_type', 'created_at']
    search_fields = ['task__title', 'title']


@admin.register(ChecklistAssignment)
class ChecklistAssignmentAdmin(admin.ModelAdmin):
    list_display = ['template', 'assigned_to', 'period', 'schedule_type', 'start_date', 'end_date', 'is_active']
    list_filter = ['is_active', 'schedule_type', 'period', 'created_at']
    search_fields = ['template__name', 'assigned_to__first_name', 'assigned_to__last_name']
    date_hierarchy = 'start_date'


@admin.register(ChecklistExecution)
class ChecklistExecutionAdmin(admin.ModelAdmin):
    list_display = ['assignment', 'execution_date', 'period', 'status', 'progress_percentage']
    list_filter = ['status', 'period', 'execution_date']
    search_fields = ['assignment__template__name', 'assignment__assigned_to__first_name']
    date_hierarchy = 'execution_date'


@admin.register(ChecklistTaskExecution)
class ChecklistTaskExecutionAdmin(admin.ModelAdmin):
    list_display = ['execution', 'task', 'is_completed', 'completed_at']
    list_filter = ['is_completed', 'completed_at']
    search_fields = ['execution__assignment__template__name', 'task__title']
    inlines = [ChecklistTaskEvidenceInline]


@admin.register(ChecklistTaskEvidence)
class ChecklistTaskEvidenceAdmin(admin.ModelAdmin):
    list_display = ['task_execution', 'evidence_type', 'uploaded_at', 'order']
    list_filter = ['evidence_type', 'uploaded_at']
    search_fields = ['task_execution__task__title']
    readonly_fields = ['uploaded_at']
    date_hierarchy = 'uploaded_at'


@admin.register(ChecklistAssignmentApprover)
class ChecklistAssignmentApproverAdmin(admin.ModelAdmin):
    list_display = ['user', 'sector', 'is_active', 'added_by', 'created_at']
    list_filter = ['is_active', 'sector', 'created_at']
    search_fields = ['user__first_name', 'user__last_name', 'user__email']
    autocomplete_fields = ['user', 'sector']


@admin.register(ChecklistPendingAssignment)
class ChecklistPendingAssignmentAdmin(admin.ModelAdmin):
    list_display = ['template', 'assigned_to', 'assigned_by', 'status', 'created_at', 'approved_at']
    list_filter = ['status', 'created_at', 'approved_at']
    search_fields = ['template__name', 'assigned_to__first_name', 'assigned_to__last_name']
    date_hierarchy = 'created_at'
