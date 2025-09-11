from django.contrib.auth.models import AbstractUser
from django.db import models
from decimal import Decimal
from core.utils import upload_user_profile_photo


class Sector(models.Model):
    name = models.CharField(max_length=100, verbose_name="Nome")
    description = models.TextField(blank=True, verbose_name="Descrição")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        verbose_name = "Setor"
        verbose_name_plural = "Setores"
        ordering = ['name']
    
    def __str__(self):
        return self.name


class User(AbstractUser):
    HIERARCHY_CHOICES = [
        ('PADRAO', 'Padrão'),
        ('ADMINISTRATIVO', 'Administrativo'),
        ('SUPERVISOR', 'Supervisor'),
        ('ADMIN', 'Administração'),
        ('SUPERADMIN', 'Superadmin'),
    ]
    
    email = models.EmailField(unique=True)
    sectors = models.ManyToManyField(Sector, blank=True, verbose_name="Setores", related_name="users")
    sector = models.ForeignKey(Sector, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Setor Principal", help_text="Setor principal para compatibilidade", related_name="primary_users")
    hierarchy = models.CharField(max_length=20, choices=HIERARCHY_CHOICES, default='PADRAO', verbose_name="Hierarquia")
    balance_cs = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="Saldo C$")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Telefone")
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True, verbose_name="Avatar")
    profile_picture = models.ImageField(upload_to=upload_user_profile_photo, blank=True, null=True)
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name']
    
    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
        ordering = ['first_name', 'last_name']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()
    
    @property
    def primary_sector(self):
        """Retorna o setor principal ou o primeiro setor da lista"""
        if self.sector:
            return self.sector
        return self.sectors.first()
    
    @property
    def all_sectors(self):
        """Retorna todos os setores do usuário"""
        return self.sectors.all()
    
    @property
    def sectors_display(self):
        """Retorna uma string com todos os setores separados por vírgula"""
        sectors = self.sectors.all()
        if sectors:
            return ", ".join([sector.name for sector in sectors])
        return "Nenhum setor"
    
    def is_in_sector(self, sector):
        """Verifica se o usuário pertence a um setor específico"""
        return self.sectors.filter(id=sector.id).exists()
    
    def add_sector(self, sector):
        """Adiciona um setor ao usuário"""
        self.sectors.add(sector)
        # Se não tem setor principal, define este como principal
        if not self.sector:
            self.sector = sector
            self.save()
    
    def remove_sector(self, sector):
        """Remove um setor do usuário"""
        self.sectors.remove(sector)
        # Se o setor removido era o principal, define outro como principal
        if self.sector == sector:
            self.sector = self.sectors.first()
            self.save()
    
    def can_manage_users(self):
        return self.hierarchy in ['ADMIN', 'SUPERADMIN']
    
    @property
    def calculated_balance_cs(self):
        """Calcula o saldo C$ baseado apenas em transações aprovadas"""
        from prizes.models import CSTransaction
        from django.db.models import Sum, Q
        
        # Somar créditos aprovados
        credits = CSTransaction.objects.filter(
            user=self,
            transaction_type__in=['CREDIT', 'ADJUSTMENT'],
            status='APPROVED',
            amount__gt=0
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        # Somar débitos aprovados (valores negativos ou tipos DEBIT/REDEMPTION)
        debits = CSTransaction.objects.filter(
            user=self,
            status='APPROVED'
        ).filter(
            Q(transaction_type__in=['DEBIT', 'REDEMPTION']) |
            Q(amount__lt=0)
        ).aggregate(total=Sum('amount'))['total'] or 0
        
        return abs(credits) - abs(debits)

    def can_manage_prizes(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']

    def can_manage_cs(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_view_all_tickets(self):
        return self.hierarchy in ['ADMIN', 'SUPERADMIN']
    
    def can_view_sector_tickets(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_create_communications(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_edit_sector_categories(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_upload_files(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_access_management_panel(self):
        return self.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_access_admin_panel(self):
        return self.hierarchy in ['ADMIN', 'SUPERADMIN']
    
    def can_view_reports(self):
        return self.hierarchy in ['SUPERVISOR', 'ADMIN', 'SUPERADMIN']
    
    def can_manage_webhooks(self):
        return self.hierarchy in ['SUPERADMIN']
    
    def can_delete_users(self):
        return self.hierarchy in ['SUPERADMIN']
    
    def can_delete_tickets(self):
        return self.hierarchy in ['SUPERADMIN']
