from django.contrib import admin

from .models import GoalEntry, GoalUpload, PowerBIReport


@admin.register(PowerBIReport)
class PowerBIReportAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'sort_order', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'description', 'embed_url')
    filter_horizontal = ('allowed_groups', 'allowed_sectors', 'allowed_users')


@admin.register(GoalUpload)
class GoalUploadAdmin(admin.ModelAdmin):
    list_display = ('month', 'year', 'source_file_name', 'uploaded_by', 'updated_at')
    list_filter = ('year', 'month')
    search_fields = ('source_file_name',)


@admin.register(GoalEntry)
class GoalEntryAdmin(admin.ModelAdmin):
    list_display = ('upload', 'sheet_type', 'user_name', 'store_name', 'pilar', 'goal_value')
    list_filter = ('sheet_type', 'upload__year', 'upload__month')
    search_fields = ('user_name', 'store_name', 'pilar')
