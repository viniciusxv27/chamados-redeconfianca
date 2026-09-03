from django.urls import path

from . import views

app_name = 'cursos'

urlpatterns = [
    path('', views.meus_cursos, name='meus_cursos'),
    path('<int:curso_id>/comprovante/', views.enviar_comprovante, name='enviar_comprovante'),
    path('bloqueado/', views.bloqueado, name='bloqueado'),

    path('gestao/', views.gestao, name='gestao'),
    path('gestao/exportar/', views.exportar, name='exportar'),
    path('gestao/novo/', views.curso_form, name='curso_novo'),
    path('gestao/<int:curso_id>/editar/', views.curso_form, name='curso_editar'),
    path('gestao/<int:curso_id>/capacitacao/', views.capacitacao, name='capacitacao'),
    path('gestao/comprovante/<int:comprovante_id>/revisar/', views.revisar_comprovante,
         name='revisar_comprovante'),
    path('gestao/aprovar-lote/', views.aprovar_lote, name='aprovar_lote'),

    path('configuracao/', views.configuracao, name='configuracao'),
]
