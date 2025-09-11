from django.urls import path
from . import views

urlpatterns = [
    path('', views.files_list_view, name='files_list'),
    path('upload/', views.file_upload_view, name='file_upload'),
    path('<int:file_id>/', views.file_detail_view, name='file_detail'),
    path('<int:file_id>/download/', views.file_download_view, name='file_download'),
    path('<int:file_id>/delete/', views.file_delete_view, name='file_delete'),
]
