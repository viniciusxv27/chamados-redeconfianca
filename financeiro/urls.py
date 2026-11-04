from django.urls import path

from contagem_caixa import views_piscofins

from . import views, views_admin, views_banco, views_fechamento, views_importacao, views_recorrentes

app_name = 'financeiro'

urlpatterns = [
    path('', views.painel, name='painel'),
    path('caixa/', views.caixa, name='caixa'),

    # PIS/Cofins (a tela; as ações seguem em /contagem-caixa/pis-cofins/...)
    path('pis-cofins/', views_piscofins.piscofins, name='piscofins'),

    # Fechamento de caixa (dia D aberto até 10h de D+1)
    path('fechamento/', views_fechamento.fechamento, name='fechamento'),
    path('fechamento/salvar/', views_fechamento.fechamento_salvar, name='fechamento_salvar'),
    path('fechamento/bloquear/', views_fechamento.fechamento_bloquear, name='fechamento_bloquear'),
    path('fechamento/regra/', views_fechamento.fechamento_config, name='fechamento_config'),
    path('fechamento/acessos/', views_fechamento.fechamento_acessos, name='fechamento_acessos'),

    # Despesas recorrentes
    path('recorrentes/', views_recorrentes.recorrentes, name='recorrentes'),
    path('recorrentes/excel/', views_recorrentes.excel, name='recorrentes_excel'),
    path('recorrentes/nova/', views_recorrentes.despesa_nova, name='recorrente_nova'),
    path('recorrentes/vincular-sugestoes/', views_recorrentes.vincular_sugestoes, name='recorrentes_vincular_sugestoes'),
    path('recorrentes/vinculo/<int:pk>/desvincular/', views_recorrentes.desvincular, name='recorrente_desvincular'),
    path('recorrentes/<int:pk>/editar/', views_recorrentes.despesa_editar, name='recorrente_editar'),
    path('recorrentes/<int:pk>/excluir/', views_recorrentes.despesa_excluir, name='recorrente_excluir'),
    path('recorrentes/<int:pk>/mes/<int:ano>/<int:mes>/', views_recorrentes.mes_detalhe, name='recorrente_mes'),
    path('recorrentes/<int:pk>/mes/<int:ano>/<int:mes>/vincular/', views_recorrentes.vincular, name='recorrente_vincular'),
    path('recorrentes/<int:pk>/mes/<int:ano>/<int:mes>/lancar/', views_recorrentes.lancar, name='recorrente_lancar'),

    # Títulos
    path('pagar/', views.pagar, name='pagar'),
    path('pagar/importar/', views_importacao.importar, name='pagar_importar'),
    path('pagar/importar/modelo.csv', views_importacao.importar_modelo, name='pagar_importar_modelo'),
    path('receber/', views.receber, name='receber'),
    path('<str:area>/novo/', views.titulo_novo, name='titulo_novo'),
    path('titulo/<int:pk>/', views.titulo, name='titulo'),
    path('titulo/<int:pk>/editar/', views.titulo_editar, name='titulo_editar'),
    path('titulo/<int:pk>/cancelar/', views.titulo_cancelar, name='titulo_cancelar'),
    path('titulo/<int:pk>/excluir/', views.titulo_excluir, name='titulo_excluir'),
    path('titulo/<int:pk>/baixar/', views.titulo_baixar, name='titulo_baixar'),
    path('anexo/<str:tipo>/<int:pk>/', views.anexo, name='anexo'),

    # Baixas
    path('baixas/', views.baixa_de_titulos, name='baixas'),
    path('baixas/lote/', views.baixar_lote, name='baixar_lote'),
    path('pagas/', views.pagas, name='pagas'),
    path('recebidas/', views.recebidas, name='recebidas'),
    path('baixa/<int:pk>/editar/', views.baixa_editar, name='baixa_editar'),
    path('baixa/<int:pk>/estornar/', views.baixa_estornar, name='baixa_estornar'),

    # Contas bancárias
    path('bancos/', views_banco.bancos, name='bancos'),
    path('bancos/nova/', views_banco.conta_salvar, name='conta_nova'),
    path('bancos/<int:pk>/', views_banco.conta, name='conta'),
    path('bancos/<int:pk>/editar/', views_banco.conta_salvar, name='conta_editar'),
    path('bancos/<int:pk>/avulso/', views_banco.conta_avulso, name='conta_avulso'),
    path('bancos/<int:pk>/transferir/', views_banco.conta_transferir, name='conta_transferir'),
    path('bancos/movimento/<int:pk>/estornar/', views_banco.movimento_estornar, name='movimento_estornar'),

    # Conciliação bancária
    path('conciliacao/', views_banco.conciliacao, name='conciliacao'),
    path('conciliacao/<int:pk>/importar/', views_banco.conciliacao_importar, name='conciliacao_importar'),
    path('conciliacao/<int:pk>/automatica/', views_banco.conciliacao_automatica, name='conciliacao_automatica'),
    path('conciliacao/importacao/<int:pk>/excluir/', views_banco.importacao_excluir, name='importacao_excluir'),
    path('conciliacao/linha/<int:pk>/conciliar/', views_banco.linha_conciliar, name='linha_conciliar'),
    path('conciliacao/linha/<int:pk>/titulo/', views_banco.linha_conciliar_titulo, name='linha_conciliar_titulo'),
    path('conciliacao/linha/<int:pk>/desfazer/', views_banco.linha_desfazer, name='linha_desfazer'),
    path('conciliacao/linha/<int:pk>/ignorar/', views_banco.linha_ignorar, name='linha_ignorar'),
    path('conciliacao/linha/<int:pk>/lancar/', views_banco.linha_lancar, name='linha_lancar'),

    # Conciliação de cartões
    path('cartoes/', views_admin.cartoes, name='cartoes'),
    path('cartoes/importar/', views_admin.cartoes_importar, name='cartoes_importar'),
    path('cartoes/reconciliar/', views_admin.cartoes_reconciliar, name='cartoes_reconciliar'),
    path('cartoes/estabelecimento/', views_admin.cartoes_estabelecimento, name='cartoes_estabelecimento'),
    path('cartoes/<int:pk>/marcar/', views_admin.cartoes_marcar, name='cartoes_marcar'),
    path('cartoes/<int:pk>/vincular/', views_admin.cartoes_vincular, name='cartoes_vincular'),

    # Plano de contas
    path('plano/', views_admin.plano, name='plano'),
    path('plano/nova/', views_admin.plano_salvar, name='plano_nova'),
    path('plano/padrao/', views_admin.plano_padrao, name='plano_padrao'),
    path('plano/<int:pk>/', views_admin.plano_salvar, name='plano_editar'),
    path('plano/<int:pk>/ativar/', views_admin.plano_ativar, name='plano_ativar'),

    # Rastreabilidade e acessos
    path('auditoria/', views_admin.auditoria_lista, name='auditoria'),
    path('acessos/', views_admin.acessos, name='acessos'),
]
