from django.urls import path

from . import views

app_name = 'bugs'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('relatar/', views.relatar, name='relatar'),
    path('configurar/', views.configurar, name='configurar'),
    path('<int:pk>/', views.detalhe, name='detalhe'),
    path('<int:pk>/estado/', views.estado, name='estado'),
    path('<int:pk>/aprovar/', views.aprovar, name='aprovar'),
    path('<int:pk>/recusar/', views.recusar, name='recusar'),
    path('<int:pk>/reanalisar/', views.reanalisar, name='reanalisar'),
]
