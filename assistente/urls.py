from django.urls import path

from . import views

app_name = 'assistente'

urlpatterns = [
    path('', views.chat, name='chat'),
    path('conectar/', views.conectar, name='conectar'),
    path('desconectar/', views.desconectar, name='desconectar'),
    path('testar/', views.testar, name='testar'),
    path('enviar/', views.enviar, name='enviar'),
    path('limpar/', views.limpar, name='limpar'),
    path('documentos/', views.documentos, name='documentos'),
    path('documentos/<int:pk>/', views.documento, name='documento'),
    path('documentos/<int:pk>/baixar/<str:formato>/', views.documento_baixar, name='documento_baixar'),
    path('documentos/<int:pk>/excluir/', views.documento_excluir, name='documento_excluir'),
]
