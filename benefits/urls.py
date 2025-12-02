from django.urls import path
from . import views

app_name = 'benefits'

urlpatterns = [
    # URLs públicas (para todos os usuários logados)
    path('', views.benefits_list, name='list'),
    path('<int:benefit_id>/', views.benefit_detail, name='detail'),
    path('<int:benefit_id>/redeem/', views.redeem_benefit, name='redeem'),
    
    # URLs de administração (apenas ADMIN e SUPERADMIN)
    path('admin/', views.admin_benefits_list, name='admin_list'),
    path('admin/create/', views.admin_create_benefit, name='admin_create'),
    path('admin/<int:benefit_id>/edit/', views.admin_edit_benefit, name='admin_edit'),
    path('admin/<int:benefit_id>/delete/', views.admin_delete_benefit, name='admin_delete'),
    
    # Histórico de resgates
    path('admin/history/', views.admin_history, name='admin_history'),
    path('admin/<int:benefit_id>/history/', views.admin_benefit_history, name='admin_benefit_history'),
]
