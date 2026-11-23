from django.urls import include, path

from . import arquivos, views

app_name = 'api_mobile'

urlpatterns = [
    path('auth/login/', views.login, name='login'),
    path('auth/logout/', views.logout, name='logout'),
    path('eu/', views.eu, name='eu'),
    path('push/registrar/', views.push_registrar, name='push_registrar'),
    path('push/desregistrar/', views.push_desregistrar, name='push_desregistrar'),
    path('inicio/', views.inicio, name='inicio'),
    path('chamados/', views.chamados, name='chamados'),
    path('chamados/setores/', views.setores, name='setores'),
    path('chamados/categorias/', views.categorias, name='categorias'),
    path('chamados/<int:pk>/', views.chamado, name='chamado'),
    path('chamados/<int:pk>/comentarios/', views.chamado_comentar, name='chamado_comentar'),
    path('comunicados/', views.comunicados, name='comunicados'),
    path('comunicados/<int:pk>/', views.comunicado, name='comunicado'),
    path('comunicados/<int:pk>/situacao/', views.comunicado_situacao, name='comunicado_situacao'),
    path('comunicados/<int:pk>/curtir/', views.comunicado_curtir, name='comunicado_curtir'),
    path('comunicados/<int:pk>/destinatarios/', views.comunicado_destinatarios, name='comunicado_destinatarios'),
    path('reconhecimentos/', views.reconhecimentos, name='reconhecimentos'),
    path('notificacoes/', views.notificacoes, name='notificacoes'),
    path('notificacoes/lidas/', views.notificacoes_lidas, name='notificacoes_lidas'),
    path('notificacoes/<str:ident>/lida/', views.notificacao_lida, name='notificacao_lida'),
    path('modulos/', views.modulos, name='modulos'),
    path('arquivo/<str:assinatura>/', arquivos.baixar, name='arquivo'),
    # Um arquivo por módulo (api_mobile/modulos/<chave>.py), cada um com a regra do próprio app.
    path('m/impulso/', include('api_mobile.modulos.impulso')),
    path('m/ponto/', include('api_mobile.modulos.ponto')),
    path('m/contracheque/', include('api_mobile.modulos.contracheque')),
    path('m/documentos/', include('api_mobile.modulos.documentos')),
    path('m/comissao/', include('api_mobile.modulos.comissao')),
    path('m/simulador/', include('api_mobile.modulos.simulador')),
    path('m/vendas/', include('api_mobile.modulos.vendas')),
    path('m/usuarios/', include('api_mobile.modulos.usuarios')),
    path('m/agenda/', include('api_mobile.modulos.agenda')),
    path('m/checklists/', include('api_mobile.modulos.checklists')),
    path('m/quadro_hc/', include('api_mobile.modulos.quadro_hc')),
    path('m/cameras/', include('api_mobile.modulos.cameras')),
    path('m/cursos/', include('api_mobile.modulos.cursos')),
    path('m/power_bi/', include('api_mobile.modulos.power_bi')),
    path('m/power_bi_metas/', include('api_mobile.modulos.power_bi_metas')),
    path('m/mercadinho/', include('api_mobile.modulos.mercadinho')),
    path('m/facilitador/', include('api_mobile.modulos.facilitador')),
    path('m/gestao_metas/', include('api_mobile.modulos.gestao_metas')),
]
