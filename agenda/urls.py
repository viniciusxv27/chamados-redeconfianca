from django.urls import path
from . import views

app_name = 'agenda'

urlpatterns = [
    # Agenda principal (calendário)
    path('', views.calendar_view, name='calendar'),

    # API JSON para eventos (FullCalendar)
    path('api/events/', views.api_events, name='api_events'),
    path('api/events/create/', views.api_event_create, name='api_event_create'),
    path('api/events/<int:pk>/update/', views.api_event_update, name='api_event_update'),
    path('api/events/<int:pk>/delete/', views.api_event_delete, name='api_event_delete'),
    path('api/events/<int:pk>/', views.api_event_detail, name='api_event_detail'),

    # Horários disponíveis de outro usuário
    path('disponibilidade/<int:user_id>/', views.user_availability, name='user_availability'),

    # Solicitar reunião
    path('solicitar/<int:user_id>/', views.request_meeting, name='request_meeting'),

    # Minhas solicitações (enviadas e recebidas)
    path('solicitacoes/', views.meeting_requests_list, name='meeting_requests'),
    path('solicitacoes/<int:pk>/aceitar/', views.meeting_request_accept, name='meeting_request_accept'),
    path('solicitacoes/<int:pk>/recusar/', views.meeting_request_reject, name='meeting_request_reject'),
    path('solicitacoes/<int:pk>/cancelar/', views.meeting_request_cancel, name='meeting_request_cancel'),

    # Superadmin: ver agenda de qualquer um
    path('usuario/<int:user_id>/', views.view_user_calendar, name='view_user_calendar'),
]
