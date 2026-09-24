from django.urls import path

from . import views

app_name = 'auditoria_sap'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('painel/', views.painel, name='painel'),
    path('atualizar/', views.atualizar, name='atualizar'),
    path('linha/<int:linha_id>/', views.detalhe, name='detalhe'),
    path('linha/<int:linha_id>/marcar/', views.marcar, name='marcar'),
]
