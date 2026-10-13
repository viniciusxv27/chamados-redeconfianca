from django.urls import path

from . import views

app_name = 'webmail'

urlpatterns = [
    path('', views.caixa, name='caixa'),
    path('conectar/', views.conectar, name='conectar'),
    path('desconectar/', views.desconectar, name='desconectar'),
    path('preferencias/', views.preferencias, name='preferencias'),
    path('anexo/', views.anexo, name='anexo'),
    path('original/', views.original, name='original'),
    path('api/pastas/', views.api_pastas, name='api_pastas'),
    path('api/pastas/criar/', views.api_criar_pasta, name='api_criar_pasta'),
    path('api/lista/', views.api_lista, name='api_lista'),
    path('api/mensagem/', views.api_mensagem, name='api_mensagem'),
    path('api/acao/', views.api_acao, name='api_acao'),
    path('api/resumo/', views.api_resumo, name='api_resumo'),
    path('api/novidades/', views.api_novidades, name='api_novidades'),
    path('api/contador/', views.api_contador, name='api_contador'),
    path('api/contatos/', views.api_contatos, name='api_contatos'),
    path('api/enviar/', views.api_enviar, name='api_enviar'),
    path('api/rascunho/', views.api_rascunho, name='api_rascunho'),
]
