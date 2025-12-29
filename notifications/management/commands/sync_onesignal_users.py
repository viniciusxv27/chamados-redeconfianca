"""
Management command to sync active users with OneSignal as external_user_ids
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = 'Sync active users with OneSignal external_user_ids'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be synced without making changes'
        )
        parser.add_argument(
            '--hierarchy',
            type=str,
            help='Filter by hierarchy (SUPERADMIN, ADMIN, GESTOR, COORDENADOR, COLABORADOR)'
        )

    def handle(self, *args, **options):
        from notifications.onesignal_service import onesignal_service
        from notifications.models import OneSignalPlayer
        
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        self.stdout.write(self.style.HTTP_INFO('OneSignal - Sync Active Users'))
        self.stdout.write(self.style.HTTP_INFO('=' * 60))
        
        if not onesignal_service.enabled:
            self.stdout.write(self.style.ERROR(
                '\n⚠ OneSignal não está configurado!'
                '\nAdicione ONESIGNAL_APP_ID e ONESIGNAL_REST_API_KEY ao .env'
            ))
            return
        
        # Get active users
        queryset = User.objects.filter(is_active=True)
        
        if options['hierarchy']:
            queryset = queryset.filter(hierarchy=options['hierarchy'])
        
        users = queryset.order_by('full_name')
        total = users.count()
        
        self.stdout.write(f"\nUsuários ativos encontrados: {total}")
        
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('\n[DRY RUN] Mostrando o que seria sincronizado:\n'))
        
        synced = 0
        already_exists = 0
        errors = 0
        
        for user in users:
            external_id = str(user.id)
            tags = {
                'user_id': str(user.id),
                'email': user.email or '',
                'full_name': user.full_name or '',
                'hierarchy': user.hierarchy or '',
                'sector': user.sector.name if user.sector else '',
                'is_active': 'true'
            }
            
            # Check if already registered locally
            existing = OneSignalPlayer.objects.filter(
                user=user,
                is_active=True
            ).first()
            
            if existing:
                already_exists += 1
                if not options['dry_run']:
                    self.stdout.write(f"  ✓ {user.full_name} - já registrado (Player: {existing.player_id[:20]}...)")
                continue
            
            if options['dry_run']:
                self.stdout.write(f"  → {user.full_name} ({user.email}) - external_id: {external_id}")
                self.stdout.write(f"    Tags: hierarchy={tags['hierarchy']}, sector={tags['sector']}")
                synced += 1
            else:
                # Create a placeholder record for tracking
                # The actual player_id will be set when user subscribes via browser
                try:
                    OneSignalPlayer.objects.create(
                        user=user,
                        player_id=f"pending_{external_id}",
                        device_type='pending',
                        extra_data={
                            'external_user_id': external_id,
                            'tags': tags,
                            'status': 'awaiting_browser_subscription'
                        }
                    )
                    synced += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"  ✓ {user.full_name} - preparado para assinatura (external_id: {external_id})"
                    ))
                except Exception as e:
                    errors += 1
                    self.stdout.write(self.style.ERROR(f"  ✗ {user.full_name} - erro: {e}"))
        
        self.stdout.write(self.style.HTTP_INFO('\n' + '-' * 60))
        self.stdout.write(f"Total de usuários: {total}")
        self.stdout.write(f"Já registrados: {already_exists}")
        self.stdout.write(f"{'Seriam sincronizados' if options['dry_run'] else 'Sincronizados'}: {synced}")
        if errors:
            self.stdout.write(self.style.ERROR(f"Erros: {errors}"))
        
        self.stdout.write(self.style.HTTP_INFO('\n' + '=' * 60))
        
        if not options['dry_run']:
            self.stdout.write(self.style.SUCCESS(
                '\n✓ Sincronização concluída!'
                '\n\nNota: Os usuários precisam acessar o sistema e aceitar notificações'
                '\npara que o OneSignal associe o player_id ao external_user_id.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                '\n[DRY RUN] Execute sem --dry-run para aplicar as mudanças'
            ))
