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
    path('pendentes/', views.pendentes, name='pendentes'),
    path('liberados/', views.liberados, name='liberados'),
    path('chamados/', views.chamados, name='chamados'),
    path('chamados/novo/', views.chamado_novo, name='chamado_novo'),
    path('chamados/<int:pk>/acao/', views.chamado_acao, name='chamado_acao'),
    path('solicitar/', views.solicitar, name='solicitar'),
    path('solicitacoes/', views.solicitacoes, name='solicitacoes'),
    path('solicitacoes/<int:pk>/responder/', views.solicitacao_responder, name='solicitacao_responder'),
    path('historico/', views.historico, name='historico'),
    path('desligamentos/', views.desligamentos, name='desligamentos'),
    path('desligamentos/<int:pk>/', views.desligamento_acao, name='desligamento_acao'),
    path('desligamentos/inativado/<int:pk>/', views.inativacao_decidir, name='inativacao_decidir'),
    path('mensagens/', views.mensagens, name='mensagens'),
    path('acessos/', views.acessos, name='acessos'),
]
