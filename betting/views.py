from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db import transaction
from django.db.models import Sum, Count, Q
from decimal import Decimal
import json

from .models import (
    Championship, ChampionOdds, ChampionBet,
    Match, MatchScorerOdds, ScorerBet,
    Bet, BetTransaction, BetWinApproval
)
from users.models import Sector, User


def has_betting_admin_permission(user):
    """Verifica se o usuário tem permissão para administrar apostas"""
    if user.is_superuser:
        return True
    if hasattr(user, 'hierarchy') and user.hierarchy:
        return user.hierarchy in ['SUPERADMIN', 'ADMIN']
    return False


@login_required
def betting_home(request):
    """Página principal do Confiança BET"""
    # Buscar campeonatos ativos
    championships = Championship.objects.filter(
        status='active'
    ).prefetch_related('participating_sectors', 'matches')
    
    # Buscar próximas partidas
    upcoming_matches = Match.objects.filter(
        championship__status='active',
        status='scheduled',
        match_date__gte=timezone.now()
    ).select_related(
        'championship', 'home_team', 'away_team'
    ).order_by('match_date')[:10]
    
    # Partidas ao vivo
    live_matches = Match.objects.filter(
        championship__status='active',
        status='live'
    ).select_related(
        'championship', 'home_team', 'away_team'
    )
    
    # Últimos resultados
    recent_results = Match.objects.filter(
        championship__status='active',
        status='finished'
    ).select_related(
        'championship', 'home_team', 'away_team'
    ).order_by('-match_date')[:10]
    
    # Minhas apostas recentes
    my_bets = Bet.objects.filter(
        user=request.user
    ).select_related(
        'match__home_team', 'match__away_team', 'match__championship'
    ).order_by('-created_at')[:10]
    
    # Estatísticas do usuário
    user_stats = Bet.objects.filter(user=request.user).aggregate(
        total_bets=Count('id'),
        total_won=Count('id', filter=Q(status='won')),
        total_lost=Count('id', filter=Q(status='lost')),
        total_wagered=Sum('amount'),
        total_winnings=Sum('winnings', filter=Q(status='won'))
    )
    
    # Verificar se é admin
    is_admin = has_betting_admin_permission(request.user)
    
    context = {
        'championships': championships,
        'upcoming_matches': upcoming_matches,
        'live_matches': live_matches,
        'recent_results': recent_results,
        'my_bets': my_bets,
        'user_stats': user_stats,
        'is_admin': is_admin,
        'user_balance': request.user.balance_cs,
    }
    
    return render(request, 'betting/home.html', context)


@login_required
def championship_detail(request, championship_id):
    """Detalhes de um campeonato"""
    championship = get_object_or_404(
        Championship.objects.prefetch_related('participating_sectors', 'matches'),
        id=championship_id
    )
    
    # Partidas agrupadas por status
    scheduled_matches = championship.matches.filter(status='scheduled').order_by('match_date')
    live_matches = championship.matches.filter(status='live')
    finished_matches = championship.matches.filter(status='finished').order_by('-match_date')
    
    # Odds de campeão
    champion_odds = championship.champion_odds.filter(is_active=True).select_related('sector')
    
    # Verificar apostas do usuário no campeão
    user_champion_bets = ChampionBet.objects.filter(
        user=request.user,
        championship=championship
    ).select_related('sector')
    
    is_admin = has_betting_admin_permission(request.user)
    
    context = {
        'championship': championship,
        'scheduled_matches': scheduled_matches,
        'live_matches': live_matches,
        'finished_matches': finished_matches,
        'champion_odds': champion_odds,
        'user_champion_bets': user_champion_bets,
        'is_admin': is_admin,
        'user_balance': request.user.balance_cs,
    }
    
    return render(request, 'betting/championship_detail.html', context)


@login_required
def match_detail(request, match_id):
    """Detalhes de uma partida"""
    match = get_object_or_404(
        Match.objects.select_related('championship', 'home_team', 'away_team'),
        id=match_id
    )
    
    # Verificar se usuário já apostou nesta partida
    user_bet = Bet.objects.filter(user=request.user, match=match).first()
    
    # Estatísticas de apostas desta partida
    bet_stats = match.bets.aggregate(
        total_home=Sum('amount', filter=Q(bet_type='home')),
        total_draw=Sum('amount', filter=Q(bet_type='draw')),
        total_away=Sum('amount', filter=Q(bet_type='away')),
        count_home=Count('id', filter=Q(bet_type='home')),
        count_draw=Count('id', filter=Q(bet_type='draw')),
        count_away=Count('id', filter=Q(bet_type='away')),
    )
    
    # Odds de artilheiro
    scorer_odds = match.scorer_odds.filter(is_active=True).select_related('player', 'player__sector')
    
    # Aposta de artilheiro do usuário nesta partida
    user_scorer_bet = ScorerBet.objects.filter(
        user=request.user,
        match=match
    ).select_related('player').first()
    
    is_admin = has_betting_admin_permission(request.user)
    
    context = {
        'match': match,
        'user_bet': user_bet,
        'bet_stats': bet_stats,
        'scorer_odds': scorer_odds,
        'user_scorer_bet': user_scorer_bet,
        'is_admin': is_admin,
        'user_balance': request.user.balance_cs,
        'can_bet': match.status in ['scheduled', 'live'] and not user_bet,
    }
    
    return render(request, 'betting/match_detail.html', context)


