from django.urls import path
from . import views

urlpatterns = [
    # Visualização pública
    path('', views.trainings_list_view, name='trainings_list'),
    path('<int:pk>/', views.training_detail_view, name='training_detail'),
    
    # Gestão administrativa (apenas para admins)
    path('upload/', views.training_upload_view, name='training_upload'),
    path('manage/', views.training_manage_view, name='trainings_manage'),
    path('<int:pk>/toggle-status/', views.training_toggle_status_view, name='training_toggle_status'),
    path('<int:pk>/delete/', views.training_delete_view, name='training_delete'),
    
    # API
    path('api/update-progress/', views.update_training_progress, name='update_training_progress'),
]