from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Sum
from decimal import Decimal
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import User, Sector
from .serializers import UserSerializer, SectorSerializer
from core.middleware import log_action
import json


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            log_action(user, 'USER_LOGIN', f'Login realizado: {email}', request)
            return redirect('dashboard')
        else:
            messages.error(request, 'Email ou senha inválidos.')
    
    return render(request, 'users/login.html')


@login_required
def logout_view(request):
    user = request.user
    log_action(user, 'USER_LOGOUT', f'Logout realizado: {user.email}', request)
    logout(request)
    return redirect('login')


@login_required
def dashboard_view(request):
    from tickets.models import Ticket
    from communications.models import Communication
    from django.db.models import Q, Count
    from django.utils import timezone
    
    user = request.user
    
    # Estatísticas de tickets
    if user.can_view_all_tickets():
        user_tickets = Ticket.objects.all()
    elif user.can_view_sector_tickets():
        user_tickets = Ticket.objects.filter(sector=user.sector)
    else:
        user_tickets = Ticket.objects.filter(created_by=user)
    
    ticket_stats = {
        'total': user_tickets.count(),
        'abertos': user_tickets.filter(status='ABERTO').count(),
        'em_andamento': user_tickets.filter(status='EM_ANDAMENTO').count(),
        'aguardando_aprovacao': user_tickets.filter(status='AGUARDANDO_APROVACAO').count(),
        'resolvidos': user_tickets.filter(status='RESOLVIDO').count(),
        'fechados': user_tickets.filter(status='FECHADO').count(),
        'overdue': user_tickets.filter(due_date__lt=timezone.now(), status__in=['ABERTO', 'EM_ANDAMENTO']).count(),
    }
    
    # Comunicados não lidos (apenas os ativos)
    now = timezone.now()
    
    unread_communications = Communication.objects.filter(
        Q(recipients=user) | Q(send_to_all=True)
    ).filter(
        Q(active_from__isnull=True) | Q(active_from__lte=now)
    ).filter(
        Q(active_until__isnull=True) | Q(active_until__gte=now)
    ).exclude(
        communicationread__user=user
    ).distinct()[:5]
    
    # Tickets recentes
    recent_tickets = user_tickets.order_by('-created_at')[:5]
    
    # Tickets em atraso que o usuário pode ver
    overdue_tickets = user_tickets.filter(
        due_date__lt=timezone.now(),
        status__in=['ABERTO', 'EM_ANDAMENTO']
    ).order_by('due_date')[:5]
    
    context = {
        'user': user,
        'user_balance': user.balance_cs,
        'ticket_stats': ticket_stats,
        'unread_communications': unread_communications,
        'recent_tickets': recent_tickets,
        'overdue_tickets': overdue_tickets,
    }
    return render(request, 'dashboard.html', context)


@login_required
def admin_panel_view(request):
    """Painel administrativo para superadmin/administrativo"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    context = {
        'total_users': User.objects.count(),
        'total_sectors': Sector.objects.count(),
        'recent_users': User.objects.order_by('-created_at')[:5],
        'user': request.user,
    }
    return render(request, 'admin/panel.html', context)


@login_required
def manage_users_view(request):
    """Gerenciar usuários"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    users = User.objects.all().select_related('sector')
    context = {
        'users': users,
        'sectors': Sector.objects.all(),
        'user': request.user,
    }
    return render(request, 'admin/users.html', context)


