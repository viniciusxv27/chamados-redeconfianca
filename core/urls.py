from django.urls import path
from . import views

urlpatterns = [
    path('training/', views.training_module, name='training'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('denuncias/', views.anonymous_report, name='anonymous_report'),
    path('admin/reports/', views.manage_reports, name='manage_reports'),
    path('admin/reports/<int:report_id>/', views.report_detail, name='report_detail'),
]
