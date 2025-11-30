from django.db import models
from django.conf import settings
from django.utils import timezone
from decimal import Decimal
from users.models import Sector, User


def get_media_storage():
    """Return media storage backend"""
    if getattr(settings, 'USE_S3', False):
        from core.storage import MediaStorage
        return MediaStorage()
    return None


def upload_championship_banner(instance, filename):
    """Define o caminho de upload para banners de campeonatos"""
    import os
    ext = filename.split('.')[-1]
    new_filename = f"championship_{instance.id or 'new'}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    return os.path.join('betting', 'championships', new_filename)


class Championship(models.Model):
    """Campeonato"""
    STATUS_CHOICES = [
        ('draft', 'Rascunho'),
        ('active', 'Ativo'),
        ('paused', 'Pausado'),
        ('finished', 'Finalizado'),
    ]
    
    name = models.CharField(max_length=200, verbose_name='Nome do Campeonato')
    description = models.TextField(blank=True, verbose_name='Descrição')
    
    # Setores participantes
    participating_sectors = models.ManyToManyField(
        Sector,
        related_name='championships',
        verbose_name='Setores Participantes'
    )
    
    # Link externo para tabela de classificação
    table_url = models.URLField(
        blank=True,
        verbose_name='URL da Tabela de Classificação',
        help_text='Cole aqui o link do site com a tabela do campeonato (será exibido em tela cheia)'
    )
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        verbose_name='Status'
    )
    
    # Imagem/Banner
    banner = models.ImageField(
        upload_to=upload_championship_banner,
        storage=get_media_storage(),
        blank=True,
        null=True,
        verbose_name='Banner'
    )
    
    # Campeão (preenchido ao finalizar)
    champion = models.ForeignKey(
        Sector,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='championships_won',
        verbose_name='Campeão'
    )
    
    # Metadados
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='created_championships',
        verbose_name='Criado por'
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    
    class Meta:
        verbose_name = 'Campeonato'
        verbose_name_plural = 'Campeonatos'
        ordering = ['-created_at']
    
    def __str__(self):
        return self.name
    
    def get_participating_players(self):
        """Retorna todos os jogadores (usuários) dos setores participantes"""
        return User.objects.filter(
            sector__in=self.participating_sectors.all()
        ).order_by('first_name', 'last_name')
    
    def finalize_championship(self, champion_sector):
        """Finaliza o campeonato e processa apostas do campeão"""
        self.champion = champion_sector
        self.status = 'finished'
        self.save()
        
        # Processar apostas de campeão
        self.process_champion_bets()
    
    def process_champion_bets(self):
        """Processa todas as apostas de campeão"""
        from django.db import transaction
        
        with transaction.atomic():
            for bet in self.champion_bets.filter(status='pending'):
                bet.process_result()


