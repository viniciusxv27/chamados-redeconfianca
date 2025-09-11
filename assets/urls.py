from django.urls import path
from . import views

app_name = 'assets'

urlpatterns = [
    path('', views.asset_list, name='list'),
    path('create/', views.asset_create, name='create'),
    path('<int:pk>/', views.asset_detail, name='detail'),
    path('<int:pk>/edit/', views.asset_edit, name='edit'),
    path('<int:pk>/delete/', views.asset_delete, name='delete'),
    path('export-excel/', views.export_assets_excel, name='export_excel'),
    path('import-excel/', views.import_assets_excel, name='import_excel'),
]
