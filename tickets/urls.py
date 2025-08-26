from django.urls import path
from . import views

urlpatterns = [
    path('', views.tickets_list_view, name='tickets_list'),
    path('create/', views.ticket_create_view, name='ticket_create'),
    path('<int:ticket_id>/', views.ticket_detail_view, name='ticket_detail'),
    path('api/categories-by-sector/', views.get_categories_by_sector, name='categories_by_sector'),
]
