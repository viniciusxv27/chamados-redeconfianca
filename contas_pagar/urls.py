from django.urls import path

from . import views

app_name = 'contas_pagar'
urlpatterns = [
    path('', views.painel, name='painel'),
    path('pagar-hoje/', views.pagar_hoje, name='pagar_hoje'),
    path('pagar-hoje/<int:pk>/', views.pagar_hoje_montar, name='pagar_hoje_montar'),
    path('pagar-hoje/<int:pk>/descartar/', views.descartar, name='descartar'),
    path('historico/', views.historico, name='historico'),
    path('historico/excluir/', views.historico_excluir, name='historico_excluir'),
    path('configuracoes/', views.configuracoes, name='configuracoes'),
    path('acessos/', views.acessos, name='acessos'),
    # APIs da montagem do pagar hoje (e o painel em JSON)
    path('api/painel/', views.api_painel, name='api_painel'),
    path('api/rascunhos/', views.api_importar, name='api_importar'),
    path('api/rascunhos/<int:pk>/', views.api_rascunho, name='api_rascunho'),
    path('api/rascunhos/<int:pk>/descartar/', views.api_descartar, name='api_descartar'),
]
