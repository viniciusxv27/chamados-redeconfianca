from django.urls import path
from . import views

app_name = 'feedback'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('pendentes/', views.my_pending, name='pending'),
    path('historico/<int:user_id>/', views.user_history, name='user_history'),
    path('novo/', views.create_feedback, name='create'),
    path('novo/<int:assignment_id>/', views.create_feedback, name='create_from_assignment'),
    path('<int:feedback_id>/', views.feedback_detail, name='detail'),
    path('<int:feedback_id>/regenerar-ia/', views.regenerate_ai_summary, name='regenerate_ai'),

    # Superadmin
    path('gerenciar/', views.manage_all, name='manage'),
    path('atribuir/', views.assign_view, name='assign'),
    path('atribuicoes/<int:assignment_id>/excluir/', views.delete_assignment, name='delete_assignment'),

    # APIs
    path('api/usuarios/', views.api_search_users, name='api_search_users'),
]
