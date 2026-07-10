from django.urls import path
from . import views

app_name = 'folhaponto'

urlpatterns = [
    # Área pessoal
    path('', views.my_folhas, name='my_folhas'),
    path('<int:pk>/', views.folha_detail, name='folha_detail'),
    path('<int:pk>/pdf/', views.folha_pdf, name='folha_pdf'),
    path('<int:pk>/pdf-assinado/', views.folha_signed_pdf, name='folha_signed_pdf'),

    # Área administrativa
    path('admin/', views.admin_folhas, name='admin_folhas'),
    path('admin/importar/', views.admin_import, name='admin_import'),
    path('admin/acessos/', views.admin_access, name='admin_access'),
    path('admin/excluir/<int:pk>/', views.admin_delete_folha, name='admin_delete_folha'),
    path('admin/reenviar-pdf/<int:pk>/', views.admin_reupload_folha_pdf, name='admin_reupload_folha_pdf'),
    path('admin/relatorio-assinaturas/', views.export_signature_report, name='export_signature_report'),

    # APIs
    path('api/importar/', views.api_import_folha, name='api_import_folha'),
    path('api/importar-lote/', views.api_bulk_import, name='api_bulk_import'),
    path('api/processar-pdf-completo/', views.api_process_full_pdf, name='api_process_full_pdf'),
    path('api/excluir-lote/', views.api_bulk_delete, name='api_bulk_delete'),
    path('api/assinar/<int:pk>/', views.api_sign_folha, name='api_sign_folha'),
    path('api/download-nao-encontrados/', views.api_download_unmatched_excel, name='api_download_unmatched_excel'),
]
