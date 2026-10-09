from django.urls import path

from . import views, views_clientes

app_name = 'vendas'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('nova/', views.venda_create, name='venda_create'),
    path('exportar/', views.venda_export, name='venda_export'),
    path('cliente/', views.cliente_historico, name='cliente_historico'),
    path('precos/', views.precos, name='precos'),
    path('precos/buscar/', views.precos_buscar, name='precos_buscar'),
    path('precos/importar/', views.precos_import, name='precos_import'),
    path('precos/novo/', views.precos_create, name='precos_create'),
    path('<int:pk>/', views.venda_detail, name='venda_detail'),

    # SLV — consultas da tela de venda e parametrização.
    path('api/cliente/', views.api_cliente, name='api_cliente'),
    path('api/cliente/salvar/', views.api_cliente_salvar, name='api_cliente_salvar'),
    path('api/renova/', views.api_renova, name='api_renova'),
    path('clientes/', views_clientes.clientes, name='clientes'),
    path('clientes/sincronizar/', views_clientes.clientes_sincronizar, name='clientes_sincronizar'),
    path('clientes/<str:cpf>/', views_clientes.cliente_detalhe, name='cliente_detalhe'),
    path('parametros/', views.parametros, name='parametros'),
    path('parametros/plano/', views.parametros_plano, name='parametros_plano'),
    path('parametros/vivo-mais/', views.parametros_vivo_mais, name='parametros_vivo_mais'),
    path('parametros/adicional/', views.parametros_adicional, name='parametros_adicional'),
]
