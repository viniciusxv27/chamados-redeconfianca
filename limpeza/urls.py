from django.urls import path

from . import views

app_name = 'limpeza'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # Registrar uma limpeza (checklist simples, sem validação)
    path('registrar/', views.registro_novo, name='registro_novo'),
    path('registro/<int:registro_id>/', views.registro_detalhe, name='registro_detalhe'),

    # Checklists (perguntas)
    path('templates/', views.template_list, name='template_list'),
    path('templates/criar/', views.template_create, name='template_create'),
    path('templates/<int:template_id>/editar/', views.template_edit, name='template_edit'),
    path('templates/<int:template_id>/excluir/', views.template_delete, name='template_delete'),
    path('templates/importar-pdf/', views.import_template_pdf, name='import_template_pdf'),

    # Relatórios
    path('relatorios/', views.reports, name='reports'),
    path('relatorios/exportar/', views.export_report, name='export_report'),

    # Arquivo
    path('arquivo/', views.archive, name='archive'),
]