@login_required
def place_bet(request, match_id):
    """Realizar uma aposta"""
    if request.method != 'POST':
        return redirect('betting:match_detail', match_id=match_id)
    
    match = get_object_or_404(Match, id=match_id)
    
    # Validações
    if match.status not in ['scheduled', 'live']:
        messages.error(request, '❌ Esta partida não está mais disponível para apostas.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Verificar se já apostou
    if Bet.objects.filter(user=request.user, match=match).exists():
        messages.error(request, '❌ Você já fez uma aposta nesta partida.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Pegar dados do formulário
    bet_type = request.POST.get('bet_type')
    amount_str = request.POST.get('amount', '0').replace(',', '.')
    
    try:
        amount = Decimal(amount_str)
    except:
        messages.error(request, '❌ Valor inválido.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Validar tipo de aposta
    if bet_type not in ['home', 'draw', 'away']:
        messages.error(request, '❌ Tipo de aposta inválido.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Validar valor mínimo
    if amount < Decimal('1.00'):
        messages.error(request, '❌ O valor mínimo de aposta é 1 C$.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Validar saldo
    if request.user.balance_cs < amount:
        messages.error(request, f'❌ Saldo insuficiente. Você tem {request.user.balance_cs} C$.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Determinar odds
    if bet_type == 'home':
        odds = match.odds_home
    elif bet_type == 'draw':
        odds = match.odds_draw
    else:
        odds = match.odds_away
    
    # Criar aposta e transação
    with transaction.atomic():
        # Debitar saldo
        balance_before = request.user.balance_cs
        request.user.balance_cs -= amount
        request.user.save()
        
        # Criar aposta
        bet = Bet.objects.create(
            user=request.user,
            match=match,
            bet_type=bet_type,
            amount=amount,
            odds_at_bet=odds,
            potential_win=amount * odds
        )
        
        # Registrar transação
        BetTransaction.objects.create(
            user=request.user,
            bet=bet,
            transaction_type='bet',
            amount=-amount,
            balance_before=balance_before,
            balance_after=request.user.balance_cs,
            description=f'Aposta em {match.home_team} x {match.away_team}'
        )
    
    messages.success(
        request,
        f'✅ Aposta realizada com sucesso! Você apostou {amount} C$ com odds de {odds}. Ganho potencial: {bet.potential_win} C$'
    )
    
    return redirect('betting:match_detail', match_id=match_id)


@login_required
def my_bets(request):
    """Minhas apostas"""
    status_filter = request.GET.get('status', '')
    bet_type_filter = request.GET.get('type', '')  # 'match', 'champion', 'scorer'
    
    # Apostas de partida
    match_bets = Bet.objects.filter(
        user=request.user
    ).select_related(
        'match__home_team', 'match__away_team', 'match__championship'
    ).order_by('-created_at')
    
    # Apostas de campeão
    champion_bets = ChampionBet.objects.filter(
        user=request.user
    ).select_related(
        'championship', 'sector'
    ).order_by('-created_at')
    
    # Apostas de artilheiro
    scorer_bets = ScorerBet.objects.filter(
        user=request.user
    ).select_related(
        'match__home_team', 'match__away_team', 'match__championship', 'player'
    ).order_by('-created_at')
    
    if status_filter:
        match_bets = match_bets.filter(status=status_filter)
        champion_bets = champion_bets.filter(status=status_filter)
        scorer_bets = scorer_bets.filter(status=status_filter)
    
    # Estatísticas totais combinadas
    match_stats = Bet.objects.filter(user=request.user).aggregate(
        total_bets=Count('id'),
        total_won=Count('id', filter=Q(status='won')),
        total_lost=Count('id', filter=Q(status='lost')),
        total_pending=Count('id', filter=Q(status='pending')),
        total_wagered=Sum('amount'),
        total_winnings=Sum('winnings', filter=Q(status='won'))
    )
    
    champion_stats = ChampionBet.objects.filter(user=request.user).aggregate(
        total_bets=Count('id'),
        total_won=Count('id', filter=Q(status='won')),
        total_lost=Count('id', filter=Q(status='lost')),
        total_pending=Count('id', filter=Q(status='pending')),
        total_wagered=Sum('amount'),
        total_winnings=Sum('winnings', filter=Q(status='won'))
    )
    
    scorer_stats = ScorerBet.objects.filter(user=request.user).aggregate(
        total_bets=Count('id'),
        total_won=Count('id', filter=Q(status='won')),
        total_lost=Count('id', filter=Q(status='lost')),
        total_pending=Count('id', filter=Q(status='pending')),
        total_wagered=Sum('amount'),
        total_winnings=Sum('winnings', filter=Q(status='won'))
    )
    
    # Combinar estatísticas
    stats = {
        'total_bets': (match_stats['total_bets'] or 0) + (champion_stats['total_bets'] or 0) + (scorer_stats['total_bets'] or 0),
        'total_won': (match_stats['total_won'] or 0) + (champion_stats['total_won'] or 0) + (scorer_stats['total_won'] or 0),
        'total_lost': (match_stats['total_lost'] or 0) + (champion_stats['total_lost'] or 0) + (scorer_stats['total_lost'] or 0),
        'total_pending': (match_stats['total_pending'] or 0) + (champion_stats['total_pending'] or 0) + (scorer_stats['total_pending'] or 0),
        'total_wagered': (match_stats['total_wagered'] or 0) + (champion_stats['total_wagered'] or 0) + (scorer_stats['total_wagered'] or 0),
        'total_winnings': (match_stats['total_winnings'] or 0) + (champion_stats['total_winnings'] or 0) + (scorer_stats['total_winnings'] or 0),
    }
    
    context = {
        'match_bets': match_bets,
        'champion_bets': champion_bets,
        'scorer_bets': scorer_bets,
        'stats': stats,
        'status_filter': status_filter,
        'bet_type_filter': bet_type_filter,
        'user_balance': request.user.balance_cs,
    }
    
    return render(request, 'betting/my_bets.html', context)


@login_required
def place_champion_bet(request, championship_id):
    """Apostar no campeão do campeonato"""
    if request.method != 'POST':
        return redirect('betting:championship_detail', championship_id=championship_id)
    
    championship = get_object_or_404(Championship, id=championship_id)
    
    # Validar se campeonato aceita apostas
    if championship.status != 'active':
        messages.error(request, '❌ Este campeonato não está aceitando apostas.')
        return redirect('betting:championship_detail', championship_id=championship_id)
    
    sector_id = request.POST.get('sector_id')
    amount_str = request.POST.get('amount', '0').replace(',', '.')
    
    try:
        amount = Decimal(amount_str)
    except:
        messages.error(request, '❌ Valor inválido.')
        return redirect('betting:championship_detail', championship_id=championship_id)
    
    # Validar valor mínimo
    if amount < Decimal('1.00'):
        messages.error(request, '❌ O valor mínimo de aposta é 1 C$.')
        return redirect('betting:championship_detail', championship_id=championship_id)
    
    # Validar saldo
    if request.user.balance_cs < amount:
        messages.error(request, f'❌ Saldo insuficiente. Você tem {request.user.balance_cs} C$.')
        return redirect('betting:championship_detail', championship_id=championship_id)
    
    # Buscar odds
    champion_odds = get_object_or_404(
        ChampionOdds,
        championship=championship,
        sector_id=sector_id,
        is_active=True
    )
    
    # Criar aposta
    with transaction.atomic():
        balance_before = request.user.balance_cs
        request.user.balance_cs -= amount
        request.user.save()
        
        bet = ChampionBet.objects.create(
            user=request.user,
            championship=championship,
            sector=champion_odds.sector,
            amount=amount,
            odds_at_bet=champion_odds.odds,
            potential_win=amount * champion_odds.odds
        )
        
        BetTransaction.objects.create(
            user=request.user,
            transaction_type='bet',
            amount=-amount,
            balance_before=balance_before,
            balance_after=request.user.balance_cs,
            description=f'Aposta campeão: {champion_odds.sector.name} ({championship.name})'
        )
    
    messages.success(
        request,
        f'✅ Aposta realizada! Você apostou {amount} C$ em {champion_odds.sector.name} campeão. '
        f'Odds: {champion_odds.odds}x | Ganho potencial: {bet.potential_win} C$'
    )
    
    return redirect('betting:championship_detail', championship_id=championship_id)


@login_required
def place_scorer_bet(request, match_id):
    """Apostar no artilheiro da partida"""
    if request.method != 'POST':
        return redirect('betting:match_detail', match_id=match_id)
    
    match = get_object_or_404(Match, id=match_id)
    
    # Validar se partida aceita apostas
    if match.status not in ['scheduled', 'live']:
        messages.error(request, '❌ Esta partida não está aceitando apostas.')
        return redirect('betting:match_detail', match_id=match_id)
    
    player_id = request.POST.get('player_id')
    amount_str = request.POST.get('amount', '0').replace(',', '.')
    
    try:
        amount = Decimal(amount_str)
    except:
        messages.error(request, '❌ Valor inválido.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Validar valor mínimo
    if amount < Decimal('1.00'):
        messages.error(request, '❌ O valor mínimo de aposta é 1 C$.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Validar saldo
    if request.user.balance_cs < amount:
        messages.error(request, f'❌ Saldo insuficiente. Você tem {request.user.balance_cs} C$.')
        return redirect('betting:match_detail', match_id=match_id)
    
    # Buscar odds do jogador
    scorer_odds = get_object_or_404(
        MatchScorerOdds,
        match=match,
        player_id=player_id,
        is_active=True
    )
    
    # Criar aposta
    with transaction.atomic():
        balance_before = request.user.balance_cs
        request.user.balance_cs -= amount
        request.user.save()
        
        bet = ScorerBet.objects.create(
            user=request.user,
            match=match,
            player=scorer_odds.player,
            amount=amount,
            odds_at_bet=scorer_odds.odds,
            potential_win=amount * scorer_odds.odds
        )
        
        BetTransaction.objects.create(
            user=request.user,
            transaction_type='bet',
            amount=-amount,
            balance_before=balance_before,
            balance_after=request.user.balance_cs,
            description=f'Aposta artilheiro: {scorer_odds.player.get_full_name()} marcar'
        )
    
    messages.success(
        request,
        f'✅ Aposta realizada! Você apostou {amount} C$ em {scorer_odds.player.get_full_name()} marcar. '
        f'Odds: {scorer_odds.odds}x | Ganho potencial: {bet.potential_win} C$'
    )
    
    return redirect('betting:match_detail', match_id=match_id)


# ======== ADMIN VIEWS ========

@login_required
def admin_dashboard(request):
    """Dashboard administrativo"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('betting:home')
    
    championships = Championship.objects.all().order_by('-created_at')
    
    # Estatísticas gerais
    stats = {
        'total_championships': championships.count(),
        'active_championships': championships.filter(status='active').count(),
        'total_matches': Match.objects.count(),
        'total_bets': Bet.objects.count(),
        'total_wagered': Bet.objects.aggregate(total=Sum('amount'))['total'] or 0,
        'total_paid': Bet.objects.filter(status='won').aggregate(total=Sum('winnings'))['total'] or 0,
    }
    
    # Contagem de aprovações pendentes
    pending_approvals = BetWinApproval.objects.filter(status='pending').count()
    
    context = {
        'championships': championships,
        'stats': stats,
        'pending_approvals': pending_approvals,
    }
    
    return render(request, 'betting/admin/dashboard.html', context)


@login_required
def admin_create_championship(request):
    """Criar campeonato"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('betting:home')
    
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        table_url = request.POST.get('table_url', '').strip()
        sector_ids = request.POST.getlist('sectors')
        status = request.POST.get('status', 'draft')
        banner = request.FILES.get('banner')
        
        if not name:
            messages.error(request, '❌ O nome do campeonato é obrigatório.')
            return redirect('betting:admin_create_championship')
        
        if not sector_ids:
            messages.error(request, '❌ Selecione pelo menos um setor participante.')
            return redirect('betting:admin_create_championship')
        
        championship = Championship.objects.create(
            name=name,
            description=description,
            table_url=table_url,
            status=status,
            banner=banner,
            created_by=request.user
        )
        
        # Adicionar setores
        sectors = Sector.objects.filter(id__in=sector_ids)
        championship.participating_sectors.set(sectors)
        
        messages.success(request, f'✅ Campeonato "{name}" criado com sucesso!')
        return redirect('betting:admin_dashboard')
    
    sectors = Sector.objects.all().order_by('name')
    
    context = {
        'sectors': sectors,
        'action': 'create',
    }
    
    return render(request, 'betting/admin/championship_form.html', context)


@login_required
def admin_edit_championship(request, championship_id):
    """Editar campeonato"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('betting:home')
    
    championship = get_object_or_404(Championship, id=championship_id)
    
    if request.method == 'POST':
        championship.name = request.POST.get('name', '').strip()
        championship.description = request.POST.get('description', '').strip()
        championship.table_url = request.POST.get('table_url', '').strip()
        championship.status = request.POST.get('status', 'draft')
        
        if request.FILES.get('banner'):
            championship.banner = request.FILES.get('banner')
        
        sector_ids = request.POST.getlist('sectors')
        
        if not championship.name:
            messages.error(request, '❌ O nome do campeonato é obrigatório.')
            return redirect('betting:admin_edit_championship', championship_id=championship_id)
        
        championship.save()
        
        # Atualizar setores
        sectors = Sector.objects.filter(id__in=sector_ids)
        championship.participating_sectors.set(sectors)
        
        messages.success(request, f'✅ Campeonato "{championship.name}" atualizado!')
        return redirect('betting:admin_dashboard')
    
    sectors = Sector.objects.all().order_by('name')
    
    context = {
        'championship': championship,
        'sectors': sectors,
        'action': 'edit',
    }
    
    return render(request, 'betting/admin/championship_form.html', context)


@login_required
def admin_delete_championship(request, championship_id):
    """Deletar campeonato"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    if request.method == 'POST':
        championship = get_object_or_404(Championship, id=championship_id)
        name = championship.name
        championship.delete()
        messages.success(request, f'🗑️ Campeonato "{name}" deletado!')
    
    return redirect('betting:admin_dashboard')


@login_required
def admin_create_match(request, championship_id):
    """Criar partida"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    championship = get_object_or_404(Championship, id=championship_id)
    
    if request.method == 'POST':
        home_team_id = request.POST.get('home_team')
        away_team_id = request.POST.get('away_team')
        match_date = request.POST.get('match_date')
        round_number = request.POST.get('round_number', 1)
        odds_home = request.POST.get('odds_home', '2.00')
        odds_draw = request.POST.get('odds_draw', '3.00')
        odds_away = request.POST.get('odds_away', '2.00')
        
        if not all([home_team_id, away_team_id, match_date]):
            messages.error(request, '❌ Preencha todos os campos obrigatórios.')
            return redirect('betting:admin_create_match', championship_id=championship_id)
        
        if home_team_id == away_team_id:
            messages.error(request, '❌ Os times devem ser diferentes.')
            return redirect('betting:admin_create_match', championship_id=championship_id)
        
        Match.objects.create(
            championship=championship,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            match_date=match_date,
            round_number=int(round_number),
            odds_home=Decimal(odds_home.replace(',', '.')),
            odds_draw=Decimal(odds_draw.replace(',', '.')),
            odds_away=Decimal(odds_away.replace(',', '.'))
        )
        
        messages.success(request, '✅ Partida criada com sucesso!')
        return redirect('betting:admin_manage_matches', championship_id=championship_id)
    
    context = {
        'championship': championship,
        'sectors': championship.participating_sectors.all(),
        'action': 'create',
    }
    
    return render(request, 'betting/admin/match_form.html', context)


@login_required
def admin_manage_matches(request, championship_id):
    """Gerenciar partidas de um campeonato"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    championship = get_object_or_404(Championship, id=championship_id)
    matches = championship.matches.all().order_by('round_number', 'match_date')
    
    context = {
        'championship': championship,
        'matches': matches,
    }
    
    return render(request, 'betting/admin/manage_matches.html', context)


@login_required
def admin_edit_match(request, match_id):
    """Editar partida"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    match = get_object_or_404(Match, id=match_id)
    
    if request.method == 'POST':
        match.home_team_id = request.POST.get('home_team')
        match.away_team_id = request.POST.get('away_team')
        match.match_date = request.POST.get('match_date')
        match.round_number = int(request.POST.get('round_number', 1))
        match.odds_home = Decimal(request.POST.get('odds_home', '2.00').replace(',', '.'))
        match.odds_draw = Decimal(request.POST.get('odds_draw', '3.00').replace(',', '.'))
        match.odds_away = Decimal(request.POST.get('odds_away', '2.00').replace(',', '.'))
        match.status = request.POST.get('status', 'scheduled')
        
        match.save()
        
        messages.success(request, '✅ Partida atualizada!')
        return redirect('betting:admin_manage_matches', championship_id=match.championship_id)
    
    context = {
        'match': match,
        'championship': match.championship,
        'sectors': match.championship.participating_sectors.all(),
        'action': 'edit',
    }
    
    return render(request, 'betting/admin/match_form.html', context)


@login_required
def admin_update_score(request, match_id):
    """Atualizar placar de uma partida"""
    if not has_betting_admin_permission(request.user):
        return JsonResponse({'error': 'Sem permissão'}, status=403)
    
    if request.method != 'POST':
        return JsonResponse({'error': 'Método inválido'}, status=405)
    
    match = get_object_or_404(Match, id=match_id)
    
    try:
        data = json.loads(request.body)
        home_score = int(data.get('home_score', match.home_score))
        away_score = int(data.get('away_score', match.away_score))
        
        match.home_score = home_score
        match.away_score = away_score
        
        # Se estiver ao vivo, atualizar odds
        if match.status == 'live':
            match.update_live_odds()
        
        match.save()
        
        return JsonResponse({
            'success': True,
            'home_score': match.home_score,
            'away_score': match.away_score,
            'odds_home': str(match.odds_home),
            'odds_draw': str(match.odds_draw),
            'odds_away': str(match.odds_away),
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def admin_finalize_match(request, match_id):
    """Finalizar partida e processar apostas"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    if request.method != 'POST':
        return redirect('betting:admin_dashboard')
    
    match = get_object_or_404(Match, id=match_id)
    
    # Finalizar e processar apostas
    match.finalize_match()
    
    # Contar apostas processadas
    won_count = match.bets.filter(status='won').count()
    lost_count = match.bets.filter(status='lost').count()
    
    messages.success(
        request,
        f'✅ Partida finalizada! Resultado: {match.home_team} {match.home_score} x {match.away_score} {match.away_team}. '
        f'{won_count} apostas ganhas, {lost_count} apostas perdidas.'
    )
    
    return redirect('betting:admin_manage_matches', championship_id=match.championship_id)


@login_required
def admin_set_match_live(request, match_id):
    """Definir partida como ao vivo"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    match = get_object_or_404(Match, id=match_id)
    match.status = 'live'
    match.save()
    
    messages.success(request, f'🔴 Partida {match.home_team} x {match.away_team} agora está AO VIVO!')
    
    return redirect('betting:admin_manage_matches', championship_id=match.championship_id)


@login_required
def admin_champion_odds(request, championship_id):
    """Gerenciar odds de campeão do campeonato"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    championship = get_object_or_404(Championship, id=championship_id)
    
    if request.method == 'POST':
        # Atualizar odds de cada setor
        for sector in championship.participating_sectors.all():
            odds_value = request.POST.get(f'odds_{sector.id}', '').replace(',', '.')
            is_active = request.POST.get(f'active_{sector.id}') == 'on'
            
            if odds_value:
                try:
                    odds = Decimal(odds_value)
                    ChampionOdds.objects.update_or_create(
                        championship=championship,
                        sector=sector,
                        defaults={
                            'odds': odds,
                            'is_active': is_active
                        }
                    )
                except:
                    pass
        
        messages.success(request, '✅ Odds de campeão atualizadas!')
        return redirect('betting:admin_champion_odds', championship_id=championship_id)
    
    # Preparar dados com odds existentes
    sectors_with_odds = []
    for sector in championship.participating_sectors.all():
        odds_obj = ChampionOdds.objects.filter(
            championship=championship,
            sector=sector
        ).first()
        
        sectors_with_odds.append({
            'sector': sector,
            'odds': odds_obj.odds if odds_obj else Decimal('5.00'),
            'is_active': odds_obj.is_active if odds_obj else True,
            'bet_count': ChampionBet.objects.filter(
                championship=championship,
                sector=sector,
                status='pending'
            ).count()
        })
    
    context = {
        'championship': championship,
        'sectors_with_odds': sectors_with_odds,
    }
    
    return render(request, 'betting/admin/champion_odds.html', context)


@login_required
def admin_scorer_odds(request, match_id):
    """Gerenciar odds de artilheiro de uma partida"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    match = get_object_or_404(Match, id=match_id)
    
    if request.method == 'POST':
        # Atualizar odds de cada jogador
        player_ids = request.POST.getlist('player_ids')
        
        for player_id in player_ids:
            odds_value = request.POST.get(f'odds_{player_id}', '').replace(',', '.')
            goals_value = request.POST.get(f'goals_{player_id}', '0')
            is_active = request.POST.get(f'active_{player_id}') == 'on'
            
            if odds_value:
                try:
                    odds = Decimal(odds_value)
                    goals = int(goals_value) if goals_value else 0
                    MatchScorerOdds.objects.update_or_create(
                        match=match,
                        player_id=player_id,
                        defaults={
                            'odds': odds,
                            'is_active': is_active,
                            'goals': goals,
                            'scored': goals > 0
                        }
                    )
                except:
                    pass
        
        messages.success(request, '✅ Odds e gols de artilheiros atualizados!')
        return redirect('betting:admin_scorer_odds', match_id=match_id)
    
    # Buscar jogadores dos dois times
    home_players = User.objects.filter(sector=match.home_team).order_by('first_name')
    away_players = User.objects.filter(sector=match.away_team).order_by('first_name')
    
    # Preparar dados com odds existentes
    def get_players_with_odds(players):
        result = []
        for player in players:
            odds_obj = MatchScorerOdds.objects.filter(
                match=match,
                player=player
            ).first()
            
            result.append({
                'player': player,
                'odds': odds_obj.odds if odds_obj else Decimal('3.00'),
                'is_active': odds_obj.is_active if odds_obj else True,
                'scored': odds_obj.scored if odds_obj else False,
                'goals': odds_obj.goals if odds_obj else 0,
                'bet_count': ScorerBet.objects.filter(
                    match=match,
                    player=player,
                    status='pending'
                ).count()
            })
        return result
    
    context = {
        'match': match,
        'home_players_with_odds': get_players_with_odds(home_players),
        'away_players_with_odds': get_players_with_odds(away_players),
    }
    
    return render(request, 'betting/admin/scorer_odds.html', context)


@login_required
def admin_update_scorers(request, match_id):
    """Atualizar artilheiros (quem marcou gol) de uma partida"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    match = get_object_or_404(Match, id=match_id)
    
    if request.method == 'POST':
        # Atualizar gols de cada jogador
        for scorer_odds in match.scorer_odds.all():
            goals = request.POST.get(f'goals_{scorer_odds.player_id}', '0')
            try:
                goals = int(goals)
                scorer_odds.goals = goals
                scorer_odds.scored = goals > 0
                scorer_odds.save()
            except:
                pass
        
        messages.success(request, '✅ Artilheiros atualizados!')
        return redirect('betting:admin_scorer_odds', match_id=match_id)
    
    return redirect('betting:admin_scorer_odds', match_id=match_id)


@login_required
def admin_finalize_championship(request, championship_id):
    """Finalizar campeonato e definir campeão"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    championship = get_object_or_404(Championship, id=championship_id)
    
    if request.method == 'POST':
        champion_sector_id = request.POST.get('champion_sector')
        
        if not champion_sector_id:
            messages.error(request, '❌ Selecione o campeão.')
            return redirect('betting:admin_edit_championship', championship_id=championship_id)
        
        champion = get_object_or_404(Sector, id=champion_sector_id)
        
        # Finalizar campeonato
        championship.finalize_championship(champion)
        
        # Contar apostas processadas
        won_count = championship.champion_bets.filter(status='won').count()
        lost_count = championship.champion_bets.filter(status='lost').count()
        
        messages.success(
            request,
            f'🏆 Campeonato finalizado! Campeão: {champion.name}. '
            f'{won_count} apostas ganhas, {lost_count} apostas perdidas.'
        )
        
        return redirect('betting:admin_dashboard')
    
    context = {
        'championship': championship,
        'sectors': championship.participating_sectors.all(),
    }
    
    return render(request, 'betting/admin/finalize_championship.html', context)


# ======== API VIEWS ========

@login_required
def api_get_odds(request, match_id):
    """API para obter odds atualizadas"""
    match = get_object_or_404(Match, id=match_id)
    
    return JsonResponse({
        'odds_home': str(match.odds_home),
        'odds_draw': str(match.odds_draw),
        'odds_away': str(match.odds_away),
        'home_score': match.home_score,
        'away_score': match.away_score,
        'status': match.status,
    })


@login_required
def api_get_user_balance(request):
    """API para obter saldo do usuário"""
    return JsonResponse({
        'balance': str(request.user.balance_cs)
    })


# ======== APROVAÇÃO DE LUCROS ========

@login_required
def admin_win_approvals(request):
    """Lista de aprovações de lucros pendentes"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('betting:home')
    
    status_filter = request.GET.get('status', 'pending')
    
    approvals = BetWinApproval.objects.select_related('user', 'reviewed_by')
    
    if status_filter:
        approvals = approvals.filter(status=status_filter)
    
    approvals = approvals.order_by('-created_at')
    
    # Estatísticas
    stats = {
        'pending': BetWinApproval.objects.filter(status='pending').count(),
        'pending_total': BetWinApproval.objects.filter(status='pending').aggregate(
            total=Sum('profit_amount'))['total'] or 0,
        'approved': BetWinApproval.objects.filter(status='approved').count(),
        'rejected': BetWinApproval.objects.filter(status='rejected').count(),
    }
    
    context = {
        'approvals': approvals,
        'stats': stats,
        'status_filter': status_filter,
    }
    
    return render(request, 'betting/admin/win_approvals.html', context)


@login_required
def admin_approve_win(request, approval_id):
    """Aprovar lucro de aposta"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    if request.method != 'POST':
        return redirect('betting:admin_win_approvals')
    
    approval = get_object_or_404(BetWinApproval, id=approval_id, status='pending')
    
    approval.approve(request.user)
    
    messages.success(
        request,
        f'✅ Lucro de {approval.profit_amount} C$ aprovado para {approval.user.first_name}!'
    )
    
    return redirect('betting:admin_win_approvals')


@login_required
def admin_reject_win(request, approval_id):
    """Rejeitar lucro de aposta"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    if request.method != 'POST':
        return redirect('betting:admin_win_approvals')
    
    approval = get_object_or_404(BetWinApproval, id=approval_id, status='pending')
    
    reason = request.POST.get('reason', '')
    approval.reject(request.user, reason)
    
    messages.warning(
        request,
        f'❌ Lucro de {approval.profit_amount} C$ rejeitado para {approval.user.first_name}.'
    )
    
    return redirect('betting:admin_win_approvals')


@login_required
def admin_approve_all_wins(request):
    """Aprovar todos os lucros pendentes"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('betting:home')
    
    if request.method != 'POST':
        return redirect('betting:admin_win_approvals')
    
    pending_approvals = BetWinApproval.objects.filter(status='pending')
    count = pending_approvals.count()
    
    for approval in pending_approvals:
        approval.approve(request.user)
    
    messages.success(request, f'✅ {count} lucros aprovados com sucesso!')
    
    return redirect('betting:admin_win_approvals')


@login_required
def admin_clear_all_data(request):
    """Limpar todos os dados de apostas (campeonatos, jogos, apostas, etc.)"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('betting:home')
    
    if request.method != 'POST':
        return redirect('betting:admin_dashboard')
    
    # Confirmar com código de segurança
    confirm_code = request.POST.get('confirm_code', '')
    if confirm_code != 'LIMPAR_TUDO':
        messages.error(request, '❌ Código de confirmação incorreto.')
        return redirect('betting:admin_dashboard')
    
    # Deletar na ordem correta para evitar erros de foreign key
    from .models import ScorerBet, ChampionBet, Bet, BetTransaction, BetWinApproval, MatchScorerOdds, Match, ChampionOdds, Championship
    
    # Contar antes de deletar
    counts = {
        'scorer_bets': ScorerBet.objects.count(),
        'champion_bets': ChampionBet.objects.count(),
        'bets': Bet.objects.count(),
        'transactions': BetTransaction.objects.count(),
        'approvals': BetWinApproval.objects.count(),
        'scorer_odds': MatchScorerOdds.objects.count(),
        'matches': Match.objects.count(),
        'champion_odds': ChampionOdds.objects.count(),
        'championships': Championship.objects.count(),
    }
    
    total = sum(counts.values())
    
    # Deletar tudo
    ScorerBet.objects.all().delete()
    ChampionBet.objects.all().delete()
    Bet.objects.all().delete()
    BetTransaction.objects.all().delete()
    BetWinApproval.objects.all().delete()
    MatchScorerOdds.objects.all().delete()
    Match.objects.all().delete()
    ChampionOdds.objects.all().delete()
    Championship.objects.all().delete()
    
    messages.success(
        request, 
        f'🗑️ Todos os dados foram limpos! '
        f'{counts["championships"]} campeonato(s), '
        f'{counts["matches"]} jogo(s), '
        f'{counts["bets"] + counts["champion_bets"] + counts["scorer_bets"]} aposta(s) removidas.'
    )
    
    return redirect('betting:admin_dashboard')


@login_required
def admin_team_logos(request):
    """Gerenciar escudos/logos dos times (setores)"""
    if not has_betting_admin_permission(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('betting:home')
    
    sectors = Sector.objects.all().order_by('name')
    
    if request.method == 'POST':
        # Processar upload de escudos
        for sector in sectors:
            logo_key = f'logo_{sector.id}'
            remove_key = f'remove_logo_{sector.id}'
            
            # Verificar se deve remover o logo
            if remove_key in request.POST:
                if sector.team_logo:
                    sector.team_logo.delete(save=True)
                continue
            
            # Verificar se há novo upload
            if logo_key in request.FILES:
                logo_file = request.FILES[logo_key]
                # Se já tinha logo, deletar o antigo
                if sector.team_logo:
                    sector.team_logo.delete(save=False)
                sector.team_logo = logo_file
                sector.save()
        
        messages.success(request, '✅ Escudos atualizados com sucesso!')
        return redirect('betting:admin_team_logos')
    
    context = {
        'sectors': sectors,
    }
    
    return render(request, 'betting/admin/team_logos.html', context)

