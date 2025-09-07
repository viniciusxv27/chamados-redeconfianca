from django.urls import path
from . import reports_views

app_name = 'reports'

urlpatterns = [
    path('', reports_views.reports_list_view, name='reports_list'),
    path('create/', reports_views.create_report_view, name='create_report'),
    path('<int:report_id>/', reports_views.report_detail_view, name='report_detail'),
    path('<int:report_id>/update-status/', reports_views.update_report_status, name='update_report_status'),
    path('<int:report_id>/add-comment/', reports_views.add_report_comment, name='add_report_comment'),
    path('dashboard/', reports_views.reports_dashboard_view, name='reports_dashboard'),
]
