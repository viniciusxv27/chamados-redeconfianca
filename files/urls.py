from django.urls import path
from . import views

app_name = 'files'

urlpatterns = [
    path('', views.files_list, name='files_list'),
    path('upload/', views.file_upload_view, name='file_upload'),
    path('<int:pk>/', views.file_detail, name='file_detail'),
    path('<int:pk>/download/', views.file_download, name='file_download'),
    path('create-folder/', views.create_folder, name='create_folder'),
    path('create-category/', views.create_category, name='create_category'),
    path('move-file/', views.move_file, name='move_file'),
    path('get-sectors/', views.get_sectors, name='get_sectors'),
    path('delete-folder/<int:folder_id>/', views.delete_folder, name='delete_folder'),
    path('delete-category/<int:category_id>/', views.delete_category, name='delete_category'),
    path('delete-file/<int:file_id>/', views.file_delete_view, name='delete_file'),
]
