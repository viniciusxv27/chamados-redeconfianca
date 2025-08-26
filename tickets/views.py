from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Ticket, Category, TicketLog, TicketComment
from .serializers import TicketSerializer, CategorySerializer, TicketLogSerializer, TicketCommentSerializer
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
        'tickets': tickets,
        'user': user,
    }
    return render(request, 'tickets/list.html', context)


@login_required
def ticket_detail_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    user = request.user
    
    # Verificar permissão para visualizar o ticket
    can_view = (
        user.can_view_all_tickets() or 
        (user.can_view_sector_tickets() and ticket.sector == user.sector) or
        ticket.created_by == user
    )
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para visualizar este chamado.')
        return redirect('tickets_list')
    
    context = {
        'ticket': ticket,
        'logs': ticket.logs.all(),
        'comments': ticket.comments.all(),
        'user': user,
    }
    return render(request, 'tickets/detail.html', context)


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
        
        ticket = Ticket.objects.create(
            title=title,
            description=description,
            sector=sector,
            category=category,
            created_by=request.user,
            requires_approval=requires_approval or category.requires_approval,
            approval_user_id=approval_user_id if requires_approval else None
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
        data = [{'id': cat.id, 'name': cat.name, 'default_description': cat.default_description} for cat in categories]
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
