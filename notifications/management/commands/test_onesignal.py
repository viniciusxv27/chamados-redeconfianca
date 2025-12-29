"""
Management command to test OneSignal integration
"""
from django.core.management.base import BaseCommand
from django.conf import settings


class Command(BaseCommand):
    help = 'Test OneSignal integration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--send',
            action='store_true',
            help='Send a test notification'
        )
        parser.add_argument(
            '--title',
            type=str,
            default='Teste OneSignal',
            help='Notification title'
        )
        parser.add_argument(
            '--message',
            type=str,
            default='Esta é uma notificação de teste do sistema.',
            help='Notification message'
        )

    def handle(self, *args, **options):
        from notifications.onesignal_service import onesignal_service
        
        self.stdout.write(self.style.HTTP_INFO('=' * 50))
        self.stdout.write(self.style.HTTP_INFO('OneSignal Integration Test'))
        self.stdout.write(self.style.HTTP_INFO('=' * 50))
        
        # Check configuration
        app_id = getattr(settings, 'ONESIGNAL_APP_ID', '')
        rest_api_key = getattr(settings, 'ONESIGNAL_REST_API_KEY', '')
        
        self.stdout.write(f"\nApp ID: {'✓ Configurado' if app_id else '✗ Não configurado'}")
        self.stdout.write(f"REST API Key: {'✓ Configurado' if rest_api_key else '✗ Não configurado'}")
        self.stdout.write(f"Service Enabled: {'✓ Sim' if onesignal_service.enabled else '✗ Não'}")
        
        if not onesignal_service.enabled:
            self.stdout.write(self.style.ERROR(
                '\n⚠ OneSignal não está configurado!'
                '\nAdicione as seguintes variáveis ao seu .env:'
                '\n  ONESIGNAL_APP_ID=seu-app-id'
                '\n  ONESIGNAL_REST_API_KEY=sua-rest-api-key'
            ))
            return
        
        # Get app info
        self.stdout.write(self.style.HTTP_INFO('\n--- App Info ---'))
        app_info = onesignal_service.get_app_info()
        if app_info.get('success'):
            info = app_info.get('app_info', {})
            self.stdout.write(f"App Name: {info.get('name', 'N/A')}")
            self.stdout.write(f"Players: {info.get('players', 'N/A')}")
            self.stdout.write(f"Messageable Players: {info.get('messageable_players', 'N/A')}")
        else:
            self.stdout.write(self.style.ERROR(f"Erro: {app_info.get('error', 'Unknown')}"))
        
        # Get player count
        self.stdout.write(self.style.HTTP_INFO('\n--- Player Count ---'))
        count_result = onesignal_service.get_player_count()
        if count_result.get('success'):
            self.stdout.write(f"Total Players: {count_result.get('count', 0)}")
        else:
            self.stdout.write(self.style.ERROR(f"Erro: {count_result.get('error', 'Unknown')}"))
        
        # Get segments
        self.stdout.write(self.style.HTTP_INFO('\n--- Segments ---'))
        segments_result = onesignal_service.get_segments()
        if segments_result.get('success'):
            segments = segments_result.get('segments', [])
            if segments:
                for seg in segments:
                    self.stdout.write(f"  • {seg.get('name', 'N/A')}")
            else:
                self.stdout.write("  Nenhum segmento encontrado")
        else:
            self.stdout.write(self.style.ERROR(f"Erro: {segments_result.get('error', 'Unknown')}"))
        
        # Send test notification if requested
        if options['send']:
            self.stdout.write(self.style.HTTP_INFO('\n--- Sending Test Notification ---'))
            
            result = onesignal_service.send_to_all(
                title=options['title'],
                message=options['message'],
                url='/'
            )
            
            if result.get('success'):
                self.stdout.write(self.style.SUCCESS(
                    f"\n✓ Notificação enviada com sucesso!"
                    f"\n  ID: {result.get('notification_id', 'N/A')}"
                    f"\n  Recipients: {result.get('recipients', 0)}"
                ))
            else:
                self.stdout.write(self.style.ERROR(
                    f"\n✗ Erro ao enviar notificação: {result.get('error', 'Unknown')}"
                ))
        
        self.stdout.write(self.style.HTTP_INFO('\n' + '=' * 50))
        self.stdout.write(self.style.SUCCESS('Teste concluído!'))
