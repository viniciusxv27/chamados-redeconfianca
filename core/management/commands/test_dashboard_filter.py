from django.core.management.base import BaseCommand
from users.models import User
from tickets.models import Ticket
from django.db import models


class Command(BaseCommand):
    help = 'Testa os filtros do dashboard ANALÍTICO (core/views.py) para debug'

    def handle(self, *args, **options):
        # Pegar usuários com diferentes hierarquias
        users = User.objects.filter(hierarchy__in=['ADMIN', 'SUPERVISOR', 'ADMINISTRATIVO', 'SUPERADMIN'])[:5]
        if not users.exists():
            # Se não houver, testar qualquer usuário
            users = User.objects.all()[:3]
        
        for user in users:
            self.stdout.write(f"\n=== TESTANDO USER: {user.username} ===")
            self.stdout.write(f"Hierarchy: {user.hierarchy}")
            self.stdout.write(f"can_view_all_tickets: {user.can_view_all_tickets()}")
            self.stdout.write(f"can_view_sector_tickets: {user.can_view_sector_tickets()}")
            self.stdout.write(f"user.sector: {user.sector}")
            self.stdout.write(f"user.sectors.all(): {list(user.sectors.all())}")
            
            # Aplicar a nova lógica do dashboard (sem created_by para supervisores)
            if user.can_view_all_tickets():
                user_tickets = Ticket.objects.all()
                self.stdout.write("Filtro aplicado: ADMIN - todos os tickets")
            elif user.can_view_sector_tickets():
                user_sectors = list(user.sectors.all())
                if user.sector:
                    user_sectors.append(user.sector)
                
                self.stdout.write(f"user_sectors finais: {user_sectors}")
                
                user_tickets = Ticket.objects.filter(
                    models.Q(sector__in=user_sectors) |
                    models.Q(created_by=user) |
                    models.Q(assigned_to=user)
                ).distinct()
                self.stdout.write(f"Filtro aplicado: SETOR + CRIADOS PELO USER - {len(user_tickets)} tickets encontrados")
            else:
                user_tickets = Ticket.objects.filter(
                    models.Q(created_by=user) |
                    models.Q(assigned_to=user) |
                    models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
                ).exclude(status='FECHADO').distinct()
                self.stdout.write("Filtro aplicado: USUARIO - apenas próprios tickets")
            
            # Mostrar tickets recentes
            recent_tickets = user_tickets.order_by('-created_at')[:3]
            self.stdout.write(f"\nTickets recentes ({len(recent_tickets)}):")
            for ticket in recent_tickets:
                self.stdout.write(f"  Ticket #{ticket.id}: {ticket.title}")
                self.stdout.write(f"    Setor: {ticket.sector}")
                self.stdout.write(f"    Criado por: {ticket.created_by}")
                self.stdout.write(f"    Setor está nos user_sectors? {ticket.sector in user_sectors if user.can_view_sector_tickets() else 'N/A'}")
                self.stdout.write(f"    Criado pelo usuário? {ticket.created_by == user}")
                self.stdout.write(f"    Atribuído ao usuário? {ticket.assigned_to == user}")