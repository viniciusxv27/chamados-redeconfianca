from django.contrib import admin

from .models import PowerBIReport


@admin.register(PowerBIReport)
class PowerBIReportAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'sort_order', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'description', 'embed_url')
    filter_horizontal = ('allowed_groups', 'allowed_sectors', 'allowed_users')
