from django.urls import path
from . import views

app_name = 'suppliers'

urlpatterns = [
    path('', views.supplier_list, name='supplier_list'),
    path('novo/', views.supplier_create, name='supplier_create'),
    path('<int:pk>/', views.supplier_detail, name='supplier_detail'),
    path('<int:pk>/editar/', views.supplier_edit, name='supplier_edit'),
    path('<int:pk>/excluir/', views.supplier_delete, name='supplier_delete'),
]