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
        # Supervisores veem tickets de todos seus setores + seus próprios tickets (incluindo fechados)
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        
        tickets = Ticket.objects.filter(
            models.Q(sector__in=user_sectors) |
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
        # Supervisores veem tickets fechados de todos seus setores + seus próprios tickets fechados
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
            
        tickets = Ticket.objects.filter(
            models.Q(sector__in=user_sectors, status='FECHADO') |
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
    user_sectors = list(user.sectors.all())
    if user.sector:
        user_sectors.append(user.sector)
    
    can_view = (
        user.can_view_all_tickets() or 
        (user.can_view_sector_tickets() and ticket.sector in user_sectors) or
        ticket.created_by == user or
        user in ticket.get_all_assigned_users()
    )
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para visualizar este chamado.')
        return redirect('tickets_list')
    
    # Processar upload de arquivos via POST
    if request.method == 'POST' and 'upload_files' in request.POST:
        # Verificar permissão para adicionar arquivos
        can_upload = (
            user.can_view_all_tickets() or 
            (user.can_view_sector_tickets() and ticket.sector in user_sectors) or
            ticket.created_by == user or
            ticket.assigned_to == user or
            user in ticket.get_all_assigned_users()
        )
        
        if not can_upload:
            messages.error(request, 'Você não tem permissão para adicionar arquivos neste chamado.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        # Processar arquivos anexados
        from .models import TicketAttachment
        attachments = request.FILES.getlist('new_attachments')
        
        if not attachments:
            messages.error(request, 'Nenhum arquivo foi selecionado.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        uploaded_count = 0
        for attachment in attachments:
            # Verificar tamanho do arquivo (limite de 50MB por exemplo)
            if attachment.size > 50 * 1024 * 1024:  # 50MB
                messages.warning(request, f'Arquivo "{attachment.name}" é muito grande (máximo 50MB). Arquivo ignorado.')
                continue
                
            TicketAttachment.objects.create(
                ticket=ticket,
                file=attachment,
                original_filename=attachment.name,
                file_size=attachment.size,
                content_type=attachment.content_type,
                uploaded_by=user
            )
            uploaded_count += 1
        
        if uploaded_count > 0:
            # Adicionar comentário informativo sobre os arquivos adicionados
            TicketComment.objects.create(
                ticket=ticket,
                user=user,
                comment=f'{uploaded_count} arquivo(s) adicionado(s) ao chamado.',
                comment_type='COMMENT'
            )
            
            messages.success(request, f'{uploaded_count} arquivo(s) adicionado(s) com sucesso!')
            log_action(
                user, 
                'TICKET_ATTACHMENT', 
                f'{uploaded_count} arquivo(s) adicionado(s) ao chamado #{ticket.id}',
                request
            )
        
        return redirect('ticket_detail', ticket_id=ticket.id)
    
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
    
    # Verificar se pode fazer upload de arquivos
    can_upload = (
        user.can_view_all_tickets() or 
        (user.can_view_sector_tickets() and ticket.sector in user_sectors) or
        ticket.created_by == user or
        ticket.assigned_to == user or
        user in ticket.get_all_assigned_users()
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
        'can_upload': can_upload,
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
        
        # Processar arquivos anexados
        from .models import TicketAttachment
        attachments = request.FILES.getlist('attachments')
        for attachment in attachments:
            TicketAttachment.objects.create(
                ticket=ticket,
                file=attachment,
                original_filename=attachment.name,
                file_size=attachment.size,
                content_type=attachment.content_type,
                uploaded_by=request.user
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
        
        if attachments:
            messages.success(request, f'Chamado #{ticket.id} criado com sucesso! {len(attachments)} arquivo(s) anexado(s).')
        else:
            messages.success(request, f'Chamado #{ticket.id} criado com sucesso!')
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    sectors = Sector.objects.all()
    # Buscar todos os usuários ativos para seleção
    users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('sector__name', 'first_name')
    context = {
        'sectors': sectors,
        'users': users,
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
        # Ver tickets de todos os setores do usuário
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        tickets = Ticket.objects.filter(sector__in=user_sectors)
    else:
        tickets = Ticket.objects.filter(created_by=user)
    
    context = {
        'tickets': tickets.order_by('-created_at'),
        'user': user,
    }
    return render(request, 'tickets/list.html', context)


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
            # Ver tickets de todos os setores do usuário
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            return Ticket.objects.filter(sector__in=user_sectors)
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
    active_webhooks_count = webhooks.filter(is_active=True).count()
    inactive_webhooks_count = webhooks.filter(is_active=False).count()
    
    context = {
        'webhooks': webhooks,
        'active_webhooks_count': active_webhooks_count,
        'inactive_webhooks_count': inactive_webhooks_count,
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
        events = request.POST.getlist('events')  # Pegar lista de eventos selecionados
        category_id = request.POST.get('category')
        sector_id = request.POST.get('sector')
        is_active = request.POST.get('is_active') == 'on'
        headers = request.POST.get('headers', '{}')
        
        try:
            # Validar se pelo menos um evento foi selecionado
            if not events:
                messages.error(request, 'Por favor, selecione pelo menos um evento.')
                context = {
                    'event_choices': Webhook.EVENT_CHOICES,
                    'categories': Category.objects.all(),
                    'sectors': Sector.objects.all(),
                    'user': request.user,
                }
                return render(request, 'admin/create_webhook.html', context)
            
            # Validar e parsear headers JSON
            import json
            try:
                headers_dict = json.loads(headers) if headers.strip() else {}
            except json.JSONDecodeError:
                headers_dict = {}
            
            category = get_object_or_404(Category, id=category_id) if category_id else None
            sector = get_object_or_404(Sector, id=sector_id) if sector_id else None
            
            # Criar um webhook para cada evento selecionado
            created_webhooks = []
            for event in events:
                webhook_name = f"{name} - {dict(Webhook.EVENT_CHOICES)[event]}"
                
                webhook = Webhook.objects.create(
                    name=webhook_name,
                    url=url,
                    event=event,
                    category=category,
                    sector=sector,
                    is_active=is_active,
                    headers=headers_dict
                )
                created_webhooks.append(webhook)
            
            log_action(
                request.user, 
                'WEBHOOK_CREATE', 
                f'Webhooks criados: {len(created_webhooks)} webhook(s) para {name}',
                request
            )
            
            if len(created_webhooks) == 1:
                messages.success(request, f'Webhook "{created_webhooks[0].name}" criado com sucesso!')
            else:
                messages.success(request, f'{len(created_webhooks)} webhooks criados com sucesso para "{name}"!')
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


@login_required
def edit_webhook_view(request, webhook_id):
    """Editar webhook existente"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    webhook = get_object_or_404(Webhook, id=webhook_id)
    
    if request.method == 'POST':
        webhook.name = request.POST.get('name')
        webhook.url = request.POST.get('url')
        webhook.event = request.POST.get('event')
        
        category_id = request.POST.get('category')
        sector_id = request.POST.get('sector')
        
        webhook.category = get_object_or_404(Category, id=category_id) if category_id else None
        webhook.sector = get_object_or_404(Sector, id=sector_id) if sector_id else None
        webhook.is_active = request.POST.get('is_active') == 'on'
        
        headers = request.POST.get('headers', '{}')
        try:
            import json
            webhook.headers = json.loads(headers) if headers.strip() else {}
        except json.JSONDecodeError:
            webhook.headers = {}
        
        try:
            webhook.save()
            
            log_action(
                request.user,
                'WEBHOOK_UPDATE',
                f'Webhook atualizado: {webhook.name}',
                request
            )
            
            messages.success(request, f'Webhook "{webhook.name}" atualizado com sucesso!')
            return redirect('manage_webhooks')
            
        except Exception as e:
            messages.error(request, f'Erro ao atualizar webhook: {str(e)}')
    
    context = {
        'webhook': webhook,
        'event_choices': Webhook.EVENT_CHOICES,
        'categories': Category.objects.all(),
        'sectors': Sector.objects.all(),
        'user': request.user,
    }
    return render(request, 'admin/edit_webhook.html', context)


@login_required
def delete_webhook_view(request, webhook_id):
    """Excluir webhook"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    webhook = get_object_or_404(Webhook, id=webhook_id)
    
    if request.method == 'POST':
        webhook_name = webhook.name
        webhook.delete()
        
        log_action(
            request.user,
            'WEBHOOK_DELETE',
            f'Webhook excluído: {webhook_name}',
            request
        )
        
        messages.success(request, f'Webhook "{webhook_name}" excluído com sucesso!')
        return redirect('manage_webhooks')
    
    context = {
        'webhook': webhook,
        'user': request.user,
    }
    return render(request, 'admin/delete_webhook.html', context)


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
        
        # Processar arquivos anexados
        from .models import TicketAttachment
        attachments = request.FILES.getlist('attachments')
        for attachment in attachments:
            TicketAttachment.objects.create(
                ticket=ticket,
                file=attachment,
                original_filename=attachment.name,
                file_size=attachment.size,
                content_type=attachment.content_type,
                uploaded_by=request.user
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
        
        if attachments:
            messages.success(request, f'Chamado #{ticket.id} criado com sucesso! {len(attachments)} arquivo(s) anexado(s).')
        else:
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


# ========================
# PURCHASE ORDER API VIEWS
# ========================

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .models import PurchaseOrderApproval, TicketComment
import json


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve_purchase_order(request, ticket_id, approval_id):
    """API para aprovar uma ordem de compra"""
    try:
        approval = PurchaseOrderApproval.objects.get(
            id=approval_id,
            ticket_id=ticket_id,
            approver=request.user,
            status='PENDING'
        )
        
        comment = request.data.get('comment', '')
        approval.approve(comment)
        
        # Adicionar comentário no ticket
        TicketComment.objects.create(
            ticket=approval.ticket,
            user=request.user,
            comment=f"Ordem de compra aprovada (R$ {approval.amount:.2f}). {comment}".strip(),
            comment_type='COMMENT'
        )
        
        return Response({
            'message': 'Ordem de compra aprovada com sucesso',
            'status': 'approved',
            'next_step': approval.approval_step + 1 if approval.approval_step < 3 else 'completed'
        })
        
    except PurchaseOrderApproval.DoesNotExist:
        return Response({
            'error': 'Aprovação não encontrada ou você não tem permissão para aprová-la'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            'error': f'Erro ao processar aprovação: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject_purchase_order(request, ticket_id, approval_id):
    """API para rejeitar uma ordem de compra"""
    try:
        approval = PurchaseOrderApproval.objects.get(
            id=approval_id,
            ticket_id=ticket_id,
            approver=request.user,
            status='PENDING'
        )
        
        comment = request.data.get('comment', 'Ordem rejeitada')
        approval.reject(comment)
        
        # Adicionar comentário no ticket
        TicketComment.objects.create(
            ticket=approval.ticket,
            user=request.user,
            comment=f"Ordem de compra rejeitada (R$ {approval.amount:.2f}). Motivo: {comment}",
            comment_type='COMMENT'
        )
        
        # Atualizar status do ticket
        approval.ticket.status = 'REJEITADO'
        approval.ticket.save()
        
        return Response({
            'message': 'Ordem de compra rejeitada',
            'status': 'rejected',
            'reason': comment
        })
        
    except PurchaseOrderApproval.DoesNotExist:
        return Response({
            'error': 'Aprovação não encontrada ou você não tem permissão para rejeitá-la'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            'error': f'Erro ao processar rejeição: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pending_approvals(request):
    """API para listar aprovações pendentes do usuário"""
    try:
        approvals = PurchaseOrderApproval.objects.filter(
            approver=request.user,
            status='PENDING'
        ).select_related('ticket', 'ticket__category', 'ticket__created_by')
        
        approvals_data = []
        for approval in approvals:
            approvals_data.append({
                'id': approval.id,
                'ticket_id': approval.ticket.id,
                'ticket_title': approval.ticket.title,
                'amount': float(approval.amount),
                'created_at': approval.created_at.isoformat(),
                'approval_step': approval.approval_step,
                'created_by': approval.ticket.created_by.full_name,
                'category': approval.ticket.category.name,
            })
        
        return Response({
            'approvals': approvals_data,
            'count': len(approvals_data)
        })
        
    except Exception as e:
        return Response({
            'error': f'Erro ao carregar aprovações: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def user_tickets_api(request, user_id):
    """
    API REST para buscar chamados de um usuário específico
    URL: /api/users/{user_id}/tickets/
    Retorna quantidade e títulos dos chamados do usuário
    """
    try:
        # Buscar o usuário
        user = get_object_or_404(User, id=user_id)
        
        # Buscar todos os tickets do usuário
        tickets = Ticket.objects.filter(created_by=user).select_related('category', 'sector')
        
        # Preparar dados para resposta
        tickets_data = []
        for ticket in tickets:
            tickets_data.append({
                'id': ticket.id,
                'title': ticket.title,
                'status': ticket.status,
                'category': ticket.category.name if ticket.category else None,
                'sector': ticket.sector.name if ticket.sector else None,
                'created_at': ticket.created_at.isoformat(),
                'updated_at': ticket.updated_at.isoformat(),
            })
        
        # Contar por status
        status_counts = {
            'total': tickets.count(),
            'open': tickets.filter(status='OPEN').count(),
            'in_progress': tickets.filter(status='IN_PROGRESS').count(),
            'closed': tickets.filter(status='CLOSED').count(),
            'cancelled': tickets.filter(status='CANCELLED').count(),
        }
        
        return JsonResponse({
            'user': {
                'id': user.id,
                'username': user.username,
                'full_name': user.full_name,
                'email': user.email,
            },
            'tickets': {
                'count': status_counts['total'],
                'status_breakdown': status_counts,
                'data': tickets_data
            },
            'success': True
        })
        
    except User.DoesNotExist:
        return JsonResponse({
            'error': 'Usuário não encontrado',
            'success': False
        }, status=404)
        
    except Exception as e:
        return JsonResponse({
            'error': f'Erro interno do servidor: {str(e)}',
            'success': False
        }, status=500)
