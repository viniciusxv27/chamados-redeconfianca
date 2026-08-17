from django.urls import path

from . import views

app_name = 'tangerino'

urlpatterns = [
    # Ponto
    path('ponto/', views.meu_ponto, name='meu_ponto'),
    path('ponto/equipe/', views.ponto_equipe, name='ponto_equipe'),
    path('ponto/folhas/', views.folhas_sincronizadas, name='folhas_sincronizadas'),

    # Férias
    path('ferias/', views.minhas_ferias, name='minhas_ferias'),
    path('ferias/equipe/', views.ferias_equipe, name='ferias_equipe'),
    path('ferias/em-ferias/', views.em_ferias, name='em_ferias'),

    # Administração
    path('ponto/configuracao/', views.configuracao, name='configuracao'),
    path('ponto/configuracao/sincronizar/', views.sincronizar_dados, name='sincronizar_dados'),
    path('ponto/vinculos/', views.vinculos, name='vinculos'),
    path('ponto/vinculos/sincronizar/', views.sincronizar, name='sincronizar'),
    path('ponto/vinculos/vincular/', views.vincular_manual, name='vincular_manual'),

    # APIs usadas pelo widget e pelo popup
    path('api/tangerino/ponto/status/', views.api_ponto_status, name='api_ponto_status'),
    path('api/tangerino/ponto/bater/', views.api_bater_ponto, name='api_bater_ponto'),
    path('api/tangerino/ferias/popup/', views.api_ferias_popup, name='api_ferias_popup'),
]
