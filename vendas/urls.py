from django.urls import path

from . import views

app_name = 'vendas'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('nova/', views.venda_create, name='venda_create'),
    path('exportar/', views.venda_export, name='venda_export'),
    path('precos/', views.precos, name='precos'),
    path('precos/buscar/', views.precos_buscar, name='precos_buscar'),
    path('precos/importar/', views.precos_import, name='precos_import'),
    path('precos/novo/', views.precos_create, name='precos_create'),
    path('<int:pk>/', views.venda_detail, name='venda_detail'),
]
