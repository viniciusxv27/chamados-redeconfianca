from django.urls import path

from . import views

app_name = 'social'

urlpatterns = [
    path('', views.feed, name='feed'),
    path('mais/', views.mais, name='mais'),
    path('publicar/', views.publicar, name='publicar'),
    path('<int:post_id>/', views.post, name='post'),
    path('<int:post_id>/curtir/', views.curtir, name='curtir'),
    path('<int:post_id>/comentar/', views.comentar, name='comentar'),
    path('<int:post_id>/comentarios/', views.comentarios, name='comentarios'),
    path('<int:post_id>/apagar/', views.apagar, name='apagar'),
    path('comentario/<int:comentario_id>/apagar/', views.apagar_comentario, name='apagar_comentario'),
]
