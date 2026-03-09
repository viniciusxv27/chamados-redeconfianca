from django.urls import path

from . import views

app_name = 'experiencia'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # Templates (perguntas)
    path('templates/', views.template_list, name='template_list'),
    path('templates/criar/', views.template_create, name='template_create'),
    path('templates/<int:template_id>/editar/', views.template_edit, name='template_edit'),
    path('templates/<int:template_id>/excluir/', views.template_delete, name='template_delete'),
    path('templates/importar-pdf/', views.import_template_pdf, name='import_template_pdf'),

    # Lançar to-do
    path('lancar/', views.launch_todo, name='launch_todo'),

    # Preencher / responder
    path('todo/<int:todo_id>/preencher/', views.fill_todo, name='fill_todo'),
    path('todo/<int:todo_id>/ver/', views.view_todo, name='view_todo'),

    # Avaliação
    path('todo/<int:todo_id>/avaliar/', views.evaluate_todo, name='evaluate_todo'),

    # Avaliadores
    path('avaliadores/', views.manage_evaluators, name='manage_evaluators'),

    # Relatórios
    path('relatorios/', views.reports, name='reports'),
    path('relatorios/exportar/', views.export_report, name='export_report'),

    # Arquivo
    path('arquivo/', views.archive, name='archive'),

    # API
    path('api/upload-foto/<int:answer_id>/', views.api_upload_photo, name='api_upload_photo'),
]
