from django.urls import path
from . import views
from . import order_views

app_name = 'assets'

urlpatterns = [
    # =========================================================================
    # INVENTÁRIO - ROTAS PRINCIPAIS
    # =========================================================================
    
    # Dashboard
    path('', views.inventory_dashboard, name='inventory_dashboard'),
    
    # Categorias
    path('categorias/', views.category_list, name='category_list'),
    path('categorias/nova/', views.category_create, name='category_create'),
    path('categorias/<int:pk>/editar/', views.category_edit, name='category_edit'),
    path('categorias/<int:pk>/excluir/', views.category_delete, name='category_delete'),
    
    # Produtos
    path('produtos/', views.product_list, name='product_list'),
    path('produtos/novo/', views.product_create, name='product_create'),
    path('produtos/<int:pk>/', views.product_detail, name='product_detail'),
    path('produtos/<int:pk>/editar/', views.product_edit, name='product_edit'),
    path('produtos/<int:pk>/excluir/', views.product_delete, name='product_delete'),
    path('produtos/<int:pk>/midia/', views.product_media_upload, name='product_media_upload'),
    path('produtos/<int:pk>/midia/<int:media_pk>/excluir/', views.product_media_delete, name='product_media_delete'),
    
    # Itens de Inventário
    path('itens/', views.item_list, name='item_list'),
    path('itens/novo/', views.item_create, name='item_create'),
    path('itens/lote/', views.item_bulk_create, name='item_bulk_create'),
    path('itens/<int:pk>/', views.item_detail, name='item_detail'),
    path('itens/<int:pk>/editar/', views.item_edit, name='item_edit'),
    
    # Movimentações
    path('movimentacoes/', views.movement_list, name='movement_list'),
    path('entrada/', views.stock_entry, name='stock_entry'),
    path('saida/', views.stock_exit, name='stock_exit'),
    
    # Relatórios
    path('relatorios/', views.report_dashboard, name='report_dashboard'),
    path('relatorios/estoque/', views.report_stock, name='report_stock'),
    path('relatorios/entradas/', views.report_entries, name='report_entries'),
    path('relatorios/saidas/', views.report_exits, name='report_exits'),
    path('relatorios/estoque/excel/', views.export_stock_excel, name='export_stock_excel'),
    path('relatorios/movimentacoes/excel/', views.export_movements_excel, name='export_movements_excel'),
    
    # Gestores
    path('gestores/', views.manager_list, name='manager_list'),
    path('gestores/novo/', views.manager_create, name='manager_create'),
    path('gestores/<int:pk>/editar/', views.manager_edit, name='manager_edit'),
    path('gestores/<int:pk>/excluir/', views.manager_delete, name='manager_delete'),
    path('gestores/<int:pk>/toggle/', views.manager_toggle, name='manager_toggle'),
    
    # Catálogo / Mercadinho (visão do solicitante)
    path('catalogo/', views.store_catalog, name='store_catalog'),

    # Solicitações de Itens
    path('solicitacoes/', views.item_request_list, name='item_request_list'),
    path('solicitacoes/nova/', views.item_request_create, name='item_request_create'),
    path('solicitacoes/<int:pk>/', views.item_request_detail, name='item_request_detail'),
    path('solicitacoes/<int:pk>/aprovar/', views.item_request_approve, name='item_request_approve'),
    path('solicitacoes/<int:pk>/rejeitar/', views.item_request_reject, name='item_request_reject'),
    path('solicitacoes/<int:pk>/entregar/', views.item_request_deliver, name='item_request_deliver'),
    path('solicitacoes/<int:pk>/cancelar/', views.item_request_cancel, name='item_request_cancel'),
    path('solicitacoes/<int:pk>/contraproposta/', views.item_request_counterproposal, name='item_request_counterproposal'),
    path('solicitacoes/<int:pk>/aceitar-contraproposta/', views.item_request_accept_counterproposal, name='item_request_accept_counterproposal'),
    path('solicitacoes/<int:pk>/recusar-contraproposta/', views.item_request_reject_counterproposal, name='item_request_reject_counterproposal'),
    
    # =========================================================================
    # PEDIDOS (Criação de pedidos - estilo prêmios)
    # =========================================================================
    # Solicitante (GERENTES+)
    path('pedidos/', order_views.order_catalog, name='order_catalog'),
    path('pedidos/criar/', order_views.order_create, name='order_create'),
    path('pedidos/meus/', order_views.my_orders, name='my_orders'),
    path('pedidos/<int:pk>/', order_views.order_detail, name='order_detail'),
    path('pedidos/<int:pk>/cancelar/', order_views.order_cancel, name='order_cancel'),

    # Gestão (SUPERVISOR+)
    path('pedidos/gerenciar/', order_views.manage_orders, name='manage_orders'),
    path('pedidos/<int:pk>/aprovar/', order_views.order_approve, name='order_approve'),
    path('pedidos/<int:pk>/reprovar/', order_views.order_reject, name='order_reject'),
    path('pedidos/<int:pk>/entregar/', order_views.order_deliver, name='order_deliver'),

    # Cadastro de produtos de pedido (SUPERVISOR+)
    path('pedidos/produtos/', order_views.order_product_list, name='order_product_list'),
    path('pedidos/produtos/novo/', order_views.order_product_create, name='order_product_create'),
    path('pedidos/produtos/<int:pk>/editar/', order_views.order_product_edit, name='order_product_edit'),
    path('pedidos/produtos/<int:pk>/excluir/', order_views.order_product_delete, name='order_product_delete'),
    path('pedidos/categorias/nova/', order_views.order_category_create, name='order_category_create'),
    
    # =========================================================================
    # ATIVOS LEGADOS (DEPRECATED - manter por compatibilidade)
    # =========================================================================
    path('legado/', views.asset_list, name='list'),
    path('legado/create/', views.asset_create, name='create'),
    path('legado/<int:pk>/', views.asset_detail, name='detail'),
    path('legado/<int:pk>/edit/', views.asset_edit, name='edit'),
    path('legado/<int:pk>/delete/', views.asset_delete, name='delete'),
    path('legado/export-excel/', views.export_assets_excel, name='export_excel'),
    path('legado/import-excel/', views.import_assets_excel, name='import_excel'),
]
