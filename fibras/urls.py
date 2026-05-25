from django.urls import path

from . import views

app_name = 'fibras'

urlpatterns = [
    path('', views.kanban, name='kanban'),
    path('sync/', views.sync_now, name='sync_now'),
    path('relatorio/', views.relatorio, name='relatorio'),
    path('<int:pk>/', views.detail, name='detail'),
    path('<int:pk>/status/', views.change_status_view, name='change_status'),
    path('<int:pk>/incidente/', views.abrir_incidente, name='abrir_incidente'),
    path('<int:pk>/chat/', views.chat_view, name='chat'),
    path('<int:pk>/chat/post/', views.chat_post, name='chat_post'),
]
