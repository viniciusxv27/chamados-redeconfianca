from django.urls import path
from . import views

app_name = 'knowledge_trails'

urlpatterns = [
    # Visualização pública
    path('', views.trails_dashboard, name='dashboard'),
    path('trail/<int:trail_id>/', views.trail_detail, name='trail_detail'),
    path('lesson/<int:lesson_id>/', views.lesson_view, name='lesson_view'),
    path('trail/<int:trail_id>/leaderboard/', views.leaderboard, name='leaderboard'),
    path('certificate/<int:certificate_id>/', views.certificate_view, name='certificate_view'),
    path('certificate/<int:certificate_id>/download/', views.download_certificate_pdf, name='download_certificate'),
    
    # Gerenciamento (Supervisores)
    path('manage/<int:trail_id>/', views.manage_trail, name='manage_trail'),
    path('manage/<int:trail_id>/edit-map/', views.edit_trail_map, name='edit_trail_map'),
    
    # CRUD de Trilhas
    path('create/', views.create_trail, name='create_trail'),
    path('edit/<int:trail_id>/', views.edit_trail, name='edit_trail'),
    path('delete/<int:trail_id>/', views.delete_trail, name='delete_trail'),
    
    # CRUD de Módulos
    path('trail/<int:trail_id>/create-module/', views.create_module, name='create_module'),
    path('module/<int:module_id>/delete/', views.delete_module, name='delete_module'),
]
