from django.urls import path

from . import views

app_name = 'facilitador'

urlpatterns = [
    path('', views.inicio, name='inicio'),
    path('novo/', views.novo, name='novo'),
    path('<int:pk>/', views.detalhe, name='detalhe'),
    path('<int:pk>/baixar/', views.baixar, name='baixar'),
    path('<int:pk>/ir/', views.ir, name='ir'),
    path('qr/<str:token>/', views.baixar_qr, name='baixar_qr'),  # sem login: link assinado
    path('<int:pk>/editar/', views.editar, name='editar'),
    path('<int:pk>/visibilidade/', views.alternar, name='alternar'),
    path('<int:pk>/excluir/', views.excluir, name='excluir'),
]
