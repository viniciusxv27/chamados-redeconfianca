from django.urls import path

from . import views

app_name = 'rotina_adm'

urlpatterns = [
    path('', views.painel, name='painel'),
    path('checklist/', views.checklist_editar, name='checklist_editar'),
    path('checklist/<int:item_id>/marcar/', views.checklist_marcar, name='checklist_marcar'),
    path('acessos/', views.acessos, name='acessos'),
]
