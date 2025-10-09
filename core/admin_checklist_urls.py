from django.urls import path
from . import views

app_name = 'admin_checklist'

urlpatterns = [
    # Dashboard principal
    path('', views.admin_checklist_dashboard, name='dashboard'),
    
    # Tarefas
    path('tasks/', views.admin_checklist_tasks, name='tasks'),
    path('tasks/<int:task_id>/action/', views.admin_checklist_task_action, name='task_action'),
    path('tasks/user/', views.admin_checklist_user_tasks, name='user_tasks'),
    
    # Visualização detalhada
    path('detail/<int:checklist_id>/', views.admin_checklist_detail_view, name='detail'),
    
    # Atribuições
    path('assign-task/', views.admin_checklist_assign_task, name='assign_task'),
    path('bulk-assign/', views.admin_checklist_bulk_assign, name='bulk_assign'),
    
    # Atividades
    path('add-activity/', views.admin_checklist_add_activity, name='add_activity'),
    
    # Gerenciamento (apenas superadmin)
    path('templates/', views.admin_checklist_templates, name='templates'),
    path('assignments/', views.admin_checklist_assignments, name='assignments'),
    path('assignments/create/', views.admin_checklist_create_assignment, name='create_assignment'),
    path('assignments/<int:assignment_id>/toggle/', views.admin_checklist_toggle_assignment, name='toggle_assignment'),
    path('assignments/<int:assignment_id>/delete/', views.admin_checklist_delete_assignment, name='delete_assignment'),
    
    # Relatórios e exportação
    path('reports/', views.admin_checklist_reports, name='reports'),
    
    # Tarefas de Setor
    path('sector-tasks/', views.admin_checklist_sector_tasks, name='sector_tasks'),
    path('sector-tasks/create/', views.admin_checklist_create_sector_task, name='create_sector_task'),
    path('sector-tasks/approve/', views.admin_checklist_approve_sector_tasks, name='approve_sector_tasks'),
]