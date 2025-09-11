from django.core.management.base import BaseCommand
from tickets.models import PurchaseOrderApprover
from users.models import User


class Command(BaseCommand):
    help = 'Configura aprovadores iniciais para ordem de compra'

    def handle(self, *args, **options):
        # Definir aprovadores conforme solicitado
        approvers_config = [
            {'name': 'Maicon', 'email': 'gerencia.financeiro@redeconfianca.com', 'order': 1, 'amount': 100.00},
            {'name': 'Alcides', 'email': 'alcides@redeconfianca.com', 'order': 2, 'amount': 500.00},
            {'name': 'Lucas', 'email': 'lucas@redeconfianca.com', 'order': 3, 'amount': 1000.00},
        ]
        
        created_count = 0
        for config in approvers_config:
            try:
                user = User.objects.get(email=config['email'])
                
                approver, created = PurchaseOrderApprover.objects.get_or_create(
                    user=user,
                    defaults={
                        'max_amount': config['amount'],
                        'approval_order': config['order']
                    }
                )
                
                if created:
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Aprovador criado: {user.full_name} - Ordem {config["order"]} - Até R$ {config["amount"]}')
                    )
                else:
                    # Atualizar valores se já existir
                    approver.max_amount = config['amount']
                    approver.approval_order = config['order']
                    approver.save()
                    self.stdout.write(
                        self.style.WARNING(f'- Aprovador atualizado: {user.full_name} - Ordem {config["order"]} - Até R$ {config["amount"]}')
                    )
                    
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'✗ Usuário não encontrado: {config["email"]}')
                )
        
        if created_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'\nTotal de {created_count} aprovadores criados com sucesso!')
            )
        else:
            self.stdout.write(
                self.style.WARNING('Nenhum aprovador novo foi criado. Configurações atualizadas.')
            )