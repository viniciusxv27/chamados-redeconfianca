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

    # Conectar a conta Google do dono (para quem não tem Workspace)
    path('oauth/conectar/', views.oauth_conectar, name='oauth_conectar'),
    path('oauth/callback/', views.oauth_callback, name='oauth_callback'),
    path('oauth/desconectar/', views.oauth_desconectar, name='oauth_desconectar'),

    # Meu Drive inteiro (SUPERADMIN, só com a conta própria conectada)
    path('meu-drive/', views.meu_drive, name='meu_drive'),
    path('meu-drive/f/<str:folder_id>/', views.meu_drive, name='meu_drive_folder'),
    path('meu-drive/a/<str:file_id>/', views.meu_drive_arquivo, name='meu_drive_arquivo'),
    path('meu-drive/a/<str:file_id>/conteudo/', views.meu_drive_conteudo, name='meu_drive_conteudo'),
    path('meu-drive/enviar/', views.meu_drive_upload, name='meu_drive_upload'),
    path('meu-drive/nova-pasta/', views.meu_drive_nova_pasta, name='meu_drive_nova_pasta'),
    path('meu-drive/pastas/', views.meu_drive_pastas, name='meu_drive_pastas'),
    path('meu-drive/a/<str:file_id>/renomear/', views.meu_drive_renomear, name='meu_drive_renomear'),
    path('meu-drive/a/<str:file_id>/mover/', views.meu_drive_mover, name='meu_drive_mover'),
    path('meu-drive/a/<str:file_id>/nova-versao/', views.meu_drive_nova_versao, name='meu_drive_nova_versao'),
    path('meu-drive/a/<str:file_id>/excluir/', views.meu_drive_excluir, name='meu_drive_excluir'),
    path('meu-drive/a/<str:file_id>/favoritar/', views.meu_drive_favoritar, name='meu_drive_favoritar'),
    path('meu-drive/a/<str:file_id>/versoes/', views.meu_drive_versoes, name='meu_drive_versoes'),
    path('meu-drive/a/<str:file_id>/versoes/restaurar/', views.meu_drive_versao_restaurar,
         name='meu_drive_versao_restaurar'),
]
