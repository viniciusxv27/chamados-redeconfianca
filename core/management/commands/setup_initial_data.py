from django.core.management.base import BaseCommand
from users.models import User, Sector
from tickets.models import Category
from prizes.models import Prize
from decimal import Decimal


class Command(BaseCommand):
    help = 'Criar dados iniciais para teste do sistema'

    def handle(self, *args, **options):
        self.stdout.write('Criando dados iniciais...')
        
        # Criar setores
        setores = [
            {'name': 'Tecnologia da Informação', 'description': 'Setor responsável por TI e sistemas'},
            {'name': 'Recursos Humanos', 'description': 'Gestão de pessoas e processos de RH'},
            {'name': 'Financeiro', 'description': 'Controladoria e finanças'},
            {'name': 'Compras', 'description': 'Procurement e aquisições'},
            {'name': 'Estrutural', 'description': 'Infraestrutura e facilities'},
            {'name': 'Comercial', 'description': 'Vendas e relacionamento com clientes'},
        ]
        
        for setor_data in setores:
            setor, created = Sector.objects.get_or_create(
                name=setor_data['name'],
                defaults={'description': setor_data['description']}
            )
            if created:
                self.stdout.write(f'Setor criado: {setor.name}')
        
        # Criar categorias para cada setor
        categorias = {
            'Tecnologia da Informação': [
                {'name': 'Suporte Técnico', 'webhook_url': '', 'requires_approval': False, 'default_description': 'Descreva o problema técnico que está enfrentando...'},
                {'name': 'Desenvolvimento', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Descreva a funcionalidade ou correção necessária...'},
                {'name': 'Infraestrutura', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Descreva a necessidade de infraestrutura...'},
            ],
            'Recursos Humanos': [
                {'name': 'Folha de Pagamento', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Descreva a questão relacionada à folha de pagamento...'},
                {'name': 'Benefícios', 'webhook_url': '', 'requires_approval': False, 'default_description': 'Descreva sua dúvida ou solicitação sobre benefícios...'},
                {'name': 'Recrutamento', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Descreva a vaga ou necessidade de contratação...'},
            ],
            'Financeiro': [
                {'name': 'Reembolso', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Prezados, solicito reembolso de R$ [VALOR] referente a [DESCRIÇÃO]. Anexo comprovantes.'},
                {'name': 'Contas a Pagar', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Solicitação de pagamento para fornecedor...'},
                {'name': 'Orçamento', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Solicitação de verba/orçamento para...'},
            ],
            'Compras': [
                {'name': 'Cotação', 'webhook_url': '', 'requires_approval': False, 'default_description': 'Solicito cotação para os seguintes itens...'},
                {'name': 'Ordem de Compra', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Solicito autorização para compra de...'},
                {'name': 'Recebimento', 'webhook_url': '', 'requires_approval': False, 'default_description': 'Confirmação de recebimento de materiais...'},
            ],
            'Estrutural': [
                {'name': 'Manutenção', 'webhook_url': '', 'requires_approval': False, 'default_description': 'Solicito manutenção/reparo em...'},
                {'name': 'Limpeza', 'webhook_url': '', 'requires_approval': False, 'default_description': 'Solicitação de limpeza especial em...'},
                {'name': 'Segurança', 'webhook_url': '', 'requires_approval': True, 'default_description': 'Questão relacionada à segurança do ambiente...'},
            ],
        }
        
        for setor_name, cats in categorias.items():
            try:
                setor = Sector.objects.get(name=setor_name)
                for cat_data in cats:
                    categoria, created = Category.objects.get_or_create(
                        sector=setor,
                        name=cat_data['name'],
                        defaults={
                            'webhook_url': cat_data['webhook_url'],
                            'requires_approval': cat_data['requires_approval'],
                            'default_description': cat_data['default_description']
                        }
                    )
                    if created:
                        self.stdout.write(f'Categoria criada: {setor_name} - {categoria.name}')
            except Sector.DoesNotExist:
                continue
        
        # Criar usuários de exemplo
        usuarios = [
            {
                'email': 'leo.teixeira@redeconfianca.com',
                'username': 'leo.teixeira',
                'first_name': 'Léo',
                'last_name': 'Teixeira',
                'hierarchy': 'SUPERVISOR',
                'balance_cs': Decimal('20000.00'),
                'phone': '(11) 99999-0001'
            },
            {
                'email': 'jose.silva@redeconfianca.com', 
                'username': 'jose.silva',
                'first_name': 'José',
                'last_name': 'Silva',
                'hierarchy': 'PADRAO',
                'balance_cs': Decimal('5000.00'),
                'phone': '(11) 99999-0002'
            },
            {
                'email': 'maria.santos@redeconfianca.com',
                'username': 'maria.santos', 
                'first_name': 'Maria',
                'last_name': 'Santos',
                'hierarchy': 'ADMINISTRATIVO',
                'balance_cs': Decimal('15000.00'),
                'phone': '(11) 99999-0003'
            },
            {
                'email': 'gabriel.oliveira@redeconfianca.com',
                'username': 'gabriel.oliveira',
                'first_name': 'Gabriel',
                'last_name': 'Oliveira', 
                'hierarchy': 'PADRAO',
                'balance_cs': Decimal('3000.00'),
                'phone': '(11) 99999-0004'
            },
            {
                'email': 'rayra.pianesola@redeconfianca.com',
                'username': 'rayra.pianesola',
                'first_name': 'Rayra',
                'last_name': 'Pianesola',
                'hierarchy': 'SUPERVISOR', 
                'balance_cs': Decimal('12000.00'),
                'phone': '(11) 99999-0005'
            }
        ]
        
        ti_setor = Sector.objects.get(name='Tecnologia da Informação')
        rh_setor = Sector.objects.get(name='Recursos Humanos')
        fin_setor = Sector.objects.get(name='Financeiro')
        
        for i, user_data in enumerate(usuarios):
            # Distribuir usuários entre setores
            if i < 2:
                user_data['sector'] = ti_setor
            elif i < 4:
                user_data['sector'] = rh_setor 
            else:
                user_data['sector'] = fin_setor
                
            user, created = User.objects.get_or_create(
                email=user_data['email'],
                defaults=user_data
            )
            if created:
                user.set_password('redeconfianca123')
                user.save()
                self.stdout.write(f'Usuário criado: {user.email}')
        
        # Criar prêmios de exemplo
        premios = [
            {
                'name': 'Vale Combustível R$ 50',
                'description': 'Vale combustível no valor de R$ 50,00 para uso em postos conveniados',
                'value_cs': Decimal('5000.00'),
                'stock': 10,
                'unlimited_stock': False
            },
            {
                'name': 'Folga Extra',
                'description': 'Um dia de folga extra para usar quando quiser',
                'value_cs': Decimal('8000.00'),
                'stock': 5,
                'unlimited_stock': False
            },
            {
                'name': 'Almoço no Restaurante',
                'description': 'Almoço especial no restaurante da empresa',
                'value_cs': Decimal('3000.00'),
                'stock': 0,
                'unlimited_stock': True
            },
            {
                'name': 'Kit Home Office',
                'description': 'Kit com mouse, teclado e mousepad para home office',
                'value_cs': Decimal('12000.00'),
                'stock': 3,
                'unlimited_stock': False
            }
        ]
        
        for premio_data in premios:
            premio, created = Prize.objects.get_or_create(
                name=premio_data['name'],
                defaults=premio_data
            )
            if created:
                self.stdout.write(f'Prêmio criado: {premio.name}')
        
        self.stdout.write(self.style.SUCCESS('Dados iniciais criados com sucesso!'))
        self.stdout.write('')
        self.stdout.write('Usuários criados:')
        for user_data in usuarios:
            self.stdout.write(f'  - {user_data["email"]} (senha: redeconfianca123)')
        self.stdout.write('')
        self.stdout.write('Acesse: http://127.0.0.1:8000/login/')
