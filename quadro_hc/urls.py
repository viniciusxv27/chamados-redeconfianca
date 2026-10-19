from django.urls import path

from . import views

app_name = 'quadro_hc'

urlpatterns = [
    path('', views.quadro, name='quadro'),
    path('simular/', views.simular, name='simular'),
    path('movimentar/', views.movimentar, name='movimentar'),
    path('movimentacao/<int:pk>/aplicar/', views.mov_aplicar, name='mov_aplicar'),
    path('movimentacao/<int:pk>/cancelar/', views.mov_cancelar, name='mov_cancelar'),
    path('observacao/', views.observacao, name='observacao'),
    path('vaga/', views.vaga_abrir, name='vaga_abrir'),
    path('vaga/<int:pk>/fechar/', views.vaga_fechar, name='vaga_fechar'),
    path('carteira/', views.carteira_salvar, name='carteira_salvar'),
    path('carteira/<int:pk>/excluir/', views.carteira_excluir, name='carteira_excluir'),
]
