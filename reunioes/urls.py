from django.urls import path

from . import views

app_name = 'reunioes'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('nova/', views.nova, name='nova'),
    path('<int:reuniao_id>/', views.detalhe, name='detalhe'),
    path('<int:reuniao_id>/editar/', views.nova, name='editar'),
    path('<int:reuniao_id>/sala/', views.sala, name='sala'),
    path('<int:reuniao_id>/encerrar/', views.encerrar, name='encerrar'),
    path('<int:reuniao_id>/cancelar/', views.cancelar, name='cancelar'),
    path('<int:reuniao_id>/ata/', views.registrar_ata, name='registrar_ata'),
    path('configuracao/', views.configuracao, name='configuracao'),
]
