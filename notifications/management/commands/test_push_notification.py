from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from notifications.models import PushNotification, NotificationCategory, DeviceToken
import json

User = get_user_model()


class Command(BaseCommand):
    help = 'Test push notification system'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='User ID to send test notification to',
        )
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='Send test notification to all users',
        )

    def handle(self, *args, **options):
        try:
            # Criar notificação de teste
            notification = PushNotification.objects.create(
                title="Teste de Push Notification",
                message="Esta é uma notificação de teste para verificar se o sistema está funcionando corretamente.",
                notification_type='CUSTOM',
                priority='NORMAL',
                send_to_all=options.get('all_users', False),
                created_by=User.objects.filter(is_superuser=True).first()
            )
            
            # Se especificou user_id, adicionar como target
            if options.get('user_id'):
                try:
                    user = User.objects.get(id=options['user_id'])
                    notification.target_users.add(user)
                    self.stdout.write(f'Notification will be sent to user: {user.get_full_name()}')
                except User.DoesNotExist:
                    self.stdout.write(
                        self.style.ERROR(f'User with ID {options["user_id"]} not found')
                    )
                    return
            
            # Verificar se há tokens de dispositivo
            if options.get('all_users'):
                device_count = DeviceToken.objects.filter(is_active=True).count()
                user_count = User.objects.filter(is_active=True).count()
            elif options.get('user_id'):
                device_count = DeviceToken.objects.filter(
                    user_id=options['user_id'], 
                    is_active=True
                ).count()
                user_count = 1
            else:
                self.stdout.write(
                    self.style.WARNING('Specify either --user-id or --all-users')
                )
                return
            
            self.stdout.write(f'Target users: {user_count}')
            self.stdout.write(f'Active device tokens: {device_count}')
            
            if device_count == 0:
                self.stdout.write(
                    self.style.WARNING('No active device tokens found. Users need to enable push notifications first.')
                )
            
            # Enviar notificação
            self.stdout.write('Sending notification...')
            success = notification.send_notification()
            
            if success:
                self.stdout.write(
                    self.style.SUCCESS(f'Test notification sent successfully! ID: {notification.id}')
                )
                self.stdout.write(f'Check the logs for push notification delivery status.')
            else:
                self.stdout.write(
                    self.style.ERROR('Failed to send notification')
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error sending test notification: {str(e)}')
            )