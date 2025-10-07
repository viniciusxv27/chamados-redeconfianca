from django.urls import path
from . import views, api_views, views_chat
from .views_chat import get_task_chat, send_task_message, support_chat_list, get_support_chat, create_support_chat, send_support_message

app_name = 'projects'

urlpatterns = [
    # Dashboard
    path('', views.project_dashboard, name='dashboard'),
    
    # Projetos
    path('list/', views.project_list, name='project_list'),
    path('create/', views.project_create, name='project_create'),
    path('project/<int:project_id>/', views.project_detail, name='project_detail'),
    path('project/<int:project_id>/edit/', views.project_edit, name='project_edit'),
    path('project/<int:project_id>/delete/', views.project_delete, name='project_delete'),
    
    # Atividades
    path('project/<int:project_id>/activity/create/', views.activity_create, name='activity_create'),
    path('activity/<int:activity_id>/edit/', views.activity_edit, name='activity_edit'),
    path('activity/<int:activity_id>/status/', views.activity_update_status, name='activity_update_status'),
    path('activity/<int:activity_id>/update-status/', views.activity_update_status, name='activity_drag_update_status'),
    
    # API para modal de detalhes
    path('activity/<int:activity_id>/detail/', views.activity_detail_api, name='activity_detail_api'),
    path('activity/<int:activity_id>/comment/', views.activity_add_comment, name='activity_add_comment'),
    path('activity/<int:activity_id>/subtask/', views.activity_add_subtask, name='activity_add_subtask'),
    path('subtask/<int:subtask_id>/toggle/', views.subtask_toggle, name='subtask_toggle'),
    path('subtask/<int:subtask_id>/delete/', views.subtask_delete, name='subtask_delete'),
    path('activity/<int:activity_id>/duplicate/', views.activity_duplicate, name='activity_duplicate'),
    path('activity/<int:activity_id>/archive/', views.activity_archive, name='activity_archive'),
    
    # Chat URLs
    path('task/<int:activity_id>/chat/', views_chat.get_task_chat, name='get_task_chat'),
    path('task/<int:activity_id>/chat/send/', views_chat.send_task_message, name='send_task_message'),
    
    # Support Chat URLs
    path('support/chats/', views_chat.support_chat_list, name='support_chat_list'),
    path('support/chat/<int:chat_id>/', views_chat.get_support_chat, name='get_support_chat'),
    path('support/chat/create/', views_chat.create_support_chat, name='create_support_chat'),
    path('support/chat/<int:chat_id>/send/', views_chat.send_support_message, name='send_support_message'),
    path('support/sectors/', views_chat.get_sectors, name='get_sectors'),
    path('support/sectors/<int:sector_id>/categories/', views_chat.get_sector_categories, name='get_sector_categories'),
    path('support/chat/<int:chat_id>/files/upload/', views_chat.upload_chat_file, name='upload_chat_file'),
    path('support/chat/<int:chat_id>/rate/', views_chat.rate_support_chat, name='rate_support_chat'),
    
    # Support Admin URLs
    path('support/admin/', views_chat.support_admin_dashboard, name='support_admin_dashboard'),
    path('support/admin/template/', views_chat.support_admin_template, name='support_admin_template'),
    path('support/chat/<int:chat_id>/assign/', views_chat.assign_chat_to_agent, name='assign_chat_to_agent'),
    path('support/metrics/', views_chat.support_metrics, name='support_metrics'),
    path('support/metrics/export/', views_chat.export_metrics_report, name='export_metrics_report'),
    path('support/admin/categories/', views_chat.manage_support_categories, name='manage_support_categories'),
    path('support/admin/agents/', views_chat.manage_support_agents, name='manage_support_agents'),
    
    # API
    path('api/sectors/<int:sector_id>/users/', api_views.sector_users_api, name='sector_users_api'),
]