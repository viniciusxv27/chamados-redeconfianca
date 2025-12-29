"""
Admin configuration for Notifications app
"""
from django.contrib import admin
from .models import (
    NotificationCategory, 
    PushNotification, 
    UserNotification, 
    DeviceToken, 
    NotificationPreference,
    TruepushSubscriber,
    TruepushNotificationLog,
    OneSignalPlayer,
    OneSignalNotificationLog
)


@admin.register(NotificationCategory)
class NotificationCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'icon', 'color', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name']
    ordering = ['name']


@admin.register(PushNotification)
class PushNotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'notification_type', 'priority', 'is_sent', 'created_by', 'created_at']
    list_filter = ['notification_type', 'priority', 'is_sent', 'send_to_all']
    search_fields = ['title', 'message']
    date_hierarchy = 'created_at'
    raw_id_fields = ['created_by']
    filter_horizontal = ['target_sectors', 'target_users']
    
    fieldsets = (
        ('Conteúdo', {
            'fields': ('title', 'message', 'category', 'icon')
        }),
        ('Tipo e Prioridade', {
            'fields': ('notification_type', 'priority')
        }),
        ('Ação', {
            'fields': ('action_url', 'action_text')
        }),
        ('Destinatários', {
            'fields': ('send_to_all', 'target_sectors', 'target_users')
        }),
        ('Agendamento', {
            'fields': ('schedule_for', 'is_scheduled')
        }),
        ('Status', {
            'fields': ('is_sent', 'sent_at', 'created_by')
        }),
        ('Extras', {
            'fields': ('extra_data',),
            'classes': ('collapse',)
        }),
    )


@admin.register(UserNotification)
class UserNotificationAdmin(admin.ModelAdmin):
    list_display = ['notification', 'user', 'is_read', 'is_clicked', 'created_at']
    list_filter = ['is_read', 'is_clicked', 'created_at']
    search_fields = ['notification__title', 'user__full_name', 'user__email']
    raw_id_fields = ['notification', 'user']
    date_hierarchy = 'created_at'


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ['user', 'device_type', 'is_active', 'created_at', 'last_used']
    list_filter = ['device_type', 'is_active']
    search_fields = ['user__full_name', 'user__email']
    raw_id_fields = ['user']
    date_hierarchy = 'created_at'


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'in_app_enabled', 'push_enabled', 'email_enabled', 'quiet_hours_enabled']
    list_filter = ['in_app_enabled', 'push_enabled', 'email_enabled', 'quiet_hours_enabled']
    search_fields = ['user__full_name', 'user__email']
    raw_id_fields = ['user']
    
    fieldsets = (
        ('Usuário', {
            'fields': ('user',)
        }),
        ('Canais', {
            'fields': ('in_app_enabled', 'push_enabled', 'email_enabled')
        }),
        ('Tipos de Notificação', {
            'fields': ('ticket_created', 'ticket_assigned', 'ticket_status_changed', 
                      'ticket_comment', 'communication_new')
        }),
        ('Horário Silencioso', {
            'fields': ('quiet_hours_enabled', 'quiet_hours_start', 'quiet_hours_end')
        }),
    )


@admin.register(TruepushSubscriber)
class TruepushSubscriberAdmin(admin.ModelAdmin):
    list_display = ['subscriber_id_short', 'user', 'device_type', 'browser', 'os', 'is_active', 'created_at']
    list_filter = ['device_type', 'is_active', 'browser', 'os']
    search_fields = ['subscriber_id', 'user__full_name', 'user__email']
    raw_id_fields = ['user']
    date_hierarchy = 'created_at'
    readonly_fields = ['subscriber_id', 'created_at', 'updated_at']
    
    def subscriber_id_short(self, obj):
        return f"{obj.subscriber_id[:30]}..." if len(obj.subscriber_id) > 30 else obj.subscriber_id
    subscriber_id_short.short_description = 'ID do Assinante'


@admin.register(TruepushNotificationLog)
class TruepushNotificationLogAdmin(admin.ModelAdmin):
    list_display = ['title', 'success_icon', 'sent_count', 'sent_to_all', 'sent_by', 'created_at']
    list_filter = ['success', 'sent_to_all', 'created_at']
    search_fields = ['title', 'message']
    date_hierarchy = 'created_at'
    raw_id_fields = ['sent_by']
    readonly_fields = ['title', 'message', 'url', 'segment_id', 'sent_to_all', 
                       'success', 'sent_count', 'response_data', 'error_message',
                       'sent_by', 'created_at']
    
    def success_icon(self, obj):
        if obj.success:
            return '✅ Sucesso'
        return '❌ Erro'
    success_icon.short_description = 'Status'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False


# =============================================================================
# OneSignal Admin Classes
# =============================================================================

@admin.register(OneSignalPlayer)
class OneSignalPlayerAdmin(admin.ModelAdmin):
    list_display = ['player_id_short', 'user', 'device_type', 'browser', 'os', 'is_active', 'created_at']
    list_filter = ['device_type', 'is_active', 'browser', 'os']
    search_fields = ['player_id', 'user__full_name', 'user__email']
    raw_id_fields = ['user']
    date_hierarchy = 'created_at'
    readonly_fields = ['player_id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Identificação', {
            'fields': ('player_id', 'user')
        }),
        ('Dispositivo', {
            'fields': ('device_type', 'browser', 'os')
        }),
        ('Status', {
            'fields': ('is_active',)
        }),
        ('Informações Adicionais', {
            'fields': ('extra_data', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def player_id_short(self, obj):
        return f"{obj.player_id[:30]}..." if len(obj.player_id) > 30 else obj.player_id
    player_id_short.short_description = 'Player ID'


@admin.register(OneSignalNotificationLog)
class OneSignalNotificationLogAdmin(admin.ModelAdmin):
    list_display = ['title', 'success_icon', 'sent_count', 'delivery_status', 'sent_by', 'created_at']
    list_filter = ['success', 'sent_to_all', 'created_at']
    search_fields = ['title', 'message', 'notification_id']
    date_hierarchy = 'created_at'
    raw_id_fields = ['sent_by']
    readonly_fields = ['notification_id', 'title', 'message', 'url', 'segment', 'sent_to_all', 
                       'success', 'sent_count', 'response_data', 'error_message',
                       'sent_by', 'created_at']
    
    fieldsets = (
        ('Identificação', {
            'fields': ('notification_id', 'title', 'message')
        }),
        ('Destino', {
            'fields': ('url', 'segment', 'sent_to_all')
        }),
        ('Resultado', {
            'fields': ('success', 'sent_count')
        }),
        ('Resposta da API', {
            'fields': ('response_data', 'error_message'),
            'classes': ('collapse',)
        }),
        ('Metadados', {
            'fields': ('sent_by', 'created_at')
        }),
    )
    
    def success_icon(self, obj):
        if obj.success:
            return '✅ Sucesso'
        return '❌ Erro'
    success_icon.short_description = 'Status'
    
    def delivery_status(self, obj):
        if obj.success:
            return f"✅ Enviada para {obj.sent_count} dispositivos"
        return f"❌ {obj.error_message[:50]}..." if obj.error_message else "❌ Erro"
    delivery_status.short_description = 'Entrega'
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False