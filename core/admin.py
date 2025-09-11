from django.contrib import admin
from .models import SystemLog, Tutorial, Report, ReportComment, Notification


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
