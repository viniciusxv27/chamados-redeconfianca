from django.urls import path, include
from . import historico_views, views
from .test_views import test_upload_view

app_name = 'core'

urlpatterns = [
    path('training/', views.training_module, name='training'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('denuncias/', views.anonymous_report, name='anonymous_report'),
    path('test-upload/', test_upload_view, name='test_upload'),  # View de teste
    
    # Notifications API
    path('api/notifications/', views.notifications_api_view, name='notifications_api'),
    path('api/notifications/count/', views.notifications_count_api_view, name='notifications_count_api'),
    path('api/notifications/<int:notification_id>/mark-read/', views.notification_mark_read_api_view, name='notification_mark_read_api'),
    path('api/notifications/mark-all-read/', views.notifications_mark_all_read_api_view, name='notifications_mark_all_read_api'),
    
    # Histórico de Atividades (Superadmin)
    path('activity-history/', historico_views.activity_history, name='activity_history'),
    path('activity-history/export/', historico_views.export_activity_history, name='export_activity_history'),
    path('activity-history/limpar/', historico_views.limpar_historico, name='limpar_historico'),
    path('activity-history/limpar/iniciar/', historico_views.limpar_iniciar, name='limpar_historico_iniciar'),
    path('activity-history/limpar/<int:limpeza_id>/lote/', historico_views.limpar_lote, name='limpar_historico_lote'),
    
    # Checklist Administrativo
    path('admin-checklist/', include('core.admin_checklist_urls')),
]
