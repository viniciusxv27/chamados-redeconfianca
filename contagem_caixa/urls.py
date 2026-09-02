from django.urls import path

from . import views

app_name = 'contagem_caixa'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('loja/<int:loja_id>/', views.loja_detalhe, name='loja_detalhe'),
    path('loja/<int:loja_id>/salvar/', views.salvar_dia, name='salvar_dia'),
    path('loja/<int:loja_id>/saldo-inicial/', views.salvar_saldo_inicial,
         name='salvar_saldo_inicial'),
    path('importar/', views.importacao, name='importacao'),
]
