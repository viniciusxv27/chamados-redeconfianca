from django.urls import path

from . import views

app_name = 'solicitacoes_dp'

urlpatterns = [
    path('solicitacoes/', views.minhas, name='minhas'),
    path('solicitacoes/nova/<str:tipo>/', views.nova, name='nova'),
    path('solicitacoes/marcacoes-do-dia/', views.marcacoes_do_dia, name='marcacoes_do_dia'),
    path('solicitacoes/<int:pk>/', views.detalhe, name='detalhe'),
    path('solicitacoes/<int:pk>/corrigir/', views.corrigir, name='corrigir'),
    path('solicitacoes/<int:pk>/acao/', views.acao, name='acao'),
    path('solicitacoes/<int:pk>/anexo/<int:anexo_pk>/', views.anexo, name='anexo'),
    path('dp/', views.caixa, name='caixa'),
    path('dp/responsaveis/', views.responsaveis, name='responsaveis'),
]
