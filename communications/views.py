from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.db import models
from django.db.models import Q
from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
import json

from .models import Communication, CommunicationRead, CommunicationComment, CommunicationGroup
from .serializers import CommunicationSerializer
from users.models import User, Sector
from core.middleware import log_action


@login_required
def home_feed(request):
    """View principal da home com feed de comunicados"""
    # Comunicados fixados
    pinned_communications = Communication.objects.filter(
        is_pinned=True
    ).filter(
        Q(send_to_all=True) | Q(recipients=request.user)
    ).distinct().order_by('-created_at')[:3]
    
    # Feed de comunicados (não fixados)
    communications_list = Communication.objects.filter(
        is_pinned=False
    ).filter(
        Q(send_to_all=True) | Q(recipients=request.user)
    ).distinct().order_by('-created_at')
    
    # Paginação
    paginator = Paginator(communications_list, 10)
    page_number = request.GET.get('page')
    communications = paginator.get_page(page_number)
    
    context = {
        'pinned_communications': pinned_communications,
        'communications': communications,
    }
    return render(request, 'home.html', context)


@login_required
@require_POST
def communication_react(request, communication_id):
    """Endpoint para reações nos comunicados"""
    communication = get_object_or_404(Communication, id=communication_id)
    
    try:
        data = json.loads(request.body)
        reaction = data.get('reaction')
        
        if reaction not in ['like', 'love', 'clap']:
            return JsonResponse({'success': False, 'error': 'Reação inválida'})
        
        # Mapear reação para campo do modelo
        reaction_field_map = {
            'like': 'liked_by',
            'love': 'loved_by', 
            'clap': 'clapped_by'
        }
        
        reaction_field = getattr(communication, reaction_field_map[reaction])
        
        # Toggle da reação
        if reaction_field.filter(id=request.user.id).exists():
            reaction_field.remove(request.user)
            added = False
        else:
            reaction_field.add(request.user)
            added = True
        
        # Retornar nova contagem
        count = reaction_field.count()
        
        return JsonResponse({
            'success': True,
            'added': added,
            'count': count,
            'reaction': reaction
        })
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


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
        'comments': communication.comments.all().order_by('created_at'),
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
        is_pinned = request.POST.get('is_pinned') == 'on'
        is_popup = request.POST.get('is_popup') == 'on'
        sender_group = request.POST.get('sender_group', '')
        custom_group_id = request.POST.get('custom_group', '')
        active_from = request.POST.get('active_from')
        active_until = request.POST.get('active_until')
        
        try:
            communication = Communication.objects.create(
                title=title,
                message=message,
                sender=request.user,
                send_to_all=send_to_all,
                is_pinned=is_pinned,
                is_popup=is_popup,
                sender_group=sender_group if sender_group else None,
                custom_group_id=custom_group_id if custom_group_id else None,
                active_from=active_from if active_from else None,
                active_until=active_until if active_until else None
            )
            
            # Processar upload de imagem
            if 'photo' in request.FILES:
                communication.image = request.FILES['photo']
                # Se tem imagem, forçar is_popup para False
                communication.is_popup = False
                communication.save()
            
            # Adicionar destinatários baseado na lógica
            if custom_group_id:
                # Se foi selecionado um grupo personalizado, adicionar apenas os membros do grupo
                custom_group = CommunicationGroup.objects.get(id=custom_group_id)
                communication.recipients.add(*custom_group.members.all())
                communication.send_to_all = False
                communication.save()
            elif not send_to_all and recipient_ids:
                # Adicionar usuários individuais
                recipients = User.objects.filter(id__in=recipient_ids)
                communication.recipients.add(*recipients)
            
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
    
    # Preparar grupos para o usuário
    user_groups = []
    if request.user.hierarchy in ['GERENTE', 'COORDENACAO', 'DIRETORIA', 'SUPERADMIN', 'ADMINISTRADOR']:
        # Mostrar grupos baseado na hierarquia do usuário
        hierarchy_groups = {
            'GERENTE': [('GERENTES', 'Gerentes')],
            'COORDENACAO': [('COORDENACAO', 'Coordenação'), ('GERENTES', 'Gerentes')],
            'DIRETORIA': [('DIRETORIA', 'Diretoria'), ('COORDENACAO', 'Coordenação'), ('GERENTES', 'Gerentes')],
            'SUPERADMIN': Communication.SENDER_GROUP_CHOICES,
            'ADMINISTRADOR': Communication.SENDER_GROUP_CHOICES,
        }
        user_groups = hierarchy_groups.get(request.user.hierarchy, [])
    
    context = {
        'users': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'communication_groups': CommunicationGroup.objects.filter(is_active=True).order_by('name'),
        'user_groups': user_groups,
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
        
        # Processar remoção de imagem
        if request.POST.get('remove_photo'):
            if communication.image:
                communication.image.delete()
                communication.image = None
        
        # Processar upload de nova imagem
        if 'photo' in request.FILES:
            # Remover imagem antiga se existir
            if communication.image:
                communication.image.delete()
            communication.image = request.FILES['photo']
        
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


@login_required
@require_POST
def add_comment(request, communication_id):
    """Adicionar comentário a um comunicado"""
    communication = get_object_or_404(Communication, id=communication_id)
    content = request.POST.get('content', '').strip()
    
    if not content:
        return JsonResponse({'success': False, 'error': 'Comentário não pode estar vazio'})
    
    # Verificar se o usuário pode comentar
    can_comment = (
        communication.send_to_all or 
        communication.recipients.filter(id=request.user.id).exists() or
        communication.sender == request.user or
        request.user.can_manage_users()
    )
    
    if not can_comment:
        return JsonResponse({'success': False, 'error': 'Sem permissão para comentar'})
    
    comment = CommunicationComment.objects.create(
        communication=communication,
        user=request.user,
        content=content
    )
    
    # Preparar avatar do usuário
    if request.user.profile_picture:
        user_avatar = f'<img src="{request.user.profile_picture.url}" alt="{request.user.full_name}" class="w-8 h-8 rounded-full">'
    else:
        initials = f"{request.user.first_name[0].upper()}{request.user.last_name[0].upper()}" if request.user.first_name and request.user.last_name else "U"
        user_avatar = f'''<div class="w-8 h-8 bg-gray-500 rounded-full flex items-center justify-center">
            <span class="text-white text-xs font-medium">{initials}</span>
        </div>'''
    
    return JsonResponse({
        'success': True,
        'comment': {
            'id': comment.id,
            'content': comment.content.replace('\n', '<br>'),
            'user_name': comment.user.full_name,
            'user_avatar': user_avatar,
            'created_at': comment.created_at.strftime('%d/%m/%Y %H:%M')
        }
    })


@login_required
@require_POST
def delete_comment(request, comment_id):
    """Deletar comentário"""
    comment = get_object_or_404(CommunicationComment, id=comment_id)
    
    # Só o autor do comentário ou admin pode deletar
    if comment.user != request.user and not request.user.can_manage_users():
        return JsonResponse({'success': False, 'error': 'Sem permissão'})
    
    comment.delete()
    return JsonResponse({'success': True})
