from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    # Dashboard de notificações do usuário
    path('', views.notifications_dashboard, name='dashboard'),
    
    # Gerenciamento (apenas SUPERADMINs)
    path('manage/', views.manage_notifications, name='manage'),
    path('create/', views.create_notification, name='create'),
    path('send-now/<int:notification_id>/', views.send_notification_now, name='send_now'),
    
    # Ações do usuário
    path('mark-read/<int:notification_id>/', views.mark_notification_read, name='mark_read'),
    path('mark-all-read/', views.mark_all_notifications_read, name='mark_all_read'),
    path('count/', views.get_notifications_count, name='count'),
    
    # Push notifications
    path('register-token/', views.register_device_token, name='register_token'),
]