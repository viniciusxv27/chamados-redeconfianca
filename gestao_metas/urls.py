from django.urls import path

from . import views

app_name = 'gestao_metas'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('abrir/', views.abrir, name='abrir'),
    path('importar/', views.importar, name='importar'),
    path('importacoes/<int:pk>/', views.importacao_ver, name='importacao'),
    path('lojas/', views.lojas, name='lojas'),
    path('acessos/', views.acessos, name='acessos'),
    path('<str:codigo>/', views.competencia, name='competencia'),
    path('<str:codigo>/salvar/', views.salvar, name='salvar'),
    path('<str:codigo>/preencher/', views.preencher, name='preencher'),
    path('<str:codigo>/fechar/', views.fechar, name='fechar'),
    path('<str:codigo>/excel/', views.baixar_excel, name='excel'),
    path('<str:codigo>/historico/', views.historico, name='historico'),
]
