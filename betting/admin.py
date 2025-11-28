from django.contrib import admin
from .models import (
    Championship, ChampionOdds, ChampionBet,
    Match, MatchScorerOdds, ScorerBet,
    Bet, BetTransaction, BetWinApproval
)


@admin.register(Championship)
class ChampionshipAdmin(admin.ModelAdmin):
    list_display = ['name', 'status', 'champion', 'created_by', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['name', 'description']
    filter_horizontal = ['participating_sectors']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(ChampionOdds)
class ChampionOddsAdmin(admin.ModelAdmin):
    list_display = ['championship', 'sector', 'odds', 'is_active']
    list_filter = ['championship', 'is_active']
    search_fields = ['sector__name', 'championship__name']


@admin.register(ChampionBet)
class ChampionBetAdmin(admin.ModelAdmin):
    list_display = ['user', 'championship', 'sector', 'amount', 'odds_at_bet', 'potential_win', 'status']
    list_filter = ['status', 'championship', 'created_at']
    search_fields = ['user__username', 'user__email', 'sector__name']
    readonly_fields = ['created_at', 'processed_at']


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'championship', 'match_date', 'status', 'result', 'round_number']
    list_filter = ['status', 'championship', 'match_date']
    search_fields = ['home_team__name', 'away_team__name']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(MatchScorerOdds)
class MatchScorerOddsAdmin(admin.ModelAdmin):
    list_display = ['match', 'player', 'odds', 'scored', 'goals', 'is_active']
    list_filter = ['match__championship', 'scored', 'is_active']
    search_fields = ['player__first_name', 'player__last_name', 'player__username']


@admin.register(ScorerBet)
class ScorerBetAdmin(admin.ModelAdmin):
    list_display = ['user', 'match', 'player', 'amount', 'odds_at_bet', 'potential_win', 'status']
    list_filter = ['status', 'match__championship', 'created_at']
    search_fields = ['user__username', 'player__first_name', 'player__last_name']
    readonly_fields = ['created_at', 'processed_at']


@admin.register(Bet)
class BetAdmin(admin.ModelAdmin):
    list_display = ['user', 'match', 'bet_type', 'amount', 'odds_at_bet', 'potential_win', 'status', 'winnings']
    list_filter = ['status', 'bet_type', 'created_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at']


@admin.register(BetTransaction)
class BetTransactionAdmin(admin.ModelAdmin):
    list_display = ['user', 'transaction_type', 'amount', 'balance_before', 'balance_after', 'created_at']
    list_filter = ['transaction_type', 'created_at']
    search_fields = ['user__username', 'user__email', 'description']
    readonly_fields = ['created_at']


@admin.register(BetWinApproval)
class BetWinApprovalAdmin(admin.ModelAdmin):
    list_display = ['user', 'bet_type', 'original_amount', 'profit_amount', 'odds_at_bet', 'status', 'reviewed_by', 'created_at']
    list_filter = ['status', 'bet_type', 'created_at']
    search_fields = ['user__username', 'user__email', 'description']
    readonly_fields = ['created_at', 'reviewed_at']
    
    actions = ['approve_selected', 'reject_selected']
    
    def approve_selected(self, request, queryset):
        for approval in queryset.filter(status='pending'):
            approval.approve(request.user)
        self.message_user(request, f'{queryset.filter(status="approved").count()} aprovações processadas.')
    approve_selected.short_description = 'Aprovar lucros selecionados'
    
    def reject_selected(self, request, queryset):
        for approval in queryset.filter(status='pending'):
            approval.reject(request.user, 'Rejeitado em massa pelo admin')
        self.message_user(request, f'{queryset.filter(status="rejected").count()} rejeitados.')
