from django.contrib import admin

from .models import PopupCompletion, PortalPopup


@admin.register(PortalPopup)
class PortalPopupAdmin(admin.ModelAdmin):
    list_display = ['title', 'order', 'completion_mode', 'blocking_mode',
                    'target_all', 'is_active', 'updated_at']
    list_filter = ['is_active', 'completion_mode', 'blocking_mode', 'target_all']
    search_fields = ['title', 'message']
    filter_horizontal = ['target_users', 'target_sectors']
    readonly_fields = ['created_at', 'updated_at', 'created_by']


@admin.register(PopupCompletion)
class PopupCompletionAdmin(admin.ModelAdmin):
    list_display = ['popup', 'user', 'completed_at']
    search_fields = ['user__first_name', 'user__last_name', 'popup__title']
    raw_id_fields = ['popup', 'user']
    readonly_fields = ['completed_at']
