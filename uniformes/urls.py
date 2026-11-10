from django.urls import path

from . import views

app_name = 'uniformes'

urlpatterns = [
    path('', views.estoque, name='estoque'),
    path('exportar/', views.estoque_exportar, name='estoque_exportar'),
    path('importar/', views.importar, name='importar'),
    path('item/novo/', views.item_novo, name='item_novo'),
    path('item/<int:pk>/editar/', views.item_editar, name='item_editar'),
    path('entrada/', views.entrada, name='entrada'),
    path('ajuste/', views.ajuste, name='ajuste'),
    path('retirada/', views.retirada, name='retirada'),
    path('devolucao/', views.devolucao, name='devolucao'),
    path('colaborador/<int:pk>/', views.colaborador, name='colaborador'),
    path('movimento/<int:pk>/data-entrega/', views.data_entrega, name='data_entrega'),
    path('termo/<uuid:lote>/', views.termo, name='termo'),
    path('historico/', views.historico, name='historico'),
    path('historico/exportar/', views.historico_exportar, name='historico_exportar'),
    # Quem cuida do estoque (lista mantida pelo SUPERADMIN).
    path('acessos/', views.acessos, name='acessos'),
    path('acessos/<int:pk>/remover/', views.acesso_remover, name='acesso_remover'),
]
