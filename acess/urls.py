from django.urls import path

from . import views

app_name = 'acess'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('lote/', views.concluir_lote, name='concluir_lote'),
    path('lojas/', views.lojas, name='lojas'),
    path('colaboradores/', views.colaboradores, name='colaboradores'),
    path('colaboradores/novo/', views.novo, name='novo'),
    path('colaboradores/excel/', views.exportar, name='exportar'),
    path('colaborador/<int:pk>/', views.ficha, name='ficha'),
    path('colaborador/<int:pk>/acao/', views.ficha_acao, name='ficha_acao'),
    path('colaborador/<int:pk>/esteira/', views.esteira_acao, name='esteira_acao'),
    path('colaborador/<int:pk>/whatsapp/', views.whatsapp, name='whatsapp'),
    path('colaborador/<int:pk>/xml-gefac/', views.xml_gefac_anexar, name='xml_gefac_anexar'),
    path('colaborador/<int:pk>/xml-gefac/<int:xml_id>/', views.xml_gefac_baixar, name='xml_gefac_baixar'),
    path('colaborador/<int:pk>/xml-gefac/<int:xml_id>/remover/', views.xml_gefac_remover, name='xml_gefac_remover'),
    path('pendentes/', views.pendentes, name='pendentes'),
    path('liberados/', views.liberados, name='liberados'),
    path('chamados/', views.chamados, name='chamados'),
    path('chamados/novo/', views.chamado_novo, name='chamado_novo'),
    path('chamados/<int:pk>/acao/', views.chamado_acao, name='chamado_acao'),
    path('solicitar/', views.solicitar, name='solicitar'),
    path('solicitacoes/', views.solicitacoes, name='solicitacoes'),
    path('solicitacoes/<int:pk>/responder/', views.solicitacao_responder, name='solicitacao_responder'),
    path('historico/', views.historico, name='historico'),
    path('pedir-desligamento/', views.pedir_desligamento, name='pedir_desligamento'),
    path('desligamentos/pedido/<int:pk>/', views.pedido_desligamento_decidir, name='pedido_desligamento_decidir'),
    path('desligamentos/', views.desligamentos, name='desligamentos'),
    path('desligamentos/<int:pk>/', views.desligamento_acao, name='desligamento_acao'),
    path('desligamentos/inativado/<int:pk>/', views.inativacao_decidir, name='inativacao_decidir'),
    path('mensagens/', views.mensagens, name='mensagens'),
    path('acessos/', views.acessos, name='acessos'),
    path('popups/', views.popups, name='popups'),
    path('popups/novo/', views.popup_nova, name='popup_nova'),
    path('popups/<int:pk>/', views.popup_respostas, name='popup_respostas'),
    path('popups/<int:pk>/editar/', views.popup_editar, name='popup_editar'),
    path('popups/<int:pk>/alternar/', views.popup_alternar, name='popup_alternar'),
    path('popup-login/<int:pk>/responder/', views.popup_login_responder, name='popup_login_responder'),
]
