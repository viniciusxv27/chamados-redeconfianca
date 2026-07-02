from django.urls import path
from . import views

app_name = 'feedback'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('pesquisa-clima/', views.climate_survey, name='climate_survey'),
    path('pesquisa-clima/progresso/', views.climate_survey_progress, name='climate_survey_progress'),
    path('pesquisa-clima/relatorio/', views.climate_survey_report, name='climate_survey_report'),
    path('pesquisa-clima/acessos/', views.survey_access, name='survey_access'),

    # Entrevista de Desligamento
    path('entrevista-desligamento/', views.exit_interview, name='exit_interview'),
    path('entrevista-desligamento/acessos/', views.exit_interview_access, name='exit_interview_access'),
    path('entrevista-desligamento/relatorio/', views.exit_interview_report, name='exit_interview_report'),
    path('entrevista-desligamento/<int:user_id>/desligar/', views.exit_interview_dismiss, name='exit_interview_dismiss'),
    path('entrevista-desligamento/<int:user_id>/zerar/', views.exit_interview_reset, name='exit_interview_reset'),

    path('pendentes/', views.my_pending, name='pending'),
    path('historico/<int:user_id>/', views.user_history, name='user_history'),
    path('novo/', views.create_feedback, name='create'),
    path('novo/<int:assignment_id>/', views.create_feedback, name='create_from_assignment'),
    path('<int:feedback_id>/', views.feedback_detail, name='detail'),
    path('<int:feedback_id>/regenerar-ia/', views.regenerate_ai_summary, name='regenerate_ai'),

    # Superadmin
    path('gerenciar/', views.manage_all, name='manage'),
    path('relatorios/', views.reports, name='reports'),
    path('atribuir/', views.assign_view, name='assign'),
    path('atribuicoes/<int:assignment_id>/excluir/', views.delete_assignment, name='delete_assignment'),

    # APIs
    path('api/usuarios/', views.api_search_users, name='api_search_users'),
    path('api/reminders/', views.api_reminders, name='api_reminders'),
    path('api/reminders/dismiss/', views.api_dismiss_reminder, name='api_dismiss_reminder'),
]
