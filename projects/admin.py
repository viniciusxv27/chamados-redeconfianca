from django.contrib import admin
from django.utils.html import format_html
from .models import (
    ProjectSectorAccess, 
    Project, 
    ProjectAttachment, 
    Activity, 
    ActivityComment
)


@admin.register(ProjectSectorAccess)
class ProjectSectorAccessAdmin(admin.ModelAdmin):
    list_display = [
        'sector', 'can_view_projects', 'can_create_projects', 
        'can_manage_all_projects', 'created_at'
    ]
    list_filter = ['can_view_projects', 'can_create_projects', 'can_manage_all_projects']
    search_fields = ['sector__name']
    readonly_fields = ['created_at', 'updated_at']


class ProjectAttachmentInline(admin.TabularInline):
    model = ProjectAttachment
    extra = 0
    readonly_fields = ['file_size', 'content_type', 'uploaded_by', 'uploaded_at']


class ActivityInline(admin.TabularInline):
    model = Activity
    extra = 0
    fields = ['name', 'status', 'deadline', 'responsible_user', 'order']
    ordering = ['order']


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'sector', 'status', 'priority', 'progress_display', 
        'deadline', 'created_by', 'created_at'
    ]
    list_filter = [
        'status', 'priority', 'sector', 'created_at', 'deadline'
    ]
    search_fields = ['name', 'description', 'created_by__full_name']
    readonly_fields = ['created_at', 'updated_at', 'progress_percentage']
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('name', 'description', 'scope', 'reason')
        }),
        ('Datas', {
            'fields': ('start_date', 'deadline', 'completion_date')
        }),
        ('Status e Controle', {
            'fields': ('status', 'priority', 'progress_percentage')
        }),
        ('Responsabilidades', {
            'fields': ('created_by', 'responsible_user', 'sector')
        }),
        ('Metadados', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    inlines = [ActivityInline, ProjectAttachmentInline]
    
    def progress_display(self, obj):
        color = 'green' if obj.progress_percentage >= 80 else 'orange' if obj.progress_percentage >= 50 else 'red'
        return format_html(
            '<span style="color: {};">{:.1f}%</span>',
            color,
            obj.progress_percentage
        )
    progress_display.short_description = 'Progresso'


class ActivityCommentInline(admin.TabularInline):
    model = ActivityComment
    extra = 0
    readonly_fields = ['created_at']


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'project', 'status', 'priority', 'deadline', 
        'responsible_user', 'level_display'
    ]
    list_filter = [
        'status', 'priority', 'project__sector', 'deadline'
    ]
    search_fields = [
        'name', 'description', 'project__name', 'responsible_user__full_name'
    ]
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Informações Básicas', {
            'fields': ('project', 'parent_activity', 'name', 'description')
        }),
        ('Datas', {
            'fields': ('start_date', 'deadline', 'completion_date')
        }),
        ('Status e Controle', {
            'fields': ('status', 'priority', 'order')
        }),
        ('Responsabilidades', {
            'fields': ('responsible_user', 'created_by')
        }),
        ('Metadados', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    inlines = [ActivityCommentInline]
    
    def level_display(self, obj):
        return '→ ' * obj.level + str(obj.level)
    level_display.short_description = 'Nível'


@admin.register(ProjectAttachment)
class ProjectAttachmentAdmin(admin.ModelAdmin):
    list_display = [
        'project', 'original_filename', 'file_size_formatted', 
        'uploaded_by', 'uploaded_at'
    ]
    list_filter = ['content_type', 'uploaded_at']
    search_fields = ['project__name', 'original_filename', 'uploaded_by__full_name']
    readonly_fields = ['file_size', 'content_type', 'uploaded_at']


@admin.register(ActivityComment)
class ActivityCommentAdmin(admin.ModelAdmin):
    list_display = ['activity', 'user', 'content_preview', 'created_at']
    list_filter = ['created_at', 'activity__project']
    search_fields = ['content', 'user__full_name', 'activity__name']
    readonly_fields = ['created_at']
    
    def content_preview(self, obj):
        return obj.content[:50] + '...' if len(obj.content) > 50 else obj.content
    content_preview.short_description = 'Conteúdo'
