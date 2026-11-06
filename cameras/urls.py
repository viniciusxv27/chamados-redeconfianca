from django.urls import path

from . import views, views_agente

app_name = 'cameras'

urlpatterns = [
    path('', views.painel, name='painel'),
    path('estado/', views.estado, name='estado'),
    path('fotos/', views.fotos, name='fotos'),
    path('loja/<int:gravador_id>/', views.loja, name='loja'),
    path('canal/<int:canal_id>/salvar/', views.canal_salvar, name='canal_salvar'),
    path('indicadores/', views.indicadores, name='indicadores'),
    path('alertas/', views.alertas, name='alertas'),
    path('alertas/<int:alerta_id>/', views.alerta_detalhe, name='alerta_detalhe'),
    path('alertas/<int:alerta_id>/foto/', views.alerta_foto, name='alerta_foto'),
    path('alertas/<int:alerta_id>/video/', views.alerta_video, name='alerta_video'),
    path('alertas/<int:alerta_id>/quadro/<int:indice>/', views.alerta_quadro, name='alerta_quadro'),
    path('alertas/<int:alerta_id>/ia/', views.alerta_ia, name='alerta_ia'),
    path('aprendizado/', views.aprendizado_view, name='aprendizado'),
    path('configuracao/', views.configuracao, name='configuracao'),
    path('configuracao/importar/', views.importar, name='importar'),
    path('configuracao/<int:gravador_id>/', views.gravador_editar, name='gravador_editar'),
    path('configuracao/<int:gravador_id>/instalador/', views.gravador_instalador, name='gravador_instalador'),
    path('configuracao/<int:gravador_id>/token/', views.gravador_token, name='gravador_token'),
    path('aovivo/abrir/', views.ao_vivo_abrir, name='ao_vivo_abrir'),
    path('aovivo/respostas/', views.ao_vivo_respostas, name='ao_vivo_respostas'),
    path('aovivo/estado/', views.ao_vivo_estado, name='ao_vivo_estado'),
    path('agente/aovivo/', views_agente.ao_vivo_pedidos, name='agente_ao_vivo'),
    path('agente/aovivo/resposta/', views_agente.ao_vivo_resposta, name='agente_ao_vivo_resposta'),
    path('agente/ativar/', views_agente.ativar, name='agente_ativar'),
    path('agente/sinal/', views_agente.sinal, name='agente_sinal'),
    path('agente/descoberta/', views_agente.receber_descoberta, name='agente_descoberta'),
    path('agente/metricas/', views_agente.receber_metricas, name='agente_metricas'),
    path('agente/fotos/', views_agente.receber_fotos, name='agente_fotos'),
    path('agente/alerta/', views_agente.receber_alerta, name='agente_alerta'),
    path('agente/clipe/', views_agente.receber_clipe, name='agente_clipe'),
]
