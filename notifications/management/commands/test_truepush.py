"""
Comando de gerenciamento para testar o envio de notificações via Truepush.

Uso:
    python manage.py test_truepush
    python manage.py test_truepush --title "Título" --message "Mensagem"
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Testa o envio de notificações via Truepush'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--title',
            type=str,
            default='Teste Truepush',
            help='Título da notificação de teste'
        )
        parser.add_argument(
            '--message',
            type=str,
            default='Esta é uma notificação de teste do sistema Rede Confiança.',
            help='Mensagem da notificação de teste'
        )
        parser.add_argument(
            '--url',
            type=str,
            default='/',
            help='URL de destino ao clicar na notificação'
        )
    
    def handle(self, *args, **options):
        from notifications.truepush_service import truepush_service
        
        self.stdout.write(self.style.WARNING('Verificando configuração do Truepush...'))
        
        if not truepush_service.enabled:
            raise CommandError(
                'Truepush não está configurado!\n'
                'Defina as seguintes variáveis de ambiente:\n'
                '  - TRUEPUSH_API_KEY\n'
                '  - TRUEPUSH_PROJECT_ID'
            )
        
        self.stdout.write(self.style.SUCCESS('✓ Truepush configurado'))
        self.stdout.write(f'  Project ID: {truepush_service.project_id}')
        
        title = options['title']
        message = options['message']
        url = options['url']
        
        self.stdout.write(self.style.WARNING('\nEnviando notificação de teste...'))
        self.stdout.write(f'  Título: {title}')
        self.stdout.write(f'  Mensagem: {message}')
        self.stdout.write(f'  URL: {url}')
        
        result = truepush_service.send_to_all(
            title=title,
            message=message,
            url=url
        )
        
        if result.get('success'):
            self.stdout.write(self.style.SUCCESS('\n✓ Notificação enviada com sucesso!'))
            self.stdout.write(f'  Enviado para: {result.get("sent_count", "?")} assinantes')
            if result.get('response'):
                self.stdout.write(f'  Resposta: {result.get("response")}')
        else:
            self.stdout.write(self.style.ERROR('\n✗ Erro ao enviar notificação!'))
            self.stdout.write(f'  Erro: {result.get("error", "Erro desconhecido")}')
        
        # Testar obtenção de estatísticas
        self.stdout.write(self.style.WARNING('\nObtendo estatísticas...'))
        
        stats_result = truepush_service.get_subscriber_count()
        if stats_result.get('success'):
            self.stdout.write(self.style.SUCCESS(f'✓ Total de assinantes: {stats_result.get("count", 0)}'))
        else:
            self.stdout.write(self.style.WARNING(f'⚠ Não foi possível obter estatísticas: {stats_result.get("error")}'))