@login_required
def create_user_view(request):
    """Criar novo usuário"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        email = request.POST.get('email')
        username = request.POST.get('username')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        password = request.POST.get('password')
        sector_id = request.POST.get('sector')
        hierarchy = request.POST.get('hierarchy')
        phone = request.POST.get('phone')
        
        try:
            # Verificar se email já existe
            if User.objects.filter(email=email).exists():
                messages.error(request, 'Email já existe.')
                return render(request, 'admin/create_user.html', {'sectors': Sector.objects.all()})
            
            # Verificar se username já existe
            if User.objects.filter(username=username).exists():
                messages.error(request, 'Nome de usuário já existe.')
                return render(request, 'admin/create_user.html', {'sectors': Sector.objects.all()})
            
            sector = get_object_or_404(Sector, id=sector_id) if sector_id else None
            
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                sector=sector,
                hierarchy=hierarchy,
                phone=phone
            )
            
            log_action(
                request.user, 
                'USER_CREATE', 
                f'Usuário criado: {user.full_name} ({user.email})',
                request
            )
            
            messages.success(request, f'Usuário {user.full_name} criado com sucesso!')
            return redirect('manage_users')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar usuário: {str(e)}')
    
    context = {
        'sectors': Sector.objects.all(),
        'hierarchy_choices': User.HIERARCHY_CHOICES,
        'user': request.user,
    }
    return render(request, 'admin/create_user.html', context)


@login_required
def edit_user_view(request, user_id):
    """Editar usuário existente"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    user_to_edit = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        email = request.POST.get('email')
        username = request.POST.get('username')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        sector_id = request.POST.get('sector')
        hierarchy = request.POST.get('hierarchy')
        phone = request.POST.get('phone')
        is_active = request.POST.get('is_active') == 'on'
        
        try:
            # Verificar se email já existe (exceto para o próprio usuário)
            if User.objects.filter(email=email).exclude(id=user_id).exists():
                messages.error(request, 'Email já existe.')
            else:
                sector = get_object_or_404(Sector, id=sector_id) if sector_id else None
                
                user_to_edit.email = email
                user_to_edit.username = username
                user_to_edit.first_name = first_name
                user_to_edit.last_name = last_name
                user_to_edit.sector = sector
                user_to_edit.hierarchy = hierarchy
                user_to_edit.phone = phone
                user_to_edit.is_active = is_active
                user_to_edit.save()
                
                log_action(
                    request.user, 
                    'USER_UPDATE', 
                    f'Usuário editado: {user_to_edit.full_name} ({user_to_edit.email})',
                    request
                )
                
                messages.success(request, f'Usuário {user_to_edit.full_name} atualizado com sucesso!')
                return redirect('manage_users')
                
        except Exception as e:
            messages.error(request, f'Erro ao atualizar usuário: {str(e)}')
    
    context = {
        'user_to_edit': user_to_edit,
        'sectors': Sector.objects.all(),
        'hierarchy_choices': User.HIERARCHY_CHOICES,
        'user': request.user,
    }
    return render(request, 'admin/edit_user.html', context)


@login_required
def manage_cs_view(request):
    """Gerenciar Confianças C$"""
    if not request.user.can_manage_cs():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    users = User.objects.all().select_related('sector')
    # Calcular total em circulação corretamente
    total_circulation = User.objects.aggregate(
        total=Sum('balance_cs')
    )['total'] or Decimal('0')
    
    context = {
        'users': users,
        'user': request.user,
        'total_circulation': total_circulation,
    }
    return render(request, 'admin/manage_cs.html', context)


@login_required 
def add_cs_view(request, user_id):
    """Adicionar C$ para usuário"""
    if not request.user.can_manage_cs():
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('dashboard')
    
    target_user = get_object_or_404(User, id=user_id)
    
    if request.method == 'POST':
        amount = request.POST.get('amount')
        description = request.POST.get('description', '')
        
        try:
            amount = float(amount)
            if amount <= 0:
                messages.error(request, 'Valor deve ser maior que zero.')
                return redirect('manage_cs')
            
            target_user.balance_cs += Decimal(str(amount))
            target_user.save()
            
            # Registrar transação
            from prizes.models import CSTransaction
            CSTransaction.objects.create(
                user=target_user,
                amount=amount,
                transaction_type='CREDIT',
                description=description or f'Adição manual de C$',
                created_by=request.user
            )
            
            log_action(
                request.user, 
                'CS_ADD', 
                f'Adicionado C$ {amount} para {target_user.full_name}',
                request
            )
            
            messages.success(request, f'C$ {amount} adicionado para {target_user.full_name}!')
            
        except ValueError:
            messages.error(request, 'Valor inválido.')
        except Exception as e:
            messages.error(request, f'Erro: {str(e)}')
    
    return redirect('manage_cs')


