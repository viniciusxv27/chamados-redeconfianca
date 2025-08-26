from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard_view, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('admin/', views.admin_panel_view, name='admin_panel'),
    path('admin/users/', views.manage_users_view, name='manage_users'),
]
