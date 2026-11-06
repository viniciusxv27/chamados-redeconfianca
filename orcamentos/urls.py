from django.urls import path

from . import views

app_name = 'orcamentos'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('<int:pk>/abrir/', views.reabrir, name='reabrir'),
    path('mailing/', views.mailing_loja, name='mailing'),
    path('mailing/contato/<int:pk>/', views.contato_acao, name='contato_acao'),
    path('gestao/', views.gestao, name='gestao'),
    path('gestao/importar/', views.importar, name='importar'),
    path('gestao/<int:pk>/conferir/', views.conferir, name='conferir'),
    path('gestao/<int:pk>/', views.base_acao, name='base_acao'),
    path('painel/', views.painel_view, name='painel'),
    path('acessos/', views.acessos, name='acessos'),
]
