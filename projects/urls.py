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
    path('activity/<int:activity_id>/status/', views.activity_update_status, name='activity_update_status'),
    
    # API
    path('api/sectors/<int:sector_id>/users/', api_views.sector_users_api, name='sector_users_api'),
]