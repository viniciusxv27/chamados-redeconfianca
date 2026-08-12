from django.urls import path

from . import views

app_name = 'cartoes'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('novo/', views.cartao_create, name='cartao_create'),
    path('<int:pk>/', views.cartao_extrato, name='extrato'),
    path('<int:pk>/gasto/', views.gasto_create, name='gasto_create'),
    path('<int:pk>/gasto/analisar/', views.gasto_analyze, name='gasto_analyze'),
]
