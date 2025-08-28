from django.urls import path
from . import views

urlpatterns = [
    path('', views.communication_list, name='communications_list'),
    path('<int:communication_id>/', views.communication_detail_view, name='communication_detail'),
    path('<int:communication_id>/update-status/', views.update_communication_status, name='update_communication_status'),
    path('<int:communication_id>/edit/', views.edit_communication_view, name='edit_communication'),
    path('<int:communication_id>/delete/', views.delete_communication_view, name='delete_communication'),
    path('create/', views.create_communication_view, name='create_communication'),
    path('api/unread/', views.get_unread_communications, name='unread_communications'),
]
