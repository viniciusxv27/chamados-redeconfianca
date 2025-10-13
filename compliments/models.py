from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from users.models import User, Sector


class Compliment(models.Model):
    """Modelo para elogios entre usuários ou para setores"""
    
    RATING_CHOICES = [
        (1, '1 Estrela'),
        (2, '2 Estrelas'),
        (3, '3 Estrelas'),
        (4, '4 Estrelas'),
        (5, '5 Estrelas'),
    ]
    
    # Quem fez o elogio
    from_user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='compliments_given',
        verbose_name='De'
    )
    
    # Para quem é o elogio (usuário ou setor)
    to_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='compliments_received',
        null=True,
        blank=True,
        verbose_name='Para usuário'
    )
    
    to_sector = models.ForeignKey(
        Sector,
        on_delete=models.CASCADE,
        related_name='compliments_received',
        null=True,
        blank=True,
        verbose_name='Para setor'
    )
    
    # Conteúdo do elogio
    rating = models.IntegerField(
        choices=RATING_CHOICES,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        verbose_name='Avaliação'
    )
    
    comment = models.TextField(
        verbose_name='Comentário',
        help_text='Descreva o motivo do elogio'
    )
    
    # Metadados
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Criado em')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Atualizado em')
    is_active = models.BooleanField(default=True, verbose_name='Ativo')
    
    class Meta:
        verbose_name = 'Elogio'
        verbose_name_plural = 'Elogios'
        ordering = ['-created_at']
        
    def __str__(self):
        target = self.to_user.get_full_name() if self.to_user else self.to_sector.name
        return f'Elogio de {self.from_user.get_full_name()} para {target} - {self.rating}★'
    
    def clean(self):
        from django.core.exceptions import ValidationError
        
        # Validar que tem um destinatário (usuário OU setor)
        if not self.to_user and not self.to_sector:
            raise ValidationError('É necessário especificar um usuário ou setor destinatário.')
        
        # Validar que não tem os dois destinatários
        if self.to_user and self.to_sector:
            raise ValidationError('Não é possível especificar usuário e setor ao mesmo tempo.')
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
    
    @property
    def target_name(self):
        """Retorna o nome do alvo do elogio"""
        if self.to_user:
            return self.to_user.get_full_name() or self.to_user.username
        elif self.to_sector:
            return self.to_sector.name
        return 'Destinatário não especificado'
    
    @property
    def target_type(self):
        """Retorna o tipo do alvo"""
        if self.to_user:
            return 'user'
        elif self.to_sector:
            return 'sector'
        return 'unknown'
    
    @property
    def stars_display(self):
        """Retorna as estrelas para exibição"""
        return '★' * self.rating + '☆' * (5 - self.rating)