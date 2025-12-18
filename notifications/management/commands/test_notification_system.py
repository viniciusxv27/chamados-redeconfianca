"""
Comando para testar o sistema de notificações.
Envia uma notificação de teste para um usuário específico ou para o próprio usuário que está executando.

Uso:
    python manage.py test_notification_system [--user=email] [--type=ticket|communication|all]
"""

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from notifications.services import notification_service, NotificationType, NotificationChannel

User = get_user_model()


class Command(BaseCommand):
    help = 'Testa o sistema de notificações enviando notificações de teste'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user',
            type=str,
            help='Email do usuário para enviar a notificação de teste'
        )
        parser.add_argument(
            '--type',
            type=str,
            default='all',
            choices=['ticket', 'communication', 'all'],
            help='Tipo de notificação para testar'
        )
        parser.add_argument(
            '--channels',
            type=str,
            default='all',
            help='Canais para testar (in_app,push,email ou all)'
        )

    def handle(self, *args, **options):
        user_email = options['user']
        test_type = options['type']
        channels_str = options['channels']
        
        # Encontrar usuário
        if user_email:
            try:
                user = User.objects.get(email=user_email)
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'Usuário com email {user_email} não encontrado'))
                return
        else:
            # Usar primeiro superadmin
            user = User.objects.filter(is_superuser=True, is_active=True).first()
            if not user:
                user = User.objects.filter(is_active=True).first()
            
            if not user:
                self.stdout.write(self.style.ERROR('Nenhum usuário ativo encontrado'))
                return
        
        self.stdout.write(self.style.SUCCESS(f'\nTestando notificações para: {user.email}'))
        self.stdout.write(f'Nome: {user.full_name}')
        self.stdout.write(f'Email configurado: {notification_service.email_enabled}')
        self.stdout.write(f'Push configurado: {notification_service.push_enabled}\n')
        
        # Determinar canais
        if channels_str == 'all':
            channels = [NotificationChannel.IN_APP, NotificationChannel.PUSH, NotificationChannel.EMAIL]
        else:
            channels = [c.strip() for c in channels_str.split(',')]
        
        self.stdout.write(f'Canais selecionados: {channels}\n')
        
        # Testar notificação geral
        if test_type in ['all']:
            self.stdout.write(self.style.MIGRATE_HEADING('=== Testando Notificação Geral ==='))
            result = notification_service.send_notification(
                recipients=user,
                title='🔔 Teste de Notificação',
                message='Esta é uma notificação de teste do sistema Rede Confiança. Se você recebeu esta mensagem, o sistema está funcionando corretamente!',
                notification_type=NotificationType.SYSTEM,
                channels=channels,
                action_url='/notifications/',
                priority='NORMAL',
                icon='fas fa-check-circle',
                respect_preferences=False
            )
            self._print_result(result)
        
        # Testar notificação de ticket
        if test_type in ['ticket', 'all']:
            self.stdout.write(self.style.MIGRATE_HEADING('\n=== Testando Notificação de Ticket ==='))
            result = notification_service.send_notification(
                recipients=user,
                title='🎫 Novo Chamado de Teste #999',
                message='Um novo chamado foi criado no setor de TI.\n\nTítulo: Problema de teste\nPrioridade: Alta\nSetor: TI',
                notification_type=NotificationType.TICKET_CREATED,
                channels=channels,
                action_url='/tickets/999/',
                priority='ALTA',
                icon='fas fa-ticket-alt',
                respect_preferences=False
            )
            self._print_result(result)
        
        # Testar notificação de comunicado
        if test_type in ['communication', 'all']:
            self.stdout.write(self.style.MIGRATE_HEADING('\n=== Testando Notificação de Comunicado ==='))
            result = notification_service.send_notification(
                recipients=user,
                title='📢 Novo Comunicado de Teste',
                message='Este é um comunicado de teste enviado pelo sistema de notificações.',
                notification_type=NotificationType.COMMUNICATION_NEW,
                channels=channels,
                action_url='/communications/',
                priority='NORMAL',
                icon='fas fa-bullhorn',
                respect_preferences=False
            )
            self._print_result(result)
        
        self.stdout.write(self.style.SUCCESS('\n✅ Testes de notificação concluídos!'))
        self.stdout.write('Verifique:')
        self.stdout.write('  - O sino de notificações no sistema')
        self.stdout.write('  - Push notifications no navegador/celular')
        self.stdout.write(f'  - Email em {user.email} (se email estiver habilitado)')
    
    def _print_result(self, result):
        self.stdout.write(f"  Sucesso geral: {result['success']}")
        self.stdout.write(f"  Mensagem: {result.get('message', 'N/A')}")
        self.stdout.write(f"  Destinatários: {result.get('recipients_count', 'N/A')}")
        
        if 'results' in result:
            for channel, channel_result in result['results'].items():
                self.stdout.write(f"\n  Canal {channel.upper()}:")
                self.stdout.write(f"    - Sucesso: {channel_result.get('success', 'N/A')}")
                self.stdout.write(f"    - Enviados: {channel_result.get('sent_count', 'N/A')}")
                if channel_result.get('error'):
                    self.stdout.write(self.style.ERROR(f"    - Erro: {channel_result.get('error')}"))
