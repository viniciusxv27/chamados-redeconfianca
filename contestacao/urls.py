from django.urls import path
from . import views

app_name = 'contestacao'

urlpatterns = [
    path('', views.exclusion_list, name='exclusion_list'),
    path('sincronizar/', views.sync_exclusions, name='sync_exclusions'),
    path('contestar/<int:exclusion_id>/', views.create_contestation, name='create_contestation'),
    path('minhas/', views.my_contestations, name='my_contestations'),
    path('gerenciar/', views.manage_contestations, name='manage_contestations'),
    path('<int:pk>/', views.contestation_detail, name='contestation_detail'),
    path('<int:pk>/aprovar/', views.approve_contestation, name='approve_contestation'),
    path('<int:pk>/rejeitar/', views.reject_contestation, name='reject_contestation'),
    path('<int:pk>/confirmar/', views.confirm_contestation, name='confirm_contestation'),
    path('<int:pk>/negar/', views.deny_contestation, name='deny_contestation'),
    path('<int:pk>/marcar-pago/', views.mark_paid, name='mark_paid'),
]
