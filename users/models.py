from django.contrib.auth.models import AbstractUser
from django.db import models
from decimal import Decimal


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
        ('SUPERVISOR', 'Supervisor'),
        ('ADMINISTRATIVO', 'Administrativo'),
        ('SUPERADMIN', 'Superadmin'),
    ]
    
    email = models.EmailField(unique=True)
    sector = models.ForeignKey(Sector, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Setor")
    hierarchy = models.CharField(max_length=20, choices=HIERARCHY_CHOICES, default='PADRAO', verbose_name="Hierarquia")
    balance_cs = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'), verbose_name="Saldo C$")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Telefone")
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True, verbose_name="Avatar")
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
    
    def can_manage_users(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERADMIN', 'ADMIN', 'SUPERVISOR']

    def can_manage_prizes(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERADMIN', 'ADMIN', 'SUPERVISOR']

    def can_manage_cs(self):
        return self.hierarchy in ['ADMINISTRATIVO', 'SUPERADMIN', 'ADMIN', 'SUPERVISOR']
    
    def can_view_all_tickets(self):
        return self.hierarchy in ['ADMIN', 'SUPERADMIN', 'ADMINISTRATIVO']
    
    def can_view_sector_tickets(self):
        return self.hierarchy in ['SUPERVISOR', 'ADMINISTRATIVO', 'ADMIN', 'SUPERADMIN']
