from django.urls import path

from . import views, views_automacao

app_name = 'auditoria_sap'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('painel/', views.painel, name='painel'),
    path('atualizar/', views.atualizar, name='atualizar'),
    path('linha/<int:linha_id>/', views.detalhe, name='detalhe'),
    path('linha/<int:linha_id>/marcar/', views.marcar, name='marcar'),
    path('gestores/', views.gestores, name='gestores'),
    path('renova/', views.renova, name='renova'),
    path('renova/<int:venda_id>/marcar/', views.renova_marcar, name='renova_marcar'),
    path('renova/configurar/', views.renova_config, name='renova_config'),
    # Automação SAP: o SUPERADMIN publica versões; o app da loja se atualiza sozinho pela API.
    path('automacao/', views_automacao.tela, name='automacao'),
    path('automacao/enviar/', views_automacao.enviar, name='automacao_enviar'),
    path('automacao/<int:versao_id>/publicar/', views_automacao.publicar, name='automacao_publicar'),
    path('automacao/<int:versao_id>/baixar/', views_automacao.baixar, name='automacao_baixar'),
    path('automacao/chave/', views_automacao.trocar_chave, name='automacao_chave'),
    path('automacao/api/versao/', views_automacao.api_versao, name='automacao_api_versao'),
    path('automacao/api/baixar/<str:versao>/', views_automacao.api_baixar, name='automacao_api_baixar'),
]
