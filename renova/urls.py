from django.urls import path

from . import views

app_name = 'renova'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('nova/', views.nova, name='nova'),
    path('tabela/', views.tabela, name='tabela'),
    path('passo-a-passo/', views.passo_a_passo, name='passo_a_passo'),
    path('configuracao/', views.configurar, name='configuracao'),
    path('<int:pk>/', views.detalhe, name='detalhe'),
    path('<int:pk>/etiqueta/', views.etiqueta, name='etiqueta'),
    path('<int:pk>/recebimento/', views.recebimento, name='recebimento'),
    path('<int:pk>/abrir-chamado/', views.abrir_chamado_de_novo, name='abrir_chamado'),
]
