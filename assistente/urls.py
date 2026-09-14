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
]
