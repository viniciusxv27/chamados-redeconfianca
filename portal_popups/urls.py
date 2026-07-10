from django.urls import path

from . import views

app_name = 'portal_popups'

urlpatterns = [
    path('', views.popup_list, name='list'),
    path('novo/', views.popup_create, name='create'),
    path('<int:pk>/editar/', views.popup_edit, name='edit'),
    path('<int:pk>/alternar/', views.popup_toggle, name='toggle'),
    path('<int:pk>/excluir/', views.popup_delete, name='delete'),
    path('<int:pk>/concluir/', views.complete_popup, name='complete'),
]
