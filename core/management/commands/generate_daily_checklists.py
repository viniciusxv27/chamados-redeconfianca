"""
Comando para gerar checklists administrativos diários automaticamente
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from datetime import date, timedelta
from core.models import AdminChecklistTemplate, DailyAdminChecklist, AdminChecklistTask
from users.models import User


class Command(BaseCommand):
    help = 'Gera checklists administrativos diários baseados nos templates ativos'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Data específica para gerar o checklist (formato: YYYY-MM-DD). Se não fornecida, usa a data atual.'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Força a criação mesmo se já existir checklist para a data'
        )
        parser.add_argument(
            '--days',
            type=int,
            default=1,
            help='Número de dias para gerar checklists (padrão: 1)'
        )

    def handle(self, *args, **options):
        # Definir data inicial
        if options['date']:
            try:
                start_date = date.fromisoformat(options['date'])
            except ValueError:
                raise CommandError('Data inválida. Use o formato YYYY-MM-DD')
        else:
            start_date = date.today()

        # Pegar superuser para criar os checklists
        superuser = User.objects.filter(is_superuser=True).first()
        if not superuser:
            raise CommandError('Nenhum superuser encontrado para criar os checklists')

        # Pegar templates ativos
        active_templates = AdminChecklistTemplate.objects.filter(is_active=True)
        if not active_templates.exists():
            self.stdout.write(
                self.style.WARNING('Nenhum template ativo encontrado. Nenhum checklist será criado.')
            )
            return

        # Gerar checklists para os dias especificados
        created_count = 0
        updated_count = 0
        
        for i in range(options['days']):
            current_date = start_date + timedelta(days=i)
            
            # Verificar se já existe checklist para esta data
            existing_checklist = DailyAdminChecklist.objects.filter(date=current_date).first()
            
            if existing_checklist and not options['force']:
                self.stdout.write(
                    self.style.WARNING(f'Checklist já existe para {current_date}. Use --force para sobrescrever.')
                )
                continue
            
            # Criar ou atualizar checklist
            if existing_checklist and options['force']:
                # Remover tarefas existentes
                existing_checklist.tasks.all().delete()
                daily_checklist = existing_checklist
                updated_count += 1
                action = 'atualizado'
            else:
                # Criar novo checklist
                daily_checklist = DailyAdminChecklist.objects.create(
                    date=current_date,
                    created_by=superuser
                )
                created_count += 1
                action = 'criado'
            
            # Criar tarefas baseadas nos templates
            tasks_created = 0
            for template in active_templates:
                AdminChecklistTask.objects.create(
                    checklist=daily_checklist,
                    template=template,
                    status='PENDING'
                )
                tasks_created += 1
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Checklist {action} para {current_date} com {tasks_created} tarefas'
                )
            )
        
        # Resumo final
        total_actions = created_count + updated_count
        if total_actions > 0:
            summary_parts = []
            if created_count > 0:
                summary_parts.append(f'{created_count} checklist(s) criado(s)')
            if updated_count > 0:
                summary_parts.append(f'{updated_count} checklist(s) atualizado(s)')
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Processo concluído: {", ".join(summary_parts)}'
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING('Nenhum checklist foi processado.')
            )