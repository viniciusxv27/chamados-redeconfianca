from django.urls import path
from . import views

app_name = 'purchases'

urlpatterns = [
    # Compras
    path('', views.purchase_list, name='purchase_list'),
    path('nova/', views.purchase_create, name='purchase_create'),
    path('<int:pk>/', views.purchase_detail, name='purchase_detail'),
    path('<int:pk>/editar/', views.purchase_edit, name='purchase_edit'),
    path('<int:pk>/excluir/', views.purchase_delete, name='purchase_delete'),
    
    # Formas de Pagamento
    path('formas-pagamento/', views.payment_method_list, name='payment_method_list'),
    path('formas-pagamento/nova/', views.payment_method_create, name='payment_method_create'),
    path('formas-pagamento/<int:pk>/editar/', views.payment_method_edit, name='payment_method_edit'),
    path('formas-pagamento/<int:pk>/excluir/', views.payment_method_delete, name='payment_method_delete'),
]