from django.urls import path
from . import views

app_name = 'compliments'

urlpatterns = [
    path('', views.compliments_dashboard, name='dashboard'),
    path('create/', views.create_compliment, name='create'),
    path('my/', views.my_compliments, name='my_compliments'),
    path('detail/<int:compliment_id>/', views.compliment_detail, name='detail'),
    path('api/search-users/', views.api_search_users, name='api_search_users'),
]