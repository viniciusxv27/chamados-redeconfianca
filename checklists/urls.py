from django.urls import path
from . import views

app_name = 'checklists'

urlpatterns = [
    path('', views.checklist_dashboard, name='dashboard'),
    path('today/', views.execute_today_checklists, name='today_checklists'),
    path('create/', views.create_assignment, name='create_assignment'),
    path('execute/<int:assignment_id>/', views.execute_checklist, name='execute_checklist'),  # Compatibilidade com URLs antigas (assignment_id + ?period=)
    path('view/<int:execution_id>/', views.view_execution, name='view_execution'),  # Nova URL para visualização (execution_id direto)
    path('my/', views.my_checklists, name='my_checklists'),
    
    # API
    path('api/template/<int:template_id>/', views.api_get_template_details, name='api_template_details'),
    path('api/search-users/', views.api_search_users, name='api_search_users'),
    path('api/group/<int:group_id>/members/', views.api_group_members, name='api_group_members'),
    path('api/day-checklists/', views.api_get_day_checklists, name='api_day_checklists'),
    path('api/unassign/<int:assignment_id>/', views.api_unassign_checklist, name='api_unassign_checklist'),
    path('api/upload-evidence/<int:task_exec_id>/', views.api_upload_evidence, name='api_upload_evidence'),
    path('api/delete-evidence/<int:evidence_id>/', views.api_delete_evidence, name='api_delete_evidence'),
    
    # Admin - Templates
    path('admin/templates/', views.admin_templates, name='admin_templates'),
    path('admin/templates/create/', views.create_template, name='create_template'),
    path('admin/templates/<int:template_id>/edit/', views.edit_template, name='edit_template'),
    path('admin/templates/<int:template_id>/delete/', views.delete_template, name='delete_template'),
    
    # Admin - Approvals
    path('admin/approvals/', views.admin_approvals, name='admin_approvals'),
    path('admin/approve/<int:execution_id>/', views.approve_checklist, name='approve_checklist'),
    path('admin/reject/<int:execution_id>/', views.reject_checklist, name='reject_checklist'),
    path('admin/approve-all/', views.approve_all_checklists, name='approve_all_checklists'),
    path('admin/reject-all/', views.reject_all_checklists, name='reject_all_checklists'),
    
    # Admin - Task Approvals
    path('admin/approve-task/<int:task_exec_id>/', views.approve_task, name='approve_task'),
    path('admin/reject-task/<int:task_exec_id>/', views.reject_task, name='reject_task'),
    path('admin/unapprove-task/<int:task_exec_id>/', views.unapprove_task, name='unapprove_task'),
    
    # Admin - Relatórios e Exportação
    path('admin/reports/', views.checklist_reports, name='checklist_reports'),
    path('admin/export/', views.export_checklists, name='export_checklists'),
    
    # Admin - Controle de Execuções
    path('admin/executions/', views.admin_executions, name='admin_executions'),
    path('api/delete-executions/', views.api_delete_executions, name='api_delete_executions'),
]