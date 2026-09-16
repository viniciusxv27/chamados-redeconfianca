from django.urls import path

from . import views

app_name = 'apresentacoes'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('nova/', views.nova, name='nova'),
    path('<int:pk>/', views.editor, name='editor'),
    path('<int:pk>/apresentar/', views.apresentar, name='apresentar'),
    path('<int:pk>/documento/', views.documento, name='documento'),
    path('<int:pk>/versoes/', views.versoes, name='versoes'),
    path('<int:pk>/versoes/<int:versao_id>/restaurar/', views.restaurar, name='restaurar'),
    path('<int:pk>/mensagens/', views.mensagens, name='mensagens'),
    path('<int:pk>/duplicar/', views.duplicar, name='duplicar'),
    path('<int:pk>/excluir/', views.excluir, name='excluir'),
    path('<int:pk>/midias/', views.midias_lista, name='midias_lista'),
    path('<int:pk>/ia/', views.ia, name='ia'),
    path('<int:pk>/iniciar/', views.iniciar, name='iniciar'),
    path('<int:pk>/exportar/pptx/', views.exportar_pptx, name='exportar_pptx'),
    path('<int:pk>/exportar/canva/', views.exportar_canva, name='exportar_canva'),
    path('<int:pk>/video/', views.video, name='video'),
    path('midias/', views.midias_upload, name='midias_upload'),
    path('midia/<int:pk>/', views.midia, name='midia'),
    path('tarefas/<int:pk>/', views.tarefa, name='tarefa'),
    path('tarefas/<int:pk>/responder/', views.responder, name='responder'),
    path('modulos/', views.modulos, name='modulos'),
    path('modulos/<slug:label>/gerar/', views.modulo_gerar, name='modulo_gerar'),
    path('templates/', views.templates_lista, name='templates'),
    path('templates/novo/', views.template_novo, name='template_novo'),
    path('templates/<int:pk>/', views.template_editor, name='template_editor'),
    path('templates/<int:pk>/documento/', views.template_documento, name='template_documento'),
    path('templates/<int:pk>/midias/', views.template_midias, name='template_midias'),
    path('templates/<int:pk>/limpar-fundo/', views.template_limpar_fundo, name='template_limpar_fundo'),
    path('templates/<int:pk>/excluir/', views.template_excluir, name='template_excluir'),
    path('templates/<int:pk>/opcoes/', views.template_opcoes, name='template_opcoes'),
    path('configuracoes/', views.configuracoes, name='configuracoes'),
    path('canva/conectar/', views.canva_conectar, name='canva_conectar'),
    path('canva/retorno/', views.canva_retorno, name='canva_retorno'),
    path('canva/desconectar/', views.canva_desconectar, name='canva_desconectar'),
]
