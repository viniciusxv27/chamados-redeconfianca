from django.urls import path

from . import views

app_name = 'validad1'

urlpatterns = [
    path('', views.lista, name='lista'),
    path('sync/', views.sync_now, name='sync_now'),
    path('relatorio/', views.relatorio, name='relatorio'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/sinalizar/', views.sinalizar, name='sinalizar'),
    path('<int:pk>/de-acordo/', views.de_acordo, name='de_acordo'),
    path('<int:pk>/contestar/', views.contestar, name='contestar'),
    path('contestacao/<int:pk>/', views.contestacao_detail, name='contestacao_detail'),
    path('contestacao/<int:pk>/post/', views.contestacao_post, name='contestacao_post'),
    path('contestacao/<int:pk>/resolver/', views.contestacao_resolver, name='contestacao_resolver'),
]
