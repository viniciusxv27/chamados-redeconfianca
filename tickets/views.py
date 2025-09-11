from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.db import models
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Ticket, Category, TicketLog, TicketComment, Webhook, TicketView, TicketAssignment
from .serializers import TicketSerializer, CategorySerializer, TicketLogSerializer, TicketCommentSerializer, WebhookSerializer
from users.models import Sector, User
from core.middleware import log_action


@login_required
def tickets_list_view(request):
    user = request.user
    
    # Filtrar tickets baseado na hierarquia do usuário
    if user.can_view_all_tickets():
        # Admin vê todos os tickets (incluindo fechados)
        tickets = Ticket.objects.all()
    elif user.can_view_sector_tickets():
        # Supervisores veem tickets do setor + seus próprios tickets (incluindo fechados)
        tickets = Ticket.objects.filter(
            models.Q(sector=user.sector) |
            models.Q(created_by=user) |
            models.Q(assigned_to=user)
        ).distinct()
    else:
        # Usuários comuns veem seus próprios tickets + tickets onde estão atribuídos
        # Excluindo tickets fechados
        tickets = Ticket.objects.filter(
            models.Q(created_by=user) |
            models.Q(assigned_to=user) |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
        ).exclude(status='FECHADO').distinct()
    
    context = {
        'tickets': tickets.order_by('-created_at'),
        'user': user,
    }
    return render(request, 'tickets/list.html', context)


@login_required
def tickets_history_view(request):
    """View para mostrar histórico de chamados concluídos"""
    user = request.user
    
    # Filtrar apenas tickets fechados baseado na hierarquia do usuário
    if user.can_view_all_tickets():
        # Admin vê todos os tickets fechados
        tickets = Ticket.objects.filter(status='FECHADO')
    elif user.can_view_sector_tickets():
        # Supervisores veem tickets fechados do setor + seus próprios tickets fechados
        tickets = Ticket.objects.filter(
            models.Q(sector=user.sector, status='FECHADO') |
            models.Q(created_by=user, status='FECHADO')
        ).distinct()
    else:
        # Usuários comuns veem apenas seus próprios tickets fechados
        tickets = Ticket.objects.filter(
            models.Q(created_by=user, status='FECHADO') |
            models.Q(assigned_to=user, status='FECHADO') |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True, status='FECHADO')
        ).distinct()
    
    context = {
        'tickets': tickets.order_by('-closed_at'),
        'user': user,
        'is_history': True,
    }
    return render(request, 'tickets/history.html', context)


@login_required
def ticket_detail_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    user = request.user
    
    # Verificar permissão para visualizar o ticket
    can_view = (
        user.can_view_all_tickets() or 
        (user.can_view_sector_tickets() and ticket.sector == user.sector) or
        ticket.created_by == user or
        user in ticket.get_all_assigned_users()
    )
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para visualizar este chamado.')
        return redirect('tickets_list')
    
    # Marcar como visualizado
    ticket.mark_as_viewed(user)
    
    # Verificar se pode assumir o chamado
    can_assume = ticket.can_assume(user)
    
    # Verificar se pode atribuir outros usuários
    can_assign = (
        user.can_view_sector_tickets() or 
        user.can_view_all_tickets() or
        ticket.assigned_to == user
    )
    
    # Buscar usuários para atribuição (todos os setores) - sempre disponível
    sector_users = User.objects.filter(is_active=True).exclude(id=user.id).order_by('sector__name', 'first_name')
    
    context = {
        'ticket': ticket,
        'logs': ticket.logs.all(),
        'comments': ticket.comments.all(),
        'user': user,
        'can_assume': can_assume,
        'can_assign': can_assign,
        'assigned_users': ticket.get_all_assigned_users(),
        'additional_assignments': ticket.additional_assignments.filter(is_active=True).select_related('user', 'assigned_by'),
        'sector_users': sector_users,
    }
    return render(request, 'tickets/detail.html', context)


@login_required
def assume_ticket_view(request, ticket_id):
    """Assumir um chamado"""
    if request.method == 'POST':
        ticket = get_object_or_404(Ticket, id=ticket_id)
        comment = request.POST.get('comment', '')
        
        if ticket.assume_ticket(request.user, comment):
            messages.success(request, f'Chamado #{ticket.id} assumido com sucesso!')
            log_action(
                request.user, 
                'TICKET_ASSUME', 
                f'Chamado #{ticket.id} assumido',
                request
            )
        else:
            messages.error(request, 'Não foi possível assumir este chamado.')
        
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    return redirect('tickets_list')


@login_required
def add_comment_view(request, ticket_id):
    """Adicionar comentário ao chamado"""
    if request.method == 'POST':
        ticket = get_object_or_404(Ticket, id=ticket_id)
        comment_text = request.POST.get('comment')
        comment_type = request.POST.get('comment_type', 'COMMENT')
        assigned_user_id = request.POST.get('assigned_to')
        
        # Verificar permissão para comentar
        can_comment = (
            request.user.can_view_all_tickets() or 
            (request.user.can_view_sector_tickets() and ticket.sector == request.user.sector) or
            ticket.created_by == request.user or
            request.user in ticket.get_all_assigned_users()
        )
        
        if not can_comment:
            messages.error(request, 'Você não tem permissão para comentar neste chamado.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        if not comment_text:
            messages.error(request, 'Comentário é obrigatório.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        # Criar comentário
        comment = TicketComment.objects.create(
            ticket=ticket,
            user=request.user,
            comment=comment_text,
            comment_type=comment_type
        )
        
        # Se é uma atribuição, adicionar usuário
        if comment_type == 'ASSIGNMENT' and assigned_user_id:
            from users.models import User
            assigned_user = get_object_or_404(User, id=assigned_user_id)
            assignment = ticket.assign_additional_user(assigned_user, request.user, comment_text)
            comment.assigned_to = assigned_user
            comment.save()
        
        messages.success(request, 'Comentário adicionado com sucesso!')
        log_action(
            request.user, 
            'TICKET_COMMENT', 
            f'Comentário adicionado ao chamado #{ticket.id}',
            request
        )
        
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    return redirect('tickets_list')


@login_required
def update_ticket_status_view(request, ticket_id):
    """Atualizar status do chamado"""
    if request.method == 'POST':
        ticket = get_object_or_404(Ticket, id=ticket_id)
        new_status = request.POST.get('status')
        observation = request.POST.get('observation', '')
        solution = request.POST.get('solution', '')
        
        # Verificar permissão para atualizar
        can_update = (
            request.user.can_view_all_tickets() or 
            (request.user.can_view_sector_tickets() and ticket.sector == request.user.sector) or
            ticket.assigned_to == request.user or
            request.user in ticket.get_all_assigned_users() or
            ticket.created_by == request.user  # Criador pode aprovar/reprovar
        )
        
        if not can_update:
            messages.error(request, 'Você não tem permissão para atualizar este chamado.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        if not new_status:
            messages.error(request, 'Status é obrigatório.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        old_status = ticket.status
        ticket.status = new_status
        
        if new_status == 'RESOLVIDO':
            ticket.resolved_at = timezone.now()
            if solution:
                ticket.solution = solution
            else:
                messages.error(request, 'Solução é obrigatória para marcar como resolvido.')
                return redirect('ticket_detail', ticket_id=ticket.id)
            # Aguarda aprovação do usuário que criou o chamado
        elif new_status == 'FECHADO':
            ticket.closed_at = timezone.now()
        elif new_status == 'EM_ANDAMENTO' and old_status == 'RESOLVIDO':
            # Reprovação - limpar campos de resolução
            ticket.resolved_at = None
            ticket.solution = ''
        
        ticket.save()
        
        # Criar log
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            old_status=old_status,
            new_status=new_status,
            observation=observation
        )
        
        # Criar comentário se houver observação
        if observation:
            comment_type = 'STATUS_CHANGE'
            if new_status == 'FECHADO' and old_status == 'RESOLVIDO':
                comment_type = 'COMMENT'
                observation = f"Solução aprovada pelo usuário. {observation}"
            elif new_status == 'EM_ANDAMENTO' and old_status == 'RESOLVIDO':
                comment_type = 'COMMENT'
                observation = f"Solução reprovada pelo usuário. Motivo: {observation}"
                
            TicketComment.objects.create(
                ticket=ticket,
                user=request.user,
                comment=observation,
                comment_type=comment_type
            )
        
        # Mensagem de sucesso personalizada
        if new_status == 'RESOLVIDO':
            messages.success(request, f'Chamado #{ticket.id} marcado como resolvido. Aguardando aprovação do usuário.')
        elif new_status == 'FECHADO':
            messages.success(request, f'Chamado #{ticket.id} fechado com sucesso!')
        elif new_status == 'EM_ANDAMENTO' and old_status == 'RESOLVIDO':
            messages.warning(request, f'Solução do chamado #{ticket.id} foi reprovada. O chamado retornou para "Em Andamento".')
        else:
            messages.success(request, f'Status do chamado #{ticket.id} atualizado com sucesso!')
        
        log_action(
            request.user, 
            'TICKET_UPDATE', 
            f'Status do chamado #{ticket.id} alterado: {old_status} → {new_status}',
            request
        )
        
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    return redirect('tickets_list')


@login_required
def ticket_create_view(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        sector_id = request.POST.get('sector')
        category_id = request.POST.get('category')
        requires_approval = request.POST.get('requires_approval') == 'on'
        approval_user_id = request.POST.get('approval_user')
        
        sector = get_object_or_404(Sector, id=sector_id)
        category = get_object_or_404(Category, id=category_id)
        
        # Criar ticket
        ticket = Ticket.objects.create(
            title=title,
            description=description,
            sector=sector,
            category=category,
            created_by=request.user,
            requires_approval=requires_approval or category.requires_approval,
            approval_user_id=approval_user_id if requires_approval else None,
            solution_time_hours=int(request.POST.get('solution_time_hours', 24)),
            priority=request.POST.get('priority', 'MEDIA')
        )
        
        # Criar log inicial
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            new_status='ABERTO',
            observation='Chamado criado'
        )
        
        log_action(
            request.user, 
            'TICKET_CREATE', 
            f'Chamado criado: #{ticket.id} - {ticket.title}',
            request
        )
        
        messages.success(request, f'Chamado #{ticket.id} criado com sucesso!')
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    sectors = Sector.objects.all()
    context = {
        'sectors': sectors,
    }
    return render(request, 'tickets/create.html', context)


def get_categories_by_sector(request):
    sector_id = request.GET.get('sector_id')
    if sector_id:
        categories = Category.objects.filter(sector_id=sector_id, is_active=True)
        data = [{'id': cat.id, 'name': cat.name, 'default_description': cat.default_description, 'default_solution_time_hours': cat.default_solution_time_hours} for cat in categories]
        return JsonResponse({'categories': data})
    return JsonResponse({'categories': []})
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Ticket, Category, TicketLog, TicketComment, Webhook
from .serializers import TicketSerializer, CategorySerializer, TicketLogSerializer, TicketCommentSerializer, WebhookSerializer
from users.models import Sector
from core.middleware import log_action


@login_required
def tickets_list_view(request):
    user = request.user
    
    # Filtrar tickets baseado na hierarquia do usuário
    if user.can_view_all_tickets():
        tickets = Ticket.objects.all()
    elif user.can_view_sector_tickets():
        tickets = Ticket.objects.filter(sector=user.sector)
    else:
        tickets = Ticket.objects.filter(created_by=user)
    
    context = {
        'tickets': tickets.order_by('-created_at'),
        'user': user,
    }
    return render(request, 'tickets/list.html', context)


@login_required
def ticket_create_view(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        sector_id = request.POST.get('sector')
        category_id = request.POST.get('category')
        requires_approval = request.POST.get('requires_approval') == 'on'
        approval_user_id = request.POST.get('approval_user')
        
        sector = get_object_or_404(Sector, id=sector_id)
        category = get_object_or_404(Category, id=category_id)
        
        # Criar ticket
        ticket = Ticket.objects.create(
            title=title,
            description=description,
            sector=sector,
            category=category,
            created_by=request.user,
            requires_approval=requires_approval or category.requires_approval,
            approval_user_id=approval_user_id if requires_approval else None,
            solution_time_hours=int(request.POST.get('solution_time_hours', 24)),
            priority=request.POST.get('priority', 'MEDIA')
        )
        
        # Criar log inicial
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            new_status='ABERTO',
            observation='Chamado criado'
        )
        
        log_action(
            request.user, 
            'TICKET_CREATE', 
            f'Chamado criado: #{ticket.id} - {ticket.title}',
            request
        )
        
        messages.success(request, f'Chamado #{ticket.id} criado com sucesso!')
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    sectors = Sector.objects.all()
    context = {
        'sectors': sectors,
    }
    return render(request, 'tickets/create.html', context)


def get_categories_by_sector(request):
    sector_id = request.GET.get('sector_id')
    if sector_id:
        categories = Category.objects.filter(sector_id=sector_id, is_active=True)
        data = [{'id': cat.id, 'name': cat.name, 'default_description': cat.default_description, 'default_solution_time_hours': cat.default_solution_time_hours} for cat in categories]
        return JsonResponse({'categories': data})
    return JsonResponse({'categories': []})


class TicketViewSet(viewsets.ModelViewSet):
    queryset = Ticket.objects.all()
    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.can_view_all_tickets():
            return Ticket.objects.all()
        elif user.can_view_sector_tickets():
            return Ticket.objects.filter(sector=user.sector)
        else:
            return Ticket.objects.filter(created_by=user)
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        ticket = self.get_object()
        new_status = request.data.get('status')
        observation = request.data.get('observation', '')
        solution = request.data.get('solution', '')
        
        if not new_status:
            return Response(
                {'error': 'Status é obrigatório'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        old_status = ticket.status
        ticket.status = new_status
        
        if new_status == 'RESOLVIDO':
            ticket.resolved_at = timezone.now()
            ticket.solution = solution
            # Se não requer aprovação do usuário, vai direto para fechado
            if not ticket.requires_approval:
                ticket.status = 'FECHADO'
                ticket.closed_at = timezone.now()
        elif new_status == 'FECHADO':
            ticket.closed_at = timezone.now()
        
        ticket.save()
        
        # Criar log
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            old_status=old_status,
            new_status=new_status,
            observation=observation
        )
        
        log_action(
            request.user, 
            'TICKET_UPDATE', 
            f'Status do chamado #{ticket.id} alterado: {old_status} → {new_status}',
            request
        )
        
        return Response({'message': 'Status atualizado com sucesso'})
    
    @action(detail=True, methods=['post'])
    def add_comment(self, request, pk=None):
        ticket = self.get_object()
        comment_text = request.data.get('comment')
        
        if not comment_text:
            return Response(
                {'error': 'Comentário é obrigatório'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        comment = TicketComment.objects.create(
            ticket=ticket,
            user=request.user,
            comment=comment_text
        )
        
        serializer = TicketCommentSerializer(comment)
        return Response(serializer.data)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]


@login_required
def manage_webhooks_view(request):
    """Gerenciar webhooks"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    webhooks = Webhook.objects.all().select_related('category', 'sector')
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
        name = request.POST.get('name')
        url = request.POST.get('url')
        event = request.POST.get('event')
        category_id = request.POST.get('category')
        sector_id = request.POST.get('sector')
        is_active = request.POST.get('is_active') == 'on'
        
        try:
            category = get_object_or_404(Category, id=category_id) if category_id else None
            sector = get_object_or_404(Sector, id=sector_id) if sector_id else None
            
            webhook = Webhook.objects.create(
                name=name,
                url=url,
                event=event,
                category=category,
                sector=sector,
                is_active=is_active
            )
            
            log_action(
                request.user, 
                'WEBHOOK_CREATE', 
                f'Webhook criado: {webhook.name}',
                request
            )
            
            messages.success(request, f'Webhook "{webhook.name}" criado com sucesso!')
            return redirect('manage_webhooks')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar webhook: {str(e)}')
    
    context = {
        'event_choices': Webhook.EVENT_CHOICES,
        'categories': Category.objects.all(),
        'sectors': Sector.objects.all(),
        'user': request.user,
    }
    return render(request, 'admin/create_webhook.html', context)


class WebhookViewSet(viewsets.ModelViewSet):
    queryset = Webhook.objects.all()
    serializer_class = WebhookSerializer
    permission_classes = [IsAuthenticated]
    
    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        webhook = self.get_object()
        
        # Criar payload de teste
        test_payload = {
            'event': webhook.event,
            'webhook_name': webhook.name,
            'test': True,
            'timestamp': timezone.now().isoformat(),
            'message': 'Este é um teste do webhook'
        }
        
        try:
            import requests
            response = requests.post(webhook.url, json=test_payload, timeout=10)
            return Response({
                'message': 'Webhook testado com sucesso',
                'status_code': response.status_code,
                'response': response.text[:500]
            })
        except Exception as e:
            return Response({
                'error': f'Erro ao testar webhook: {str(e)}'
            }, status=status.HTTP_400_BAD_REQUEST)


@login_required
def ticket_create_fixed_view(request):
    """View corrigida para criação de tickets com todos os usuários"""
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        sector_id = request.POST.get('sector')
        category_id = request.POST.get('category')
        requires_approval = request.POST.get('requires_approval') == 'on'
        approval_user_id = request.POST.get('approval_user')
        
        sector = get_object_or_404(Sector, id=sector_id)
        category = get_object_or_404(Category, id=category_id)
        
        # Criar ticket
        ticket = Ticket.objects.create(
            title=title,
            description=description,
            sector=sector,
            category=category,
            created_by=request.user,
            requires_approval=requires_approval or category.requires_approval,
            approval_user_id=approval_user_id if requires_approval else None,
            solution_time_hours=int(request.POST.get('solution_time_hours', 24)),
            priority=request.POST.get('priority', 'MEDIA')
        )
        
        # Criar log inicial
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            new_status='ABERTO',
            observation='Chamado criado'
        )
        
        log_action(
            request.user, 
            'TICKET_CREATE', 
            f'Chamado criado: #{ticket.id} - {ticket.title}',
            request
        )
        
        messages.success(request, f'Chamado #{ticket.id} criado com sucesso!')
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    sectors = Sector.objects.all()
    # Buscar todos os usuários ativos para cópia, exceto o usuário atual
    users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('sector__name', 'first_name')
    context = {
        'sectors': sectors,
        'users': users,
    }
    return render(request, 'tickets/create.html', context)