class ChampionOdds(models.Model):
    """Odds para cada time vencer o campeonato"""
    championship = models.ForeignKey(
        Championship,
        on_delete=models.CASCADE,
        related_name='champion_odds',
        verbose_name='Campeonato'
    )
    
    sector = models.ForeignKey(
        Sector,
        on_delete=models.CASCADE,
        related_name='champion_odds',
        verbose_name='Time (Setor)'
    )
    
    odds = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('5.00'),
        verbose_name='Odds'
    )
    
    is_active = models.BooleanField(
        default=True,
        verbose_name='Ativo',
        help_text='Se desativado, não aceita mais apostas'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Odd de Campeão'
        verbose_name_plural = 'Odds de Campeões'
        unique_together = ['championship', 'sector']
        ordering = ['odds']
    
    def __str__(self):
        return f"{self.sector.name} - {self.odds}x ({self.championship.name})"


class ChampionBet(models.Model):
    """Aposta no campeão do campeonato"""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('won', 'Ganhou'),
        ('lost', 'Perdeu'),
        ('cancelled', 'Cancelada'),
        ('refunded', 'Reembolsada'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='champion_bets',
        verbose_name='Usuário'
    )
    
    championship = models.ForeignKey(
        Championship,
        on_delete=models.CASCADE,
        related_name='champion_bets',
        verbose_name='Campeonato'
    )
    
    # Time apostado como campeão
    sector = models.ForeignKey(
        Sector,
        on_delete=models.CASCADE,
        related_name='champion_bets_received',
        verbose_name='Time Apostado'
    )
    
    # Valor apostado (em C$)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Valor Apostado (C$)'
    )
    
    # Odds no momento da aposta
    odds_at_bet = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        verbose_name='Odds no Momento'
    )
    
    # Valor potencial de ganho
    potential_win = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Ganho Potencial'
    )
    
    # Status da aposta
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status'
    )
    
    # Valor ganho (preenchido após resultado)
    winnings = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Valor Ganho'
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Apostado em')
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name='Processado em')
    
    class Meta:
        verbose_name = 'Aposta de Campeão'
        verbose_name_plural = 'Apostas de Campeão'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.first_name} apostou {self.amount} C$ em {self.sector.name} campeão"
    
    def save(self, *args, **kwargs):
        if not self.potential_win:
            self.potential_win = self.amount * self.odds_at_bet
        super().save(*args, **kwargs)
    
    def process_result(self):
        """Processa o resultado da aposta de campeão"""
        from django.db import transaction
        
        with transaction.atomic():
            if self.championship.champion == self.sector:
                # Ganhou!
                self.status = 'won'
                self.winnings = self.potential_win
                
                # Calcular lucro (ganho total - valor apostado)
                profit = self.potential_win - self.amount
                
                # Devolver o valor apostado imediatamente
                balance_before = self.user.balance_cs
                self.user.balance_cs += self.amount
                self.user.save()
                
                # Registrar transação do reembolso do valor apostado
                BetTransaction.objects.create(
                    user=self.user,
                    transaction_type='win',
                    amount=self.amount,
                    balance_before=balance_before,
                    balance_after=self.user.balance_cs,
                    description=f'Devolução aposta campeão ganha: {self.sector.name}'
                )
                
                # Criar aprovação de lucro (se houver lucro)
                if profit > 0:
                    BetWinApproval.objects.create(
                        user=self.user,
                        bet_type='champion',
                        bet_id=self.id,
                        original_amount=self.amount,
                        profit_amount=profit,
                        total_win=self.potential_win,
                        odds_at_bet=self.odds_at_bet,
                        description=f'Campeão: {self.sector.name} ({self.championship.name})'
                    )
            else:
                # Perdeu - não faz nada, valor já foi debitado
                self.status = 'lost'
                self.winnings = Decimal('0.00')
            
            self.processed_at = timezone.now()
            self.save()


class Match(models.Model):
    """Jogo/Partida entre dois setores"""
    STATUS_CHOICES = [
        ('scheduled', 'Agendado'),
        ('live', 'Ao Vivo'),
        ('finished', 'Finalizado'),
        ('cancelled', 'Cancelado'),
    ]
    
    championship = models.ForeignKey(
        Championship,
        on_delete=models.CASCADE,
        related_name='matches',
        verbose_name='Campeonato'
    )
    
    # Times (Setores)
    home_team = models.ForeignKey(
        Sector,
        on_delete=models.CASCADE,
        related_name='home_matches',
        verbose_name='Time da Casa (Setor 1)'
    )
    away_team = models.ForeignKey(
        Sector,
        on_delete=models.CASCADE,
        related_name='away_matches',
        verbose_name='Time Visitante (Setor 2)'
    )
    
    # Placar
    home_score = models.PositiveIntegerField(default=0, verbose_name='Gols Casa')
    away_score = models.PositiveIntegerField(default=0, verbose_name='Gols Visitante')
    
    # Odds base (definidas pelo admin)
    odds_home = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('2.00'),
        verbose_name='Odds Vitória Casa'
    )
    odds_draw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('3.00'),
        verbose_name='Odds Empate'
    )
    odds_away = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('2.00'),
        verbose_name='Odds Vitória Visitante'
    )
    
    # Data/Hora do jogo
    match_date = models.DateTimeField(verbose_name='Data e Hora do Jogo')
    
    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='scheduled',
        verbose_name='Status'
    )
    
    # Rodada (opcional)
    round_number = models.PositiveIntegerField(
        default=1,
        verbose_name='Rodada'
    )
    
    # Resultado final para determinar vencedor
    RESULT_CHOICES = [
        ('pending', 'Pendente'),
        ('home', 'Vitória Casa'),
        ('draw', 'Empate'),
        ('away', 'Vitória Visitante'),
    ]
    result = models.CharField(
        max_length=10,
        choices=RESULT_CHOICES,
        default='pending',
        verbose_name='Resultado'
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    
    class Meta:
        verbose_name = 'Partida'
        verbose_name_plural = 'Partidas'
        ordering = ['match_date']
    
    def __str__(self):
        return f"{self.home_team} {self.home_score} x {self.away_score} {self.away_team}"
    
    def calculate_result(self):
        """Calcula o resultado baseado no placar"""
        if self.home_score > self.away_score:
            return 'home'
        elif self.away_score > self.home_score:
            return 'away'
        else:
            return 'draw'
    
    def finalize_match(self):
        """Finaliza a partida e processa as apostas"""
        self.result = self.calculate_result()
        self.status = 'finished'
        self.save()
        
        # Processar apostas de resultado
        self.process_bets()
        
        # Processar apostas de artilheiro
        self.process_scorer_bets()
    
    def process_bets(self):
        """Processa todas as apostas desta partida"""
        from django.db import transaction
        
        with transaction.atomic():
            for bet in self.bets.filter(status='pending'):
                bet.process_result(self.result)
    
    def process_scorer_bets(self):
        """Processa todas as apostas de artilheiro desta partida"""
        from django.db import transaction
        
        with transaction.atomic():
            for bet in self.scorer_bets.filter(status='pending'):
                bet.process_result()
    
    def update_live_odds(self):
        """Atualiza odds dinamicamente baseado no placar atual (durante jogo ao vivo)"""
        if self.status != 'live':
            return
        
        # Lógica simples de ajuste de odds baseado no placar
        goal_diff = self.home_score - self.away_score
        
        # Base multipliers
        if goal_diff > 0:  # Casa vencendo
            # Diminui odds da casa, aumenta do visitante
            home_mult = max(0.5, 1 - (goal_diff * 0.15))
            away_mult = min(3.0, 1 + (goal_diff * 0.3))
            draw_mult = min(2.5, 1 + (abs(goal_diff) * 0.2))
        elif goal_diff < 0:  # Visitante vencendo
            # Aumenta odds da casa, diminui do visitante
            home_mult = min(3.0, 1 + (abs(goal_diff) * 0.3))
            away_mult = max(0.5, 1 - (abs(goal_diff) * 0.15))
            draw_mult = min(2.5, 1 + (abs(goal_diff) * 0.2))
        else:  # Empate
            home_mult = 1.0
            away_mult = 1.0
            draw_mult = 0.9
        
        # Aplicar multiplicadores às odds base (mantendo mínimo de 1.10)
        self.odds_home = max(Decimal('1.10'), self.odds_home * Decimal(str(home_mult)))
        self.odds_draw = max(Decimal('1.10'), self.odds_draw * Decimal(str(draw_mult)))
        self.odds_away = max(Decimal('1.10'), self.odds_away * Decimal(str(away_mult)))
        
        self.save()


class Bet(models.Model):
    """Aposta de um usuário em uma partida"""
    BET_TYPE_CHOICES = [
        ('home', 'Vitória Casa'),
        ('draw', 'Empate'),
        ('away', 'Vitória Visitante'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('won', 'Ganhou'),
        ('lost', 'Perdeu'),
        ('cancelled', 'Cancelada'),
        ('refunded', 'Reembolsada'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bets',
        verbose_name='Usuário'
    )
    
    match = models.ForeignKey(
        Match,
        on_delete=models.CASCADE,
        related_name='bets',
        verbose_name='Partida'
    )
    
    # Tipo de aposta
    bet_type = models.CharField(
        max_length=10,
        choices=BET_TYPE_CHOICES,
        verbose_name='Tipo de Aposta'
    )
    
    # Valor apostado (em C$)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Valor Apostado (C$)'
    )
    
    # Odds no momento da aposta
    odds_at_bet = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        verbose_name='Odds no Momento'
    )
    
    # Valor potencial de ganho
    potential_win = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Ganho Potencial'
    )
    
    # Status da aposta
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status'
    )
    
    # Valor ganho (preenchido após resultado)
    winnings = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Valor Ganho'
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Apostado em')
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name='Processado em')
    
    class Meta:
        verbose_name = 'Aposta'
        verbose_name_plural = 'Apostas'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.first_name} - {self.amount} C$ em {self.get_bet_type_display()}"
    
    def save(self, *args, **kwargs):
        # Calcular ganho potencial antes de salvar
        if not self.potential_win:
            self.potential_win = self.amount * self.odds_at_bet
        super().save(*args, **kwargs)
    
    def process_result(self, match_result):
        """Processa o resultado da aposta"""
        from django.db import transaction
        
        with transaction.atomic():
            if self.bet_type == match_result:
                # Ganhou!
                self.status = 'won'
                self.winnings = self.potential_win
                
                # Calcular lucro (ganho total - valor apostado)
                profit = self.potential_win - self.amount
                
                # Devolver o valor apostado imediatamente
                balance_before = self.user.balance_cs
                self.user.balance_cs += self.amount
                self.user.save()
                
                # Registrar transação do reembolso do valor apostado
                BetTransaction.objects.create(
                    user=self.user,
                    bet=self,
                    transaction_type='win',
                    amount=self.amount,
                    balance_before=balance_before,
                    balance_after=self.user.balance_cs,
                    description=f'Devolução aposta ganha: {self.match}'
                )
                
                # Criar aprovação de lucro (se houver lucro)
                if profit > 0:
                    BetWinApproval.objects.create(
                        user=self.user,
                        bet_type='match',
                        bet_id=self.id,
                        original_amount=self.amount,
                        profit_amount=profit,
                        total_win=self.potential_win,
                        odds_at_bet=self.odds_at_bet,
                        description=f'{self.match.home_team} x {self.match.away_team} - {self.get_bet_type_display()}'
                    )
            else:
                # Perdeu - não faz nada, valor já foi debitado
                self.status = 'lost'
                self.winnings = Decimal('0.00')
            
            self.processed_at = timezone.now()
            self.save()


class BetTransaction(models.Model):
    """Histórico de transações de apostas"""
    TRANSACTION_TYPE_CHOICES = [
        ('bet', 'Aposta'),
        ('win', 'Ganho'),
        ('win_profit', 'Lucro Aprovado'),
        ('refund', 'Reembolso'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bet_transactions',
        verbose_name='Usuário'
    )
    
    bet = models.ForeignKey(
        Bet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transactions',
        verbose_name='Aposta'
    )
    
    transaction_type = models.CharField(
        max_length=20,
        choices=TRANSACTION_TYPE_CHOICES,
        verbose_name='Tipo'
    )
    
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Valor'
    )
    
    balance_before = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Saldo Antes'
    )
    
    balance_after = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Saldo Depois'
    )
    
    description = models.CharField(
        max_length=500,
        blank=True,
        verbose_name='Descrição'
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Data')
    
    class Meta:
        verbose_name = 'Transação de Aposta'
        verbose_name_plural = 'Transações de Apostas'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.first_name} - {self.get_transaction_type_display()} - {self.amount} C$"


class BetWinApproval(models.Model):
    """Aprovação de lucros de apostas ganhas - para evitar fraudes"""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('approved', 'Aprovado'),
        ('rejected', 'Rejeitado'),
    ]
    
    BET_TYPE_CHOICES = [
        ('match', 'Aposta de Partida'),
        ('champion', 'Aposta de Campeão'),
        ('scorer', 'Aposta de Artilheiro'),
    ]
    
    # Referência ao usuário que ganhou
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='bet_win_approvals',
        verbose_name='Usuário'
    )
    
    # Tipo de aposta
    bet_type = models.CharField(
        max_length=20,
        choices=BET_TYPE_CHOICES,
        verbose_name='Tipo de Aposta'
    )
    
    # Referência à aposta (um dos três tipos)
    bet_id = models.PositiveIntegerField(
        verbose_name='ID da Aposta',
        help_text='ID da aposta no modelo correspondente'
    )
    
    # Valores
    original_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Valor Apostado'
    )
    
    profit_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Lucro (para aprovação)'
    )
    
    total_win = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Ganho Total'
    )
    
    odds_at_bet = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        verbose_name='Odds'
    )
    
    # Descrição da aposta para contexto
    description = models.CharField(
        max_length=500,
        verbose_name='Descrição da Aposta'
    )
    
    # Status da aprovação
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status'
    )
    
    # Quem aprovou/rejeitou
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bet_approvals_reviewed',
        verbose_name='Revisado por'
    )
    
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Data da Revisão'
    )
    
    rejection_reason = models.TextField(
        blank=True,
        verbose_name='Motivo da Rejeição'
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    
    class Meta:
        verbose_name = 'Aprovação de Lucro de Aposta'
        verbose_name_plural = 'Aprovações de Lucros de Apostas'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.first_name} - Lucro {self.profit_amount} C$ - {self.get_status_display()}"
    
    def approve(self, reviewer):
        """Aprovar o lucro e creditar na conta do usuário"""
        from django.db import transaction as db_transaction
        
        with db_transaction.atomic():
            self.status = 'approved'
            self.reviewed_by = reviewer
            self.reviewed_at = timezone.now()
            self.save()
            
            # Creditar o lucro ao usuário
            balance_before = self.user.balance_cs
            self.user.balance_cs += self.profit_amount
            self.user.save()
            
            # Registrar transação
            BetTransaction.objects.create(
                user=self.user,
                transaction_type='win_profit',
                amount=self.profit_amount,
                balance_before=balance_before,
                balance_after=self.user.balance_cs,
                description=f'Lucro aprovado: {self.description}'
            )
    
    def reject(self, reviewer, reason=''):
        """Rejeitar o lucro (possível fraude)"""
        self.status = 'rejected'
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.rejection_reason = reason
        self.save()


class MatchScorerOdds(models.Model):
    """Odds para cada jogador marcar gol em uma partida específica"""
    match = models.ForeignKey(
        Match,
        on_delete=models.CASCADE,
        related_name='scorer_odds',
        verbose_name='Partida'
    )
    
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scorer_odds',
        verbose_name='Jogador'
    )
    
    odds = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal('3.00'),
        verbose_name='Odds'
    )
    
    is_active = models.BooleanField(
        default=True,
        verbose_name='Ativo',
        help_text='Se desativado, não aceita mais apostas'
    )
    
    # Se o jogador marcou gol nesta partida
    scored = models.BooleanField(
        default=False,
        verbose_name='Marcou Gol'
    )
    
    # Quantidade de gols marcados
    goals = models.PositiveIntegerField(
        default=0,
        verbose_name='Gols Marcados'
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Odd de Artilheiro'
        verbose_name_plural = 'Odds de Artilheiros'
        unique_together = ['match', 'player']
        ordering = ['odds']
    
    def __str__(self):
        return f"{self.player.get_full_name()} - {self.odds}x ({self.match})"


class ScorerBet(models.Model):
    """Aposta no artilheiro de uma partida"""
    STATUS_CHOICES = [
        ('pending', 'Pendente'),
        ('won', 'Ganhou'),
        ('lost', 'Perdeu'),
        ('cancelled', 'Cancelada'),
        ('refunded', 'Reembolsada'),
    ]
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scorer_bets',
        verbose_name='Apostador'
    )
    
    match = models.ForeignKey(
        Match,
        on_delete=models.CASCADE,
        related_name='scorer_bets',
        verbose_name='Partida'
    )
    
    # Jogador apostado como artilheiro
    player = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='scorer_bets_received',
        verbose_name='Jogador Apostado'
    )
    
    # Valor apostado (em C$)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Valor Apostado (C$)'
    )
    
    # Odds no momento da aposta
    odds_at_bet = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        verbose_name='Odds no Momento'
    )
    
    # Valor potencial de ganho
    potential_win = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name='Ganho Potencial'
    )
    
    # Status da aposta
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='Status'
    )
    
    # Valor ganho (preenchido após resultado)
    winnings = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        verbose_name='Valor Ganho'
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Apostado em')
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name='Processado em')
    
    class Meta:
        verbose_name = 'Aposta de Artilheiro'
        verbose_name_plural = 'Apostas de Artilheiro'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.user.first_name} apostou {self.amount} C$ em {self.player.get_full_name()} marcar"
    
    def save(self, *args, **kwargs):
        if not self.potential_win:
            self.potential_win = self.amount * self.odds_at_bet
        super().save(*args, **kwargs)
    
    def process_result(self):
        """Processa o resultado da aposta de artilheiro"""
        from django.db import transaction
        
        # Verificar se o jogador marcou
        scorer_odds = MatchScorerOdds.objects.filter(
            match=self.match,
            player=self.player
        ).first()
        
        with transaction.atomic():
            if scorer_odds and scorer_odds.scored:
                # Ganhou!
                self.status = 'won'
                self.winnings = self.potential_win
                
                # Calcular lucro (ganho total - valor apostado)
                profit = self.potential_win - self.amount
                
                # Devolver o valor apostado imediatamente
                balance_before = self.user.balance_cs
                self.user.balance_cs += self.amount
                self.user.save()
                
                # Registrar transação do reembolso do valor apostado
                BetTransaction.objects.create(
                    user=self.user,
                    transaction_type='win',
                    amount=self.amount,
                    balance_before=balance_before,
                    balance_after=self.user.balance_cs,
                    description=f'Devolução aposta artilheiro ganha: {self.player.get_full_name()}'
                )
                
                # Criar aprovação de lucro (se houver lucro)
                if profit > 0:
                    BetWinApproval.objects.create(
                        user=self.user,
                        bet_type='scorer',
                        bet_id=self.id,
                        original_amount=self.amount,
                        profit_amount=profit,
                        total_win=self.potential_win,
                        odds_at_bet=self.odds_at_bet,
                        description=f'Artilheiro: {self.player.get_full_name()} marcou ({self.match})'
                    )
            else:
                # Perdeu - não faz nada, valor já foi debitado
                self.status = 'lost'
                self.winnings = Decimal('0.00')
            
            self.processed_at = timezone.now()
            self.save()
