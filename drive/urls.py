from django.urls import path

from . import views

app_name = 'drive'

urlpatterns = [
    path('', views.index, name='index'),

    # Navegação por setor / pasta
    path('s/<int:sector_id>/', views.browse, name='browse'),
    path('s/<int:sector_id>/f/<str:folder_id>/', views.browse, name='browse_folder'),
    path('s/<int:sector_id>/upload/', views.upload, name='upload'),
    path('s/<int:sector_id>/mkdir/', views.mkdir, name='mkdir'),

    # Arquivo
    path('file/<str:file_id>/content/', views.file_content, name='file_content'),
    path('file/<str:file_id>/versoes/', views.file_versions, name='file_versions'),
    path('file/<str:file_id>/versoes/restaurar/', views.version_restore, name='version_restore'),
    path('file/<str:file_id>/renomear/', views.file_rename, name='file_rename'),
    path('file/<str:file_id>/mover/', views.file_move, name='file_move'),
    path('file/<str:file_id>/substituir/', views.file_replace, name='file_replace'),
    path('file/<str:file_id>/excluir/', views.file_delete, name='file_delete'),
    path('file/<str:file_id>/favoritar/', views.favorite_toggle, name='favorite_toggle'),
    path('file/<str:file_id>/', views.file_preview, name='file_preview'),

    # Áreas pessoais
    path('favoritos/', views.favoritos, name='favoritos'),
    path('recentes/', views.recentes, name='recentes'),
    path('busca/', views.busca, name='busca'),

    # Lixeira
    path('lixeira/', views.lixeira, name='lixeira'),
    path('lixeira/<str:file_id>/restaurar/', views.lixeira_restaurar, name='lixeira_restaurar'),
    path('lixeira/<str:file_id>/excluir/', views.lixeira_excluir, name='lixeira_excluir'),

    # Administração (SUPERADMIN / gestor)
    path('dashboard/', views.dashboard, name='dashboard'),
    path('auditoria/', views.auditoria, name='auditoria'),
    path('acessos/', views.acessos, name='acessos'),
    path('gestao/', views.gestao_setores, name='gestao_setores'),
    path('gestao/permissoes/', views.gestao_permissoes, name='gestao_permissoes'),
    path('gestao/permissoes/<int:pk>/excluir/', views.permissao_excluir, name='permissao_excluir'),
    path('configuracao/', views.configuracao, name='configuracao'),
]
