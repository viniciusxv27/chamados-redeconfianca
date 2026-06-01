from django.urls import path
from . import views

app_name = 'simulator'

urlpatterns = [
    path('', views.simulator_dashboard, name='dashboard'),
    path('admin/factors/', views.simulator_admin_factors, name='admin_factors'),
    path('admin/stores/', views.simulator_admin_stores, name='admin_stores'),
    path('admin/snipers/', views.simulator_admin_snipers, name='admin_snipers'),
]
