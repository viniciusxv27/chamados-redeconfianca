from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import models
from django.db.models import Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Communication, CommunicationRead
from .serializers import CommunicationSerializer
from users.models import User, Sector
from core.middleware import log_action


@login_required
def communication_list(request):
    """Lista todos os comunicados do usuário"""
    if request.user.hierarchy == 'SUPERADMIN':
        communications = Communication.objects.all().order_by('-created_at')
    else:
        # Para outros usuários, mostrar apenas comunicados gerais e destinados a ele que estão ativos
        from django.utils import timezone
        now = timezone.now()
        
        communications = Communication.objects.filter(
            Q(send_to_all=True) | Q(recipients=request.user)
        ).filter(
            Q(active_from__isnull=True) | Q(active_from__lte=now)
        ).filter(
            Q(active_until__isnull=True) | Q(active_until__gte=now)
        ).distinct().order_by('-created_at')
    
    # Verificar quais comunicados foram lidos
    read_communications = CommunicationRead.objects.filter(
        user=request.user
    ).values_list('communication_id', flat=True)
    
    # Obter status de cada comunicado
    communication_statuses = {}
    for comm in communications:
        try:
            read_obj = CommunicationRead.objects.get(user=request.user, communication=comm)
            communication_statuses[comm.id] = {
                'status': read_obj.status,
                'status_display': read_obj.get_status_display()
            }
        except CommunicationRead.DoesNotExist:
            communication_statuses[comm.id] = {
                'status': 'NAO_VISUALIZADO',
                'status_display': 'Não Visualizado'
            }
    
    return render(request, 'communications/list.html', {
        'communications': communications,
        'read_communications': read_communications,
        'communication_statuses': communication_statuses,
    })


@login_required
def communication_detail_view(request, communication_id):
    """Detalhe do comunicado"""
    communication = get_object_or_404(Communication, id=communication_id)
    user = request.user
    
    # Verificar se o usuário pode ver este comunicado
    can_view = (
        communication.send_to_all or 
        communication.recipients.filter(id=user.id).exists() or
        communication.sender == user or
        user.can_manage_users()
    )
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para visualizar este comunicado.')
        return redirect('communications_list')
    
    # Marcar como lido (apenas se não existe e não é o criador)
    if communication.sender != user:
        comm_read, created = CommunicationRead.objects.get_or_create(
            communication=communication,
            user=user
        )
    else:
        # Para o criador, não criar registro automático
        try:
            comm_read = CommunicationRead.objects.get(
                communication=communication,
                user=user
            )
        except CommunicationRead.DoesNotExist:
            comm_read = None
    
    # Se o usuário é o criador do comunicado, mostrar status de todos os usuários
    users_status = []
    if communication.sender == user or user.can_manage_users():
        # Obter todos os usuários que deveriam ver este comunicado
        if communication.send_to_all:
            target_users = User.objects.filter(is_active=True).exclude(id=communication.sender.id)
        else:
            target_users = communication.recipients.all()
        
        # Obter status de cada usuário
        for target_user in target_users:
            try:
                read_obj = CommunicationRead.objects.get(
                    communication=communication,
                    user=target_user
                )
                status_info = {
                    'user': target_user,
                    'status': read_obj.status,
                    'status_display': read_obj.get_status_display(),
                    'read_at': read_obj.read_at,
                }
            except CommunicationRead.DoesNotExist:
                status_info = {
                    'user': target_user,
                    'status': 'NAO_VISUALIZADO',
                    'status_display': 'Não Visualizado',
                    'read_at': None,
                }
            users_status.append(status_info)
        
        # Ordenar por status (primeiro quem não visualizou, depois dúvidas, depois ciente)
        status_priority = {'NAO_VISUALIZADO': 0, 'ESTOU_COM_DUVIDA': 1, 'ESTOU_CIENTE': 2}
        users_status.sort(key=lambda x: status_priority.get(x['status'], 999))
        
        # Calcular estatísticas
        status_counts = {
            'not_viewed': len([s for s in users_status if s['status'] == 'NAO_VISUALIZADO']),
            'with_doubt': len([s for s in users_status if s['status'] == 'ESTOU_COM_DUVIDA']),
            'aware': len([s for s in users_status if s['status'] == 'ESTOU_CIENTE']),
        }
    else:
        status_counts = {}
    
    context = {
        'communication': communication,
        'user': user,
        'users_status': users_status,
        'is_sender': communication.sender == user or user.can_manage_users(),
        'status_counts': status_counts,
    }
    return render(request, 'communications/detail.html', context)