@login_required
def manage_sectors_view(request):
    """Gerenciar setores"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    sectors = Sector.objects.all()
    context = {
        'sectors': sectors,
        'user': request.user,
    }
    return render(request, 'admin/sectors.html', context)


@login_required
def create_sector_view(request):
    """Criar novo setor"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        
        try:
            if Sector.objects.filter(name=name).exists():
                messages.error(request, 'Setor com este nome já existe.')
            else:
                sector = Sector.objects.create(
                    name=name,
                    description=description
                )
                
                log_action(
                    request.user, 
                    'SECTOR_CREATE', 
                    f'Setor criado: {sector.name}',
                    request
                )
                
                messages.success(request, f'Setor {sector.name} criado com sucesso!')
                return redirect('manage_sectors')
                
        except Exception as e:
            messages.error(request, f'Erro ao criar setor: {str(e)}')
    
    context = {
        'user': request.user,
    }
    return render(request, 'admin/create_sector.html', context)


@login_required
def manage_categories_view(request):
    """Gerenciar categorias"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    from tickets.models import Category
    categories = Category.objects.all().select_related('sector')
    context = {
        'categories': categories,
        'sectors': Sector.objects.all(),
        'user': request.user,
    }
    return render(request, 'admin/categories.html', context)


@login_required
def create_category_view(request):
    """Criar nova categoria"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        sector_id = request.POST.get('sector')
        default_description = request.POST.get('default_description', '')
        webhook_url = request.POST.get('webhook_url', '')
        requires_approval = request.POST.get('requires_approval') == 'on'
        
        try:
            sector = get_object_or_404(Sector, id=sector_id)
            
            from tickets.models import Category
            if Category.objects.filter(name=name, sector=sector).exists():
                messages.error(request, 'Categoria com este nome já existe neste setor.')
            else:
                category = Category.objects.create(
                    name=name,
                    sector=sector,
                    default_description=default_description,
                    webhook_url=webhook_url,
                    requires_approval=requires_approval
                )
                
                log_action(
                    request.user, 
                    'CATEGORY_CREATE', 
                    f'Categoria criada: {category.name} - {category.sector.name}',
                    request
                )
                
                messages.success(request, f'Categoria {category.name} criada com sucesso!')
                return redirect('manage_categories')
                
        except Exception as e:
            messages.error(request, f'Erro ao criar categoria: {str(e)}')
    
    context = {
        'sectors': Sector.objects.all(),
        'user': request.user,
    }
    return render(request, 'admin/create_category.html', context)


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.hierarchy == 'SUPERADMIN':
            return User.objects.all()
        elif user.hierarchy in ['ADMINISTRATIVO', 'SUPERVISOR']:
            return User.objects.filter(sector=user.sector)
        else:
            return User.objects.filter(id=user.id)
    
    @action(detail=True, methods=['post'])
    def update_balance(self, request, pk=None):
        user = self.get_object()
        target_user = request.user
        
        if not target_user.can_manage_cs():
            return Response(
                {'error': 'Você não tem permissão para alterar saldo C$'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        amount = request.data.get('amount')
        operation = request.data.get('operation')  # 'add' or 'subtract'
        description = request.data.get('description', '')
        
        if not amount or not operation:
            return Response(
                {'error': 'Campos obrigatórios: amount, operation'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            amount = float(amount)
            if operation == 'add':
                user.balance_cs += amount
            elif operation == 'subtract':
                user.balance_cs -= amount
            else:
                return Response(
                    {'error': 'Operação inválida'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            user.save()
            
            # Registrar transação
            from prizes.models import CSTransaction
            CSTransaction.objects.create(
                user=user,
                amount=amount,
                transaction_type='CREDIT' if operation == 'add' else 'DEBIT',
                description=description or f'Ajuste manual - {operation}',
                created_by=target_user
            )
            
            log_action(
                target_user, 
                'CS_CHANGE', 
                f'Alteração de C$ para {user.full_name}: {operation} C$ {amount}',
                request
            )
            
            return Response({'message': 'Saldo atualizado com sucesso'})
            
        except ValueError:
            return Response(
                {'error': 'Valor inválido'}, 
                status=status.HTTP_400_BAD_REQUEST
            )


class SectorViewSet(viewsets.ModelViewSet):
    queryset = Sector.objects.all()
    serializer_class = SectorSerializer
    permission_classes = [IsAuthenticated]


@login_required
def manage_webhooks_view(request):
    """Gerenciar webhooks"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    from tickets.models import Webhook
    webhooks = Webhook.objects.all()
    
    context = {
        'webhooks': webhooks,
        'user': request.user,
    }
    return render(request, 'admin/webhooks.html', context)


@login_required
def create_webhook_view(request):
    """Criar novo webhook"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        from tickets.models import Webhook
        
        name = request.POST.get('name')
        url = request.POST.get('url')
        events = request.POST.getlist('events')
        is_active = request.POST.get('is_active') == 'on'
        
        try:
            webhook = Webhook.objects.create(
                name=name,
                url=url,
                events=events,
                is_active=is_active
            )
            
            log_action(request.user, 'WEBHOOK_CREATE', f'Webhook criado: {webhook.name}', request)
            messages.success(request, f'Webhook "{webhook.name}" criado com sucesso!')
            return redirect('manage_webhooks')
        except Exception as e:
            messages.error(request, f'Erro ao criar webhook: {str(e)}')
    
    context = {
        'user': request.user,
        'event_choices': [
            ('ticket_created', 'Chamado Criado'),
            ('ticket_updated', 'Chamado Atualizado'),
            ('ticket_closed', 'Chamado Fechado'),
            ('communication_sent', 'Comunicado Enviado'),
            ('user_created', 'Usuário Criado'),
        ]
    }
    return render(request, 'admin/create_webhook.html', context)


@login_required
def profile_view(request):
    """Visualizar perfil do usuário"""
    context = {
        'user': request.user,
    }
    return render(request, 'users/profile.html', context)


@login_required
def update_profile_view(request):
    """Atualizar perfil do usuário"""
    if request.method == 'POST':
        user = request.user
        user.first_name = request.POST.get('first_name', user.first_name)
        user.last_name = request.POST.get('last_name', user.last_name)
        user.email = request.POST.get('email', user.email)
        user.phone = request.POST.get('phone', user.phone)
        user.save()
        
        messages.success(request, 'Perfil atualizado com sucesso!')
        return redirect('profile')
    
    return redirect('profile')


@login_required
def settings_view(request):
    """Visualizar configurações do usuário"""
    context = {
        'user': request.user,
    }
    return render(request, 'users/settings.html', context)


@login_required
def update_settings_view(request):
    """Atualizar configurações do usuário"""
    if request.method == 'POST':
        # Implementar atualização de configurações
        messages.success(request, 'Configurações atualizadas com sucesso!')
        return redirect('settings')
    
    return redirect('settings')


@login_required
def help_view(request):
    """Visualizar central de ajuda e tutoriais"""
    from core.models import Tutorial
    
    tutorials = Tutorial.objects.filter(is_active=True).order_by('order', 'title')
    
    context = {
        'tutorials': tutorials,
    }
    return render(request, 'help/tutorials.html', context)


@login_required
def change_password_view(request):
    """Alterar senha do usuário"""
    if request.method == 'POST':
        # Implementar mudança de senha
        messages.success(request, 'Senha alterada com sucesso!')
        return redirect('profile')
    
    return render(request, 'users/change_password.html')


@login_required
def manage_tutorials_view(request):
    """Gerenciar tutoriais (admin only)"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('dashboard')
    
    from core.models import Tutorial
    tutorials = Tutorial.objects.all().order_by('order', 'title')
    
    context = {
        'tutorials': tutorials,
    }
    return render(request, 'admin/tutorials.html', context)


@login_required
def create_tutorial_view(request):
    """Criar novo tutorial (admin only)"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        from core.models import Tutorial
        
        title = request.POST.get('title')
        description = request.POST.get('description')
        pdf_file = request.FILES.get('pdf_file')
        order = request.POST.get('order', 0)
        
        tutorial = Tutorial.objects.create(
            title=title,
            description=description,
            pdf_file=pdf_file,
            order=order,
            created_by=request.user
        )
        
        messages.success(request, 'Tutorial criado com sucesso!')
        return redirect('manage_tutorials')
    
    return render(request, 'admin/create_tutorial.html')


@login_required
def manage_prizes_view(request):
    """Gerenciar prêmios (admin only)"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('dashboard')
    
    from prizes.models import Prize, Redemption
    
    prizes = Prize.objects.all()
    recent_redemptions = Redemption.objects.all().order_by('-redeemed_at')[:10]
    
    # Calcular total real de C$ em circulação somando os saldos de todos os usuários
    total_cs_circulation = User.objects.aggregate(
        total=Sum('balance_cs')
    )['total'] or Decimal('0')
    
    context = {
        'prizes': prizes,
        'recent_redemptions': recent_redemptions,
        'total_prizes': prizes.count(),
        'active_prizes': prizes.filter(is_active=True).count(),
        'pending_redemptions': recent_redemptions.filter(status='PENDENTE').count(),
        'total_cs_circulation': total_cs_circulation,
    }
    return render(request, 'prizes/manage.html', context)


@login_required
def create_prize_view(request):
    """Criar novo prêmio (admin only)"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        from prizes.models import Prize
        
        name = request.POST.get('name')
        description = request.POST.get('description')
        value_cs = request.POST.get('value_cs')
        image = request.FILES.get('image')
        stock = request.POST.get('stock', 0)
        unlimited_stock = request.POST.get('unlimited_stock') == 'on'
        
        prize = Prize.objects.create(
            name=name,
            description=description,
            value_cs=value_cs,
            image=image,
            stock=stock if not unlimited_stock else 0,
            unlimited_stock=unlimited_stock
        )
        
        messages.success(request, 'Prêmio criado com sucesso!')
        return redirect('manage_prizes')
    
    return render(request, 'prizes/create.html')


@login_required
def edit_prize_view(request, prize_id):
    """Editar prêmio (admin only)"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('dashboard')
    
    from prizes.models import Prize
    prize = get_object_or_404(Prize, id=prize_id)
    
    if request.method == 'POST':
        prize.name = request.POST.get('name', prize.name)
        prize.description = request.POST.get('description', prize.description)
        prize.value_cs = request.POST.get('value_cs', prize.value_cs)
        
        if request.FILES.get('image'):
            prize.image = request.FILES.get('image')
        
        unlimited_stock = request.POST.get('unlimited_stock') == 'on'
        prize.unlimited_stock = unlimited_stock
        
        if not unlimited_stock:
            prize.stock = request.POST.get('stock', prize.stock)
        
        prize.save()
        
        messages.success(request, 'Prêmio atualizado com sucesso!')
        return redirect('manage_prizes')
    
    context = {
        'prize': prize,
    }
    return render(request, 'prizes/edit.html', context)


@login_required
def manage_redemptions_view(request):
    """Gerenciar resgates (admin only)"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('dashboard')
    
    from prizes.models import Redemption
    
    redemptions = Redemption.objects.all().order_by('-redeemed_at')
    
    context = {
        'redemptions': redemptions,
    }
    return render(request, 'prizes/redemptions.html', context)
