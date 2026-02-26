from django.urls import path
from . import views

app_name = 'contracheque'

urlpatterns = [
    # Área pessoal
    path('', views.my_payslips, name='my_payslips'),
    path('<int:pk>/', views.payslip_detail, name='payslip_detail'),
    path('<int:pk>/pdf/', views.payslip_pdf, name='payslip_pdf'),

    # Área administrativa
    path('admin/', views.admin_payslips, name='admin_payslips'),
    path('admin/importar/', views.admin_import, name='admin_import'),
    path('admin/excluir/<int:pk>/', views.admin_delete_payslip, name='admin_delete_payslip'),

    # APIs
    path('api/importar/', views.api_import_payslip, name='api_import_payslip'),
    path('api/importar-lote/', views.api_bulk_import, name='api_bulk_import'),
]
