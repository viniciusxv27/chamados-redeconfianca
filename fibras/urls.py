from django.urls import path

from . import views

app_name = 'fibras'

urlpatterns = [
    path('', views.kanban, name='kanban'),
    path('lista/', views.lista, name='lista'),
    path('sync/', views.sync_now, name='sync_now'),
    path('importar/', views.importar_planilha, name='importar_planilha'),
    path('relatorio/', views.relatorio, name='relatorio'),
    # Validação: Base de Vendas x Esteira de Vendas Vivo (cruzamento completo)
    path('validacao/', views.validacao, name='validacao'),
    path('validacao/api/estado/', views.validacao_estado, name='validacao_estado'),
    path('validacao/api/cruzar/', views.validacao_cruzar, name='validacao_cruzar'),
    path('validacao/api/pedido/', views.validacao_pedido, name='validacao_pedido'),
    path('validacao/api/ajuste/', views.validacao_ajuste, name='validacao_ajuste'),
    path('validacao/api/ajustes/limpar/', views.validacao_ajustes_limpar, name='validacao_ajustes_limpar'),
    path('validacao/api/lojas/', views.validacao_lojas, name='validacao_lojas'),
    path('validacao/api/importar/', views.validacao_importar, name='validacao_importar'),
    path('validacao/api/sincronizar/', views.validacao_sincronizar, name='validacao_sincronizar'),
    path('validacao/api/previa/', views.validacao_previa, name='validacao_previa'),
    path('validacao/api/baixar/', views.validacao_baixar, name='validacao_baixar'),
    path('export/excel/', views.export_excel, name='export_excel'),
    path('export/canceladas-csv/', views.export_canceladas_csv, name='export_canceladas_csv'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/status/', views.change_status_view, name='change_status'),
    path('<int:pk>/status/ajax/', views.change_status_ajax, name='change_status_ajax'),
    path('<int:pk>/incidente/', views.abrir_incidente, name='abrir_incidente'),
    path('<int:pk>/chat/', views.chat_view, name='chat'),
    path('<int:pk>/chat/post/', views.chat_post, name='chat_post'),
]
