from django.contrib import admin
from django.utils import timezone
from .models import (
    SystemLog, Tutorial, Report, ReportComment, Notification,
    AdminChecklistTemplate, DailyAdminChecklist, AdminChecklistTask, AdminChecklistAssignment,
    AdminChecklistSectorTask
)


@admin.register(SystemLog)
class SystemLogAdmin(admin.ModelAdmin):
    list_display = ('user', 'action_type', 'description', 'ip_address', 'created_at')
    list_filter = ('action_type', 'created_at')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'description')
    readonly_fields = ('user', 'action_type', 'description', 'ip_address', 'user_agent', 'created_at')
    ordering = ('-created_at',)
    
    def has_add_permission(self, request):
        return False


@admin.register(Tutorial)
class TutorialAdmin(admin.ModelAdmin):
    list_display = ('title', 'created_by', 'is_active', 'order', 'created_at')
    list_filter = ('is_active', 'created_at', 'created_by')
    search_fields = ('title', 'description')
    ordering = ('order', 'title')


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ('title', 'report_type', 'reporter', 'reported_user', 'status', 'priority', 'created_at')
    list_filter = ('report_type', 'status', 'priority', 'created_at', 'is_anonymous')
    search_fields = ('title', 'description', 'reporter__email', 'reported_user__email')
    readonly_fields = ('created_at', 'updated_at', 'resolved_at', 'ip_address')
    ordering = ('-created_at',)


@admin.register(ReportComment)
class ReportCommentAdmin(admin.ModelAdmin):
    list_display = ('report', 'user', 'created_at', 'is_internal')
    list_filter = ('is_internal', 'created_at')
    search_fields = ('report__title', 'user__email', 'comment')
    readonly_fields = ('created_at',)
    ordering = ('-created_at',)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'notification_type', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('title', 'message', 'user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('created_at', 'read_at')
    ordering = ('-created_at',)
    
    def has_add_permission(self, request):
        return False


@admin.register(AdminChecklistTemplate)
class AdminChecklistTemplateAdmin(admin.ModelAdmin):
    list_display = ('title', 'sector', 'priority', 'estimated_time_minutes', 'is_active', 'created_at')
    list_filter = ('sector', 'priority', 'is_active', 'created_at')
    search_fields = ('title', 'description', 'instructions')
    ordering = ('sector', 'title')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('title', 'description', 'sector')
        }),
        ('Configurações', {
            'fields': ('priority', 'estimated_time_minutes', 'is_active')
        }),
        ('Instruções', {
            'fields': ('instructions',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(DailyAdminChecklist)
class DailyAdminChecklistAdmin(admin.ModelAdmin):
    list_display = ('date', 'get_total_tasks', 'get_completed_tasks', 'get_completion_percentage', 'created_at')
    list_filter = ('date', 'created_at')
    search_fields = ('date',)
    readonly_fields = ('created_at', 'get_total_tasks', 'get_completed_tasks', 'get_completion_percentage')
    ordering = ('-date',)
    
    def get_total_tasks(self, obj):
        return obj.tasks.count()
    get_total_tasks.short_description = 'Total de Tarefas'
    
    def get_completed_tasks(self, obj):
        return obj.tasks.filter(status__in=['APPROVED', 'COMPLETED']).count()
    get_completed_tasks.short_description = 'Tarefas Concluídas'
    
    def get_completion_percentage(self, obj):
        total = obj.tasks.count()
        if total == 0:
            return '0%'
        completed = obj.tasks.filter(status__in=['APPROVED', 'COMPLETED']).count()
        return f'{(completed / total * 100):.1f}%'
    get_completion_percentage.short_description = 'Percentual de Conclusão'


@admin.register(AdminChecklistTask)
class AdminChecklistTaskAdmin(admin.ModelAdmin):
    list_display = ('get_task_title', 'get_checklist_date', 'get_sector', 'status', 'assigned_to', 'completed_at')
    list_filter = ('status', 'template__sector', 'template__priority', 'checklist__date', 'completed_at')
    search_fields = ('template__title', 'template__description', 'assigned_to__first_name', 'assigned_to__last_name')
    readonly_fields = ('checklist', 'template', 'completed_at', 'reviewed_at', 'reviewed_by')
    ordering = ('-checklist__date', 'template__sector', 'template__title')
    
    def get_task_title(self, obj):
        return obj.template.title
    get_task_title.short_description = 'Tarefa'
    
    def get_checklist_date(self, obj):
        return obj.checklist.date.strftime('%d/%m/%Y')
    get_checklist_date.short_description = 'Data'
    
    def get_sector(self, obj):
        return obj.template.sector.name
    get_sector.short_description = 'Setor'


@admin.register(AdminChecklistAssignment)
class AdminChecklistAssignmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'get_task_title', 'get_checklist_date', 'assigned_at', 'assigned_by')
    list_filter = ('assigned_at', 'assigned_by')
    search_fields = ('user__first_name', 'user__last_name', 'task__template__title')
    readonly_fields = ('assigned_at',)
    ordering = ('-assigned_at',)
    
    def get_task_title(self, obj):
        return obj.task.template.title
    get_task_title.short_description = 'Tarefa'
    
    def get_checklist_date(self, obj):
        return obj.task.checklist.date.strftime('%d/%m/%Y')
    get_checklist_date.short_description = 'Data'


@admin.register(AdminChecklistSectorTask)
class AdminChecklistSectorTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'sector', 'priority', 'date_requested', 'is_approved', 'created_by', 'created_at')
    list_filter = ('sector', 'priority', 'is_approved', 'date_requested', 'created_at')
    search_fields = ('title', 'description', 'instructions')
    ordering = ('-created_at', 'priority')
    readonly_fields = ('created_at', 'approved_at')
    
    fieldsets = (
        ('Informações da Tarefa', {
            'fields': ('title', 'description', 'instructions')
        }),
        ('Configurações', {
            'fields': ('sector', 'priority', 'estimated_time_minutes', 'date_requested')
        }),
        ('Controle', {
            'fields': ('created_by', 'is_approved', 'approved_by', 'approved_at')
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:  # Se é um novo objeto
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
