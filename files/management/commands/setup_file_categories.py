from django.core.management.base import BaseCommand
from files.models import FileCategory


class Command(BaseCommand):
    help = 'Cria categorias iniciais de arquivos'

    def handle(self, *args, **options):
        categories = [
            {
                'name': 'Documentos Gerais',
                'description': 'Documentos e arquivos gerais da empresa',
                'icon': 'fas fa-file-alt'
            },
            {
                'name': 'Formulários',
                'description': 'Formulários para preenchimento',
                'icon': 'fas fa-clipboard'
            },
            {
                'name': 'Manuais e Procedimentos',
                'description': 'Manuais, procedimentos e guias',
                'icon': 'fas fa-book'
            },
            {
                'name': 'Planilhas',
                'description': 'Planilhas e relatórios',
                'icon': 'fas fa-table'
            },
            {
                'name': 'Apresentações',
                'description': 'Slides e apresentações',
                'icon': 'fas fa-presentation'
            },
            {
                'name': 'Imagens',
                'description': 'Fotos e imagens diversas',
                'icon': 'fas fa-image'
            },
            {
                'name': 'Treinamentos',
                'description': 'Material de treinamento e capacitação',
                'icon': 'fas fa-graduation-cap'
            },
        ]
        
        created_count = 0
        for category_data in categories:
            category, created = FileCategory.objects.get_or_create(
                name=category_data['name'],
                defaults={
                    'description': category_data['description'],
                    'icon': category_data['icon']
                }
            )
            
            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Categoria criada: {category.name}')
                )
            else:
                self.stdout.write(
                    self.style.WARNING(f'- Categoria já existe: {category.name}')
                )
        
        if created_count > 0:
            self.stdout.write(
                self.style.SUCCESS(f'\nTotal de {created_count} categorias criadas com sucesso!')
            )
        else:
            self.stdout.write(
                self.style.WARNING('Nenhuma categoria nova foi criada.')
            )
