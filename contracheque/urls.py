from django.urls import path
from . import views

app_name = 'contracheque'

urlpatterns = [
    # Área pessoal – Contracheques
    path('', views.my_payslips, name='my_payslips'),
    path('<int:pk>/', views.payslip_detail, name='payslip_detail'),
    path('<int:pk>/pdf/', views.payslip_pdf, name='payslip_pdf'),

    # Área pessoal – Informes de Rendimentos
    path('informes/', views.my_income_reports, name='my_income_reports'),
    path('informes/<int:pk>/', views.income_report_detail, name='income_report_detail'),
    path('informes/<int:pk>/pdf/', views.income_report_pdf, name='income_report_pdf'),

    # Área administrativa – Contracheques
    path('admin/', views.admin_payslips, name='admin_payslips'),
    path('admin/importar/', views.admin_import, name='admin_import'),
    path('admin/excluir/<int:pk>/', views.admin_delete_payslip, name='admin_delete_payslip'),
    path('admin/relatorio-assinaturas/', views.export_signature_report, name='export_signature_report'),

    # Área administrativa – Informes de Rendimentos
    path('admin/informes/', views.admin_income_reports, name='admin_income_reports'),
    path('admin/informes/importar/', views.admin_income_import, name='admin_income_import'),
    path('admin/informes/excluir/<int:pk>/', views.admin_delete_income_report, name='admin_delete_income_report'),

    # APIs
    path('api/importar/', views.api_import_payslip, name='api_import_payslip'),
    path('api/importar-lote/', views.api_bulk_import, name='api_bulk_import'),
    path('api/excluir-lote/', views.api_bulk_delete, name='api_bulk_delete'),
    path('api/assinar/<int:pk>/', views.api_sign_payslip, name='api_sign_payslip'),
    path('api/informes/importar-lote/', views.api_bulk_import_income, name='api_bulk_import_income'),
    path('api/informes/excluir-lote/', views.api_bulk_delete_income, name='api_bulk_delete_income'),
    path('api/download-nao-encontrados/', views.api_download_unmatched_excel, name='api_download_unmatched_excel'),
]
