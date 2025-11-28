from django.urls import path
from . import views

app_name = 'betting'

urlpatterns = [
    # Páginas públicas (para usuários logados)
    path('', views.betting_home, name='home'),
    path('campeonato/<int:championship_id>/', views.championship_detail, name='championship_detail'),
    path('partida/<int:match_id>/', views.match_detail, name='match_detail'),
    path('partida/<int:match_id>/apostar/', views.place_bet, name='place_bet'),
    path('minhas-apostas/', views.my_bets, name='my_bets'),
    
    # Apostas de campeão e artilheiro
    path('campeonato/<int:championship_id>/apostar-campeao/', views.place_champion_bet, name='place_champion_bet'),
    path('partida/<int:match_id>/apostar-artilheiro/', views.place_scorer_bet, name='place_scorer_bet'),
    
    # Admin
    path('admin/', views.admin_dashboard, name='admin_dashboard'),
    path('admin/campeonato/criar/', views.admin_create_championship, name='admin_create_championship'),
    path('admin/campeonato/<int:championship_id>/editar/', views.admin_edit_championship, name='admin_edit_championship'),
    path('admin/campeonato/<int:championship_id>/deletar/', views.admin_delete_championship, name='admin_delete_championship'),
    path('admin/campeonato/<int:championship_id>/partidas/', views.admin_manage_matches, name='admin_manage_matches'),
    path('admin/campeonato/<int:championship_id>/odds-campeao/', views.admin_champion_odds, name='admin_champion_odds'),
    path('admin/campeonato/<int:championship_id>/finalizar/', views.admin_finalize_championship, name='admin_finalize_championship'),
    path('admin/campeonato/<int:championship_id>/partida/criar/', views.admin_create_match, name='admin_create_match'),
    path('admin/partida/<int:match_id>/editar/', views.admin_edit_match, name='admin_edit_match'),
    path('admin/partida/<int:match_id>/placar/', views.admin_update_score, name='admin_update_score'),
    path('admin/partida/<int:match_id>/finalizar/', views.admin_finalize_match, name='admin_finalize_match'),
    path('admin/partida/<int:match_id>/ao-vivo/', views.admin_set_match_live, name='admin_set_match_live'),
    path('admin/partida/<int:match_id>/odds-artilheiro/', views.admin_scorer_odds, name='admin_scorer_odds'),
    path('admin/partida/<int:match_id>/artilheiros/', views.admin_update_scorers, name='admin_update_scorers'),
    
    # Aprovação de lucros
    path('admin/aprovacoes/', views.admin_win_approvals, name='admin_win_approvals'),
    path('admin/aprovacoes/<int:approval_id>/aprovar/', views.admin_approve_win, name='admin_approve_win'),
    path('admin/aprovacoes/<int:approval_id>/rejeitar/', views.admin_reject_win, name='admin_reject_win'),
    path('admin/aprovacoes/aprovar-todos/', views.admin_approve_all_wins, name='admin_approve_all_wins'),
    
    # API
    path('api/partida/<int:match_id>/odds/', views.api_get_odds, name='api_get_odds'),
    path('api/saldo/', views.api_get_user_balance, name='api_get_user_balance'),
]
