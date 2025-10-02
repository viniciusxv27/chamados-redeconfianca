from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    # Dashboard e administração
    path('', views.notifications_dashboard, name='dashboard'),
    path('create/', views.create_notification, name='create'),
    path('manage/', views.manage_notifications, name='manage'),
    path('<int:notification_id>/send/', views.send_notification_now, name='send'),
    
    # Notificações do usuário
    path('my/', views.notifications_dashboard, name='user_notifications'),
    path('<int:notification_id>/mark-read/', views.mark_notification_read, name='mark_read'),
    
    # API endpoints
    path('api/unread-count/', views.api_unread_count, name='api_unread_count'),
    path('api/recent/', views.api_recent_notifications, name='api_recent'),
    path('api/<int:notification_id>/mark-read/', views.api_mark_as_read, name='api_mark_read'),
    path('api/mark-all-read/', views.api_mark_all_as_read, name='api_mark_all_read'),
    
    # Additional endpoints
    path('count/', views.get_notifications_count, name='count'),
    path('mark-all-read/', views.mark_all_notifications_read, name='mark_all_read'),
    path('register-device/', views.register_device_token, name='register_device'),
    path('delete-device/<int:token_id>/', views.delete_device_token, name='delete_device'),
    path('settings/', views.notification_settings, name='settings'),
    path('test/', views.test_push_page, name='test_page'),
    path('api/test-push/', views.test_push_notification, name='test_push'),
    path('api/vapid-key/', views.api_vapid_key, name='api_vapid_key'),
    path('api/subscribe/', views.api_subscribe_push, name='api_subscribe'),
]