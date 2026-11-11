from django.urls import path

from . import views

app_name = 'auditoria_d1'

urlpatterns = [
    path('', views.esteira, name='esteira'),
    path('acessos/', views.acessos, name='acessos'),
    path('configuracao/', views.configuracao, name='configuracao'),

    path('api/meta', views.api_meta, name='api_meta'),
    path('api/dias', views.api_dias, name='api_dias'),
    path('api/vendas', views.api_vendas, name='api_vendas'),
    path('api/verificacoes', views.api_verificacoes, name='api_verificacoes'),
    path('api/verificacoes/<str:id_servico>', views.api_reabrir, name='api_reabrir'),
    path('api/contestacoes', views.api_contestacoes, name='api_contestacoes'),
    path('api/contestacoes/<str:id_servico>', views.api_desfazer_contestacao, name='api_desfazer_contestacao'),
    path('api/historico/<str:id_servico>', views.api_historico, name='api_historico'),
    path('api/exportar-planilha', views.api_exportar_planilha, name='api_exportar_planilha'),
    path('api/exportar', views.api_exportar, name='api_exportar'),

    path('api/sva/previa', views.api_sva_previa, name='api_sva_previa'),
    path('api/sva/aplicar', views.api_sva_aplicar, name='api_sva_aplicar'),
    path('api/sva/importacoes', views.api_sva_importacoes, name='api_sva_importacoes'),
    path('api/sva/importacoes/<int:imp_id>/planilha', views.api_sva_planilha, name='api_sva_planilha'),
    path('api/sva/lojas', views.api_sva_lojas, name='api_sva_lojas'),
    path('api/sva/whatsapp', views.api_sva_whatsapp, name='api_sva_whatsapp'),
    path('api/sva/whatsapp/cancelar', views.api_sva_whatsapp_cancelar, name='api_sva_whatsapp_cancelar'),

    path('api/fixa/previa', views.api_fixa_previa, name='api_fixa_previa'),
    path('api/fixa/aplicar', views.api_fixa_aplicar, name='api_fixa_aplicar'),
    path('api/fixa/importacoes', views.api_fixa_importacoes, name='api_fixa_importacoes'),
    path('api/fixa/importacoes/<int:imp_id>/planilha', views.api_fixa_planilha, name='api_fixa_planilha'),
]
