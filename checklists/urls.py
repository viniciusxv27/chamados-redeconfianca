from django.urls import path
from . import views

app_name = 'checklists'

urlpatterns = [
    path('', views.checklist_dashboard, name='dashboard'),
    path('create/', views.create_assignment, name='create_assignment'),
    path('execute/<int:execution_id>/', views.execute_checklist, name='execute_checklist'),
    path('my/', views.my_checklists, name='my_checklists'),
    path('api/template/<int:template_id>/', views.api_get_template_details, name='api_template_details'),
    path('api/search-users/', views.api_search_users, name='api_search_users'),
]