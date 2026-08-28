from django.urls import path

from . import views

app_name = 'impulso'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),

    # CONFIAR — metas / kanban
    path('metas/', views.metas_kanban, name='metas_kanban'),
    path('metas/nova/', views.meta_create, name='meta_create'),
    path('metas/solicitacoes/', views.meta_solicitacoes, name='meta_solicitacoes'),
    path('metas/<int:meta_id>/decidir/', views.meta_decidir, name='meta_decidir'),
    path('metas/<int:meta_id>/cancelar-solicitacao/',
         views.meta_solicitacao_cancelar, name='meta_solicitacao_cancelar'),
    path('metas/<int:meta_id>/excluir/', views.meta_excluir, name='meta_excluir'),
    path('metas/<int:meta_id>/item/', views.meta_item_add, name='meta_item_add'),
    path('metas/item/<int:item_id>/check/', views.meta_item_toggle, name='meta_item_toggle'),
    path('metas/item/<int:item_id>/editar/', views.meta_item_editar, name='meta_item_editar'),
    path('metas/item/<int:item_id>/excluir/', views.meta_item_excluir, name='meta_item_excluir'),
    path('metas/<int:meta_id>/itens/reordenar/', views.meta_itens_reordenar,
         name='meta_itens_reordenar'),
    path('metas/<int:meta_id>/', views.meta_detail, name='meta_detail'),
    path('metas/<int:meta_id>/status/', views.meta_update_status, name='meta_update_status'),
    path('metas/<int:meta_id>/entregar/', views.meta_entregar, name='meta_entregar'),
    path('metas/<int:meta_id>/avaliar/', views.meta_avaliar, name='meta_avaliar'),
    path('metas/<int:meta_id>/anexo/', views.meta_add_anexo, name='meta_add_anexo'),
    path('metas/<int:meta_id>/anexo/<int:anexo_id>/editar/',
         views.meta_anexo_editar, name='meta_anexo_editar'),
    path('metas/<int:meta_id>/anexo/<int:anexo_id>/excluir/',
         views.meta_anexo_excluir, name='meta_anexo_excluir'),
    path('metas/<int:meta_id>/responsaveis/',
         views.meta_participantes_editar, name='meta_participantes_editar'),
    path('metas/<int:meta_id>/comentar/', views.meta_add_comentario, name='meta_add_comentario'),
    path('metas/comentario/<int:comentario_id>/excluir/', views.meta_excluir_comentario,
         name='meta_excluir_comentario'),
    path('atividades/', views.minhas_atividades, name='minhas_atividades'),

    path('assiduidade/', views.assiduidade, name='assiduidade'),

    # CONFIAR — feedback
    path('feedbacks/', views.feedback_list, name='feedback_list'),
    path('feedbacks/novo/', views.feedback_create, name='feedback_create'),
    path('feedbacks/<int:fb_id>/', views.feedback_detail, name='feedback_detail'),
    path('feedbacks/<int:fb_id>/ia/', views.feedback_regenerar_ia, name='feedback_regenerar_ia'),

    # CONECTAR — conteúdos
    path('conectar/', views.conectar_list, name='conectar_list'),
    path('conectar/novo/', views.conteudo_create, name='conteudo_create'),
    path('conectar/<int:conteudo_id>/', views.conteudo_detail, name='conteudo_detail'),
    path('conectar/<int:conteudo_id>/concluir/', views.conteudo_concluir, name='conteudo_concluir'),
    path('conectar/<int:conteudo_id>/progresso/', views.conteudo_progresso_video,
         name='conteudo_progresso_video'),

    # CONECTAR — projeto foco
    path('conectar/projetos/', views.projeto_foco_list, name='projeto_foco_list'),
    path('conectar/projetos/novo/', views.projeto_foco_create, name='projeto_foco_create'),
    path('conectar/projetos/<int:projeto_id>/', views.projeto_foco_detail, name='projeto_foco_detail'),
    path('conectar/projetos/<int:projeto_id>/editar/', views.projeto_foco_edit, name='projeto_foco_edit'),
    path('conectar/projetos/<int:projeto_id>/tarefa/', views.tarefa_create, name='tarefa_create'),
    path('conectar/tarefa/<int:tarefa_id>/status/', views.tarefa_update_status, name='tarefa_update_status'),
    path('minhas-tarefas/', views.minhas_tarefas, name='minhas_tarefas'),

    # INOVAR
    path('inovar/', views.inovar_list, name='inovar_list'),
    path('inovar/nova/', views.ideia_create, name='ideia_create'),
    path('inovar/<int:ideia_id>/editar/', views.ideia_edit, name='ideia_edit'),
    path('inovar/<int:ideia_id>/status/', views.ideia_update_status, name='ideia_update_status'),

    # ACOMPANHAMENTO
    path('acompanhamento/', views.acompanhamento, name='acompanhamento'),
    path('acompanhamento/<int:user_id>/', views.detalhe_colaborador, name='detalhe_colaborador'),

    # ACOMPANHAMENTO — ciclos
    path('ciclos/', views.ciclo_list, name='ciclo_list'),
    path('ciclos/novo/', views.ciclo_create, name='ciclo_create'),
    path('ciclos/<int:ciclo_id>/', views.ciclo_detail, name='ciclo_detail'),
    path('ciclos/<int:ciclo_id>/encerrar/', views.ciclo_encerrar, name='ciclo_encerrar'),
    path('ciclos/mes/<int:mes_id>/', views.mes_detail, name='mes_detail'),
    path('ciclos/mes/<int:mes_id>/fechar/', views.mes_fechar, name='mes_fechar'),
    path('ciclos/mes/<int:mes_id>/reabrir/', views.mes_reabrir, name='mes_reabrir'),
]
