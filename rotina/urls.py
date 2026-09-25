from django.urls import path

from . import api, views

app_name = 'rotina'

urlpatterns = [
    # Telas
    path('', views.minha, name='minha'),
    path('gestao/', views.gestao, name='gestao'),
    path('gestao/adicionar/', views.gestao_adicionar, name='gestao_adicionar'),
    path('gestao/<int:user_id>/', views.gestao_pessoa, name='gestao_pessoa'),
    path('gestao/<int:user_id>/acao/', views.gestao_pessoa_acao, name='gestao_pessoa_acao'),
    path('modelos/', views.modelos, name='modelos'),
    path('modelos/novo/', views.modelo_novo, name='modelo_novo'),
    path('modelos/<int:modelo_id>/', views.modelo_editor, name='modelo_editor'),
    path('modelos/<int:modelo_id>/acao/', views.modelo_acao, name='modelo_acao'),

    # API da rotina de uma pessoa
    path('api/rotina/', api.rotina_semana, name='api_rotina'),
    path('api/rotina/atividades/', api.rotina_criar, name='api_rotina_criar'),
    path('api/rotina/atividades/<int:atividade_id>/', api.rotina_atualizar, name='api_rotina_atualizar'),
    path('api/rotina/atividades/<int:atividade_id>/excluir/', api.rotina_excluir, name='api_rotina_excluir'),
    # Concluir a atividade do dia (com o comprovante, quando ela exige).
    path('api/rotina/atividades/<int:atividade_id>/concluir/', api.rotina_concluir,
         name='api_rotina_concluir'),
    path('api/rotina/atividades/<int:atividade_id>/desfazer/', api.rotina_desfazer,
         name='api_rotina_desfazer'),
    path('api/gestao/<int:user_id>/opcoes/', api.gestao_opcoes, name='api_gestao_opcoes'),

    # API dos modelos
    path('api/modelos/<int:modelo_id>/atividades/', api.modelo_atividades, name='api_modelo_atividades'),
    path('api/modelos/atividades/<int:atividade_id>/', api.modelo_atualizar, name='api_modelo_atualizar'),
    path('api/modelos/atividades/<int:atividade_id>/excluir/', api.modelo_excluir, name='api_modelo_excluir'),

    # Notificador e cartão da home
    path('api/hoje/', api.hoje, name='api_hoje'),
    path('api/hoje/cartao/', api.cartao_home, name='api_cartao_home'),
    path('api/avisos/<int:atividade_id>/', api.aviso, name='api_aviso'),
    path('api/lembretes/<int:atividade_id>/', api.lembrete, name='api_lembrete'),
]
