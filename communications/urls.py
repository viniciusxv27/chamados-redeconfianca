from django.urls import path
from . import views

urlpatterns = [
    path('', views.communication_list, name='communications_list'),
    path('create/', views.create_communication_view, name='create_communication'),
    path('<int:communication_id>/', views.communication_detail_view, name='communication_detail'),
    path('<int:communication_id>/edit/', views.edit_communication_view, name='edit_communication'),
    path('<int:communication_id>/delete/', views.delete_communication_view, name='delete_communication'),
    path('<int:communication_id>/update-status/', views.update_communication_status, name='update_communication_status'),
    path('<int:communication_id>/react/', views.communication_react, name='react_to_communication'),
    path('<int:communication_id>/comment/', views.add_comment, name='add_comment'),
    path('comment/<int:comment_id>/delete/', views.delete_comment, name='delete_comment'),
]
