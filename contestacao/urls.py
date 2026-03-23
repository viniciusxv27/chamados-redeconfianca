from django.urls import path
from . import views

app_name = 'contestacao'

urlpatterns = [
    path('', views.exclusion_list, name='exclusion_list'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('sincronizar/', views.sync_exclusions, name='sync_exclusions'),
    path('historico/', views.contestation_history, name='contestation_history'),
    path('contestar/<int:exclusion_id>/', views.create_contestation, name='create_contestation'),
    path('contestar-lote/', views.bulk_create_contestation, name='bulk_create_contestation'),
    path('minhas/', views.my_contestations, name='my_contestations'),
    path('gerenciar/', views.manage_contestations, name='manage_contestations'),
    path('gerenciar/liberar-gestor-global/', views.manage_global_contestation_managers, name='manage_global_contestation_managers'),
    path('gerenciar/liberar-refazer/', views.release_sector_for_retry, name='release_sector_for_retry'),
    path('gerenciar/vendas-a-contestar-vivo/', views.contested_with_vivo, name='contested_with_vivo'),
    path('dashboard/exportar-vendas/', views.export_contested_sales, name='export_contested_sales'),
    path('dashboard/exportar-relatorio/', views.export_contestation_report, name='export_contestation_report'),
    path('<int:pk>/', views.contestation_detail, name='contestation_detail'),
    path('<int:pk>/aprovar/', views.approve_contestation, name='approve_contestation'),
    path('<int:pk>/aprovar-e-contestar/', views.approve_and_contest_contestation, name='approve_and_contest_contestation'),
    path('<int:pk>/rejeitar/', views.reject_contestation, name='reject_contestation'),
    path('<int:pk>/anexo-errado/', views.toggle_attachment_wrong, name='toggle_attachment_wrong'),
    path('<int:pk>/confirmar/', views.confirm_contestation, name='confirm_contestation'),
    path('<int:pk>/negar/', views.deny_contestation, name='deny_contestation'),
    path('<int:pk>/marcar-pago/', views.mark_paid, name='mark_paid'),
    path('marcar-pago-lote/', views.bulk_mark_paid, name='bulk_mark_paid'),
]
