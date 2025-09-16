from django.core.management.base import BaseCommand
from django.db import transaction
from users.models import Sector
from projects.models import ProjectSectorAccess


class Command(BaseCommand):
    help = 'Configure initial project access for Intelligence sector'

    def add_arguments(self, parser):
        parser.add_argument(
            '--sector-name',
            type=str,
            default='Inteligência',
            help='Nome do setor que terá acesso aos projetos (padrão: Inteligência)'
        )

    def handle(self, *args, **options):
        sector_name = options['sector_name']
        
        try:
            with transaction.atomic():
                # Buscar ou criar o setor Inteligência
                sector, created = Sector.objects.get_or_create(
                    name__iexact=sector_name,
                    defaults={
                        'name': sector_name,
                        'description': f'Setor de {sector_name} com acesso ao sistema de projetos'
                    }
                )
                
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(f'Setor "{sector_name}" criado com sucesso!')
                    )
                else:
                    self.stdout.write(
                        self.style.WARNING(f'Setor "{sector_name}" já existia.')
                    )
                
                # Configurar acesso ao sistema de projetos
                access, created = ProjectSectorAccess.objects.get_or_create(
                    sector=sector,
                    defaults={
                        'can_view_projects': True,
                        'can_create_projects': True,
                        'can_manage_all_projects': True
                    }
                )
                
                if created:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Acesso aos projetos configurado para o setor "{sector_name}"!'
                        )
                    )
                else:
                    # Atualizar permissões existentes
                    access.can_view_projects = True
                    access.can_create_projects = True
                    access.can_manage_all_projects = True
                    access.save()
                    
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'Permissões de projetos atualizadas para o setor "{sector_name}"!'
                        )
                    )
                
                # Exibir resumo das permissões
                self.stdout.write('\n' + '='*50)
                self.stdout.write(self.style.SUCCESS('CONFIGURAÇÃO CONCLUÍDA'))
                self.stdout.write('='*50)
                self.stdout.write(f'Setor: {sector.name}')
                self.stdout.write(f'ID: {sector.id}')
                self.stdout.write(f'Pode ver projetos: {access.can_view_projects}')
                self.stdout.write(f'Pode criar projetos: {access.can_create_projects}')
                self.stdout.write(f'Pode gerenciar todos os projetos: {access.can_manage_all_projects}')
                self.stdout.write('='*50)
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Erro ao configurar acesso aos projetos: {str(e)}')
            )
            raise e