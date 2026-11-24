from django.urls import path

from . import views

app_name = 'rotina_adm'

urlpatterns = [
    path('', views.painel, name='painel'),
    path('checklist/', views.checklist_editar, name='checklist_editar'),
    path('checklist/<int:item_id>/marcar/', views.checklist_marcar, name='checklist_marcar'),
    path('ranking/', views.ranking_analistas, name='ranking'),
    path('estoque/registrar/', views.estoque_registrar, name='estoque_registrar'),
    path('acessos/', views.acessos, name='acessos'),
]
