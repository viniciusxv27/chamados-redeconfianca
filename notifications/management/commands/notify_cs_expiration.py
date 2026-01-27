"""
Management command para notificar usuários sobre confianças (C$) próximas de vencer.
Deve ser executado diariamente via cron/scheduled task.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from prizes.models import CSTransaction
from notifications.models import Notification


class Command(BaseCommand):
    help = 'Envia notificações para usuários com confianças (C$) próximas de vencer'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Executa sem enviar notificações (apenas mostra o que seria feito)',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        today = timezone.now().date()
        
        # Datas de verificação
        date_7_days = today + timedelta(days=7)
        date_3_days = today + timedelta(days=3)
        date_1_day = today + timedelta(days=1)
        
        notifications_sent = 0
        
        # Buscar transações com data de validade configurada que estão aprovadas
        transactions_with_expiration = CSTransaction.objects.filter(
            expiration_date__isnull=False,
            status='APPROVED',
            amount__gt=0  # Apenas créditos
        ).select_related('user')
        
        for transaction in transactions_with_expiration:
            user = transaction.user
            exp_date = transaction.expiration_date
            days_remaining = (exp_date - today).days
            
            # Verificar se já expirou
            if days_remaining < 0:
                continue
            
            # Notificação de 7 dias
            if days_remaining == 7 and not transaction.expiration_notified_7_days:
                if not dry_run:
                    self.send_notification(
                        user=user,
                        days=7,
                        amount=transaction.amount,
                        expiration_date=exp_date,
                        transaction=transaction
                    )
                    transaction.expiration_notified_7_days = True
                    transaction.save(update_fields=['expiration_notified_7_days'])
                else:
                    self.stdout.write(f'[DRY-RUN] Notificaria {user.full_name}: 7 dias para expirar C$ {transaction.amount}')
                notifications_sent += 1
            
            # Notificação de 3 dias
            elif days_remaining == 3 and not transaction.expiration_notified_3_days:
                if not dry_run:
                    self.send_notification(
                        user=user,
                        days=3,
                        amount=transaction.amount,
                        expiration_date=exp_date,
                        transaction=transaction
                    )
                    transaction.expiration_notified_3_days = True
                    transaction.save(update_fields=['expiration_notified_3_days'])
                else:
                    self.stdout.write(f'[DRY-RUN] Notificaria {user.full_name}: 3 dias para expirar C$ {transaction.amount}')
                notifications_sent += 1
            
            # Notificação de 1 dia
            elif days_remaining == 1 and not transaction.expiration_notified_1_day:
                if not dry_run:
                    self.send_notification(
                        user=user,
                        days=1,
                        amount=transaction.amount,
                        expiration_date=exp_date,
                        transaction=transaction
                    )
                    transaction.expiration_notified_1_day = True
                    transaction.save(update_fields=['expiration_notified_1_day'])
                else:
                    self.stdout.write(f'[DRY-RUN] Notificaria {user.full_name}: 1 dia para expirar C$ {transaction.amount}')
                notifications_sent += 1
        
        mode = '[DRY-RUN] ' if dry_run else ''
        self.stdout.write(
            self.style.SUCCESS(f'{mode}Total de notificações enviadas: {notifications_sent}')
        )

    def send_notification(self, user, days, amount, expiration_date, transaction):
        """Envia notificação para o usuário sobre validade das confianças"""
        
        if days == 1:
            title = '⚠️ Suas Confianças C$ vencem AMANHÃ!'
            message = f'Você tem C$ {amount} que irão expirar amanhã ({expiration_date.strftime("%d/%m/%Y")}). Use antes que expire!'
            urgency = 'high'
        elif days == 3:
            title = '🔔 Confianças C$ vencendo em 3 dias'
            message = f'Você tem C$ {amount} que irão expirar em 3 dias ({expiration_date.strftime("%d/%m/%Y")}). Não perca!'
            urgency = 'medium'
        else:  # 7 days
            title = '📅 Confianças C$ vencendo em breve'
            message = f'Você tem C$ {amount} que irão expirar em 7 dias ({expiration_date.strftime("%d/%m/%Y")}). Aproveite!'
            urgency = 'low'
        
        # Criar notificação no sistema
        Notification.objects.create(
            user=user,
            title=title,
            message=message,
            notification_type='CS_EXPIRATION',
            url='/prizes/',  # Link para a loja de prêmios
            priority=urgency
        )
        
        # Tentar enviar push notification
        try:
            from notifications.services import send_push_notification
            send_push_notification(
                user=user,
                title=title,
                message=message,
                url='/prizes/',
                tag=f'cs_expiration_{transaction.id}'
            )
        except Exception as e:
            self.stdout.write(
                self.style.WARNING(f'Erro ao enviar push para {user.full_name}: {str(e)}')
            )
        
        self.stdout.write(f'Notificação enviada para {user.full_name}: {days} dias para expirar C$ {amount}')