@login_required
def create_communication_view(request):
    """Criar novo comunicado"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para criar comunicados.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        title = request.POST.get('title')
        message = request.POST.get('message')
        send_to_all = request.POST.get('send_to_all') == 'on'
        recipient_ids = request.POST.getlist('recipients')
        active_from = request.POST.get('active_from')
        active_until = request.POST.get('active_until')
        
        try:
            communication = Communication.objects.create(
                title=title,
                message=message,
                sender=request.user,
                send_to_all=send_to_all,
                active_from=active_from if active_from else None,
                active_until=active_until if active_until else None
            )
            
            # Adicionar destinatários específicos se não for para todos
            if not send_to_all and recipient_ids:
                recipients = User.objects.filter(id__in=recipient_ids)
                communication.recipients.set(recipients)
            
            log_action(
                request.user, 
                'COMMUNICATION_CREATE', 
                f'Comunicado criado: {communication.title}',
                request
            )
            
            messages.success(request, f'Comunicado "{communication.title}" criado com sucesso!')
            return redirect('communications_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar comunicado: {str(e)}')
    
    context = {
        'users': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'user': request.user,
    }
    return render(request, 'communications/create.html', context)


@login_required
def edit_communication_view(request, communication_id):
    """Editar comunicado existente"""
    communication = get_object_or_404(Communication, id=communication_id)
    
    # Verificar permissões (apenas o criador ou superadmin)
    if communication.sender != request.user and not request.user.hierarchy == 'SUPERADMIN':
        messages.error(request, 'Você não tem permissão para editar este comunicado.')
        return redirect('communications_list')
    
    if request.method == 'POST':
        communication.title = request.POST.get('title')
        communication.message = request.POST.get('message')
        communication.send_to_all = request.POST.get('send_to_all') == 'on'
        
        active_from = request.POST.get('active_from')
        active_until = request.POST.get('active_until')
        communication.active_from = active_from if active_from else None
        communication.active_until = active_until if active_until else None
        
        recipient_ids = request.POST.getlist('recipients')
        
        try:
            communication.save()
            
            # Atualizar destinatários
            if not communication.send_to_all and recipient_ids:
                recipients = User.objects.filter(id__in=recipient_ids)
                communication.recipients.set(recipients)
            elif communication.send_to_all:
                communication.recipients.clear()
            
            log_action(
                request.user, 
                'COMMUNICATION_UPDATE', 
                f'Comunicado editado: {communication.title}',
                request
            )
            
            messages.success(request, f'Comunicado "{communication.title}" editado com sucesso!')
            return redirect('communication_detail', communication_id=communication.id)
            
        except Exception as e:
            messages.error(request, f'Erro ao editar comunicado: {str(e)}')
    
    context = {
        'communication': communication,
        'users': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'user': request.user,
    }
    return render(request, 'communications/edit.html', context)


@login_required
def delete_communication_view(request, communication_id):
    """Excluir comunicado"""
    communication = get_object_or_404(Communication, id=communication_id)
    
    # Verificar permissões (apenas o criador ou superadmin)
    if communication.sender != request.user and not request.user.hierarchy == 'SUPERADMIN':
        messages.error(request, 'Você não tem permissão para excluir este comunicado.')
        return redirect('communications_list')
    
    if request.method == 'POST':
        title = communication.title
        communication.delete()
        
        log_action(
            request.user, 
            'COMMUNICATION_DELETE', 
            f'Comunicado excluído: {title}',
            request
        )
        
        messages.success(request, f'Comunicado "{title}" excluído com sucesso!')
        return redirect('communications_list')
    
    return render(request, 'communications/delete.html', {
        'communication': communication,
        'user': request.user,
    })


@login_required
def get_unread_communications(request):
    """API para buscar comunicados não lidos (apenas ativos)"""
    from django.utils import timezone
    user = request.user
    now = timezone.now()
    
    # Buscar comunicados não lidos que estão ativos
    unread_communications = Communication.objects.filter(
        models.Q(recipients=user) | models.Q(send_to_all=True)
    ).filter(
        models.Q(active_from__isnull=True) | models.Q(active_from__lte=now)
    ).filter(
        models.Q(active_until__isnull=True) | models.Q(active_until__gte=now)
    ).exclude(
        communicationread__user=user
    ).distinct()
    
    data = []
    for comm in unread_communications[:5]:  # Últimos 5
        data.append({
            'id': comm.id,
            'title': comm.title,
            'message': comm.message[:100] + '...' if len(comm.message) > 100 else comm.message,
            'sender': comm.sender.full_name,
            'created_at': comm.created_at.strftime('%d/%m/%Y %H:%M'),
        })
    
    return JsonResponse({
        'communications': data,
        'count': unread_communications.count()
    })


@login_required
def update_communication_status(request, communication_id):
    """Atualizar status de confirmação do comunicado"""
    if request.method == 'POST':
        communication = get_object_or_404(Communication, id=communication_id)
        user = request.user
        status = request.POST.get('status')
        
        if status in ['ESTOU_CIENTE', 'ESTOU_COM_DUVIDA']:
            comm_read, created = CommunicationRead.objects.get_or_create(
                communication=communication,
                user=user
            )
            comm_read.status = status
            comm_read.save()
            
            messages.success(request, f'Status atualizado: {comm_read.get_status_display()}')
        else:
            messages.error(request, 'Status inválido.')
    
    return redirect('communication_detail', communication_id=communication_id)


class CommunicationViewSet(viewsets.ModelViewSet):
    queryset = Communication.objects.all()
    serializer_class = CommunicationSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.can_manage_users():
            return Communication.objects.all()
        else:
            return Communication.objects.filter(
                models.Q(recipients=user) | models.Q(send_to_all=True) | models.Q(sender=user)
            ).distinct()
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        communication = self.get_object()
        user = request.user
        
        read_obj, created = CommunicationRead.objects.get_or_create(
            communication=communication,
            user=user
        )
        
        return Response({'message': 'Comunicado marcado como lido'})
    
    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        user = request.user
        count = Communication.objects.filter(
            models.Q(recipients=user) | models.Q(send_to_all=True)
        ).exclude(
            communicationread__user=user
        ).distinct().count()
        
        return Response({'count': count})
