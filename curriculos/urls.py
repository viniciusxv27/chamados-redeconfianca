from django.urls import path

from . import views

app_name = 'curriculos'

urlpatterns = [
    path('', views.banco, name='banco'),
    path('importar/', views.importar, name='importar'),
    path('configuracao/', views.configuracao, name='configuracao'),
    path('<int:curriculo_id>/', views.detalhe, name='detalhe'),
    path('<int:curriculo_id>/atualizar/', views.atualizar, name='atualizar'),
    path('<int:curriculo_id>/excluir/', views.excluir, name='excluir'),
]
