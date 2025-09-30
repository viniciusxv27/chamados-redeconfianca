from django.urls import path
from . import views, api_views

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
    path('activity/<int:activity_id>/duplicate/', views.activity_duplicate, name='activity_duplicate'),
    path('activity/<int:activity_id>/archive/', views.activity_archive, name='activity_archive'),
    
    # API
    path('api/sectors/<int:sector_id>/users/', api_views.sector_users_api, name='sector_users_api'),
]