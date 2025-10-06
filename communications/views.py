from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
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
    ).distinct().prefetch_related('images').order_by('-created_at')[:3]
    
    # Feed de comunicados (não fixados)
    communications_list = Communication.objects.filter(
        is_pinned=False
    ).filter(
        Q(send_to_all=True) | Q(recipients=request.user)
    ).distinct().prefetch_related('images').order_by('-created_at')
    
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
@require_http_methods(["POST"])
@ensure_csrf_cookie
def communication_react(request, communication_id):
    """Endpoint para reações nos comunicados"""
    communication = get_object_or_404(Communication, id=communication_id)
    
    try:
        data = json.loads(request.body)
        reaction = data.get('reaction')
        
        print(f"DEBUG: User {request.user.id} reacting '{reaction}' to communication {communication_id}")
        
        if reaction not in ['like']:
            return JsonResponse({'success': False, 'error': 'Reação inválida'})
        
        # Mapear reação para campo do modelo
        reaction_field_map = {
            'like': 'liked_by'
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
        
        print(f"DEBUG: Reaction {reaction} {'added' if added else 'removed'}, new count: {count}")
        
        return JsonResponse({
            'success': True,
            'added': added,
            'count': count,
            'reaction': reaction
        })
        
    except Exception as e:
        print(f"DEBUG: Error in communication_react: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def communication_list(request):
    """Lista todos os comunicados do usuário"""
    if request.user.hierarchy == 'SUPERADMIN':
        communications = Communication.objects.all().select_related('sender').prefetch_related(
            'viewed_by', 'liked_by', 'comments', 'images'
        ).order_by('-created_at')
    else:
        # Para outros usuários, mostrar apenas comunicados gerais e destinados a ele que estão ativos
        from django.utils import timezone
        now = timezone.now()
        
        communications = Communication.objects.filter(
            Q(send_to_all=True) | Q(recipients=request.user) | Q(sender=request.user)
        ).filter(
            Q(active_from__isnull=True) | Q(active_from__lte=now)
        ).filter(
            Q(active_until__isnull=True) | Q(active_until__gte=now)
        ).select_related('sender').prefetch_related(
            'viewed_by', 'liked_by', 'comments', 'images'
        ).distinct().order_by('-created_at')
    
    # Verificar quais comunicados foram lidos
    read_communications = CommunicationRead.objects.filter(
        user=request.user
    ).values_list('communication_id', flat=True)
    
    # Obter status de cada comunicado e calcular contadores
    communication_statuses = {}
    status_counts = {
        'NAO_VISUALIZADO': 0,
        'ESTOU_CIENTE': 0,
        'ESTOU_COM_DUVIDA': 0
    }
    
    for comm in communications:
        try:
            read_obj = CommunicationRead.objects.get(user=request.user, communication=comm)
            communication_statuses[comm.id] = {
                'status': read_obj.status,
                'status_display': read_obj.get_status_display()
            }
            status_counts[read_obj.status] += 1
        except CommunicationRead.DoesNotExist:
            communication_statuses[comm.id] = {
                'status': 'NAO_VISUALIZADO',
                'status_display': 'Não Visualizado'
            }
            status_counts['NAO_VISUALIZADO'] += 1
    
    return render(request, 'communications/list.html', {
        'communications': communications,
        'read_communications': read_communications,
        'communication_statuses': communication_statuses,
        'status_counts': status_counts,
    })


@login_required
def communication_detail_view(request, communication_id):
    """View to display a single communication with details"""
    communication = get_object_or_404(Communication, pk=communication_id)
    
    # Mark as read for current user
    if request.user.is_authenticated:
        CommunicationRead.objects.get_or_create(
            communication=communication, 
            user=request.user,
            defaults={'read_at': timezone.now()}
        )
    
    # Get user's status for this communication
    communication_status = {'status': 'NAO_VISUALIZADO', 'status_display': 'Não visualizado'}
    if request.user.is_authenticated:
        try:
            read_obj = CommunicationRead.objects.get(user=request.user, communication=communication)
            communication_status = {
                'status': read_obj.status,
                'status_display': read_obj.get_status_display()
            }
        except CommunicationRead.DoesNotExist:
            pass
    
    # Get all reactions info with user details
    reactions_info = {
        'likes': {
            'count': communication.liked_by.count(),
            'users': communication.liked_by.select_related().all()[:10]
        },
        'views': {
            'count': communication.viewed_by.count(),
            'users': communication.viewed_by.select_related().all()[:10]
        }
    }
    
    # Get status information
    from django.db.models import Q
    status_reads = CommunicationRead.objects.filter(communication=communication).select_related('user')
    
    ciente_users = [read.user for read in status_reads if read.status == 'ESTOU_CIENTE']
    duvida_users = [read.user for read in status_reads if read.status == 'ESTOU_COM_DUVIDA']
    
    status_info = {
        'ciente': {
            'count': len(ciente_users),
            'users': ciente_users[:10]
        },
        'duvida': {
            'count': len(duvida_users),
            'users': duvida_users[:10]
        }
    }
    
    # Get comments for this communication
    comments = communication.comments.select_related('user').order_by('created_at')
    
    context = {
        'communication': communication,
        'communication_status': communication_status,
        'reactions_info': reactions_info,
        'status_info': status_info,
        'comments': comments
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
        communication_group_ids = request.POST.getlist('communication_groups')  # Múltiplos grupos
        is_pinned = request.POST.get('is_pinned') == 'on'
        is_popup = request.POST.get('is_popup') == 'on'
        sender_group = request.POST.get('sender_group', '')
        custom_group_id = request.POST.get('custom_group', '')  # Manter para compatibilidade
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
            
            # Processar upload de imagens múltiplas
            if 'photos' in request.FILES:
                from .models import CommunicationImage
                photos = request.FILES.getlist('photos')
                
                if photos:
                    # Se tem imagens, forçar is_popup para False
                    communication.is_popup = False
                    communication.save(skip_webhooks=True)
                    
                    # Processar cada imagem
                    for index, photo in enumerate(photos):
                        caption = request.POST.get(f'caption-{index}', '')
                        CommunicationImage.objects.create(
                            communication=communication,
                            image=photo,
                            caption=caption,
                            order=index
                        )
            
            # Manter compatibilidade com imagem única
            elif 'photo' in request.FILES:
                communication.image = request.FILES['photo']
                # Se tem imagem, forçar is_popup para False
                communication.is_popup = False
                communication.save()
            
            # Adicionar destinatários baseado na lógica
            all_recipients = set()
            
            # Processar múltiplos grupos de comunicação
            if communication_group_ids:
                for group_id in communication_group_ids:
                    try:
                        group = CommunicationGroup.objects.get(id=group_id)
                        group_members = group.members.all()
                        all_recipients.update(group_members)
                        print(f"DEBUG: Adicionando {group_members.count()} membros do grupo '{group.name}'")
                    except CommunicationGroup.DoesNotExist:
                        print(f"DEBUG: Grupo {group_id} não encontrado")
                        continue
                
                # Adicionar todos os destinatários únicos dos grupos
                if all_recipients:
                    communication.recipients.add(*all_recipients)
                    communication.send_to_all = False
                    communication.save()
                    print(f"DEBUG: Total de destinatários únicos adicionados: {len(all_recipients)}")
            
            elif custom_group_id:
                # Se foi selecionado um grupo personalizado (compatibilidade), adicionar apenas os membros do grupo
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
            return redirect('communications:communications_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar comunicado: {str(e)}')
    
    # Preparar grupos para o usuário (apenas grupos em que ele é membro)
    user_groups = []
    if request.user.hierarchy in ['GERENTE', 'COORDENACAO', 'DIRETORIA', 'SUPERADMIN', 'ADMIN', 'SUPERVISOR', 'ADMINISTRATIVO']:
        # Mostrar grupos baseado na hierarquia do usuário
        hierarchy_groups = {
            'GERENTE': [('GERENTES', 'Gerentes')],
            'COORDENACAO': [('COORDENACAO', 'Coordenação'), ('GERENTES', 'Gerentes')],
            'DIRETORIA': [('DIRETORIA', 'Diretoria'), ('COORDENACAO', 'Coordenação'), ('GERENTES', 'Gerentes')],
            'SUPERVISOR': [('SUPERVISORES', 'Supervisores')],
            'ADMINISTRATIVO': [('ADMINISTRATIVO', 'Administrativo')],
            'SUPERADMIN': Communication.SENDER_GROUP_CHOICES,
            'ADMIN': Communication.SENDER_GROUP_CHOICES,
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
        return redirect('communications:communications_list')
    
    if request.method == 'POST':
        communication.title = request.POST.get('title')
        communication.message = request.POST.get('message')
        communication.send_to_all = request.POST.get('send_to_all') == 'on'
        communication.is_pinned = request.POST.get('is_pinned') == 'on'
        communication.is_popup = request.POST.get('is_popup') == 'on'
        
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
            return redirect('communications:communication_detail', communication_id=communication.id)
            
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
        return redirect('communications:communications_list')
    
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
        return redirect('communications:communications_list')
    
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


from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods


@login_required
@require_http_methods(["POST"])
@ensure_csrf_cookie
def update_communication_status(request, communication_id):
    """Atualizar status de confirmação do comunicado"""
    if request.method == 'POST':
        communication = get_object_or_404(Communication, id=communication_id)
        user = request.user
        
        print(f"DEBUG: Headers: {dict(request.headers)}")
        print(f"DEBUG: Updating status for communication {communication_id} by user {user.id}")
        
        # Check if it's an AJAX request
        if request.content_type == 'application/json':
            import json
            data = json.loads(request.body)
            status = data.get('status')
            print(f"DEBUG: AJAX request with status: {status}")
        else:
            status = request.POST.get('status')
            print(f"DEBUG: Form request with status: {status}")
        
        if status in ['ESTOU_CIENTE', 'ESTOU_COM_DUVIDA']:
            comm_read, created = CommunicationRead.objects.get_or_create(
                communication=communication,
                user=user,
                defaults={'read_at': timezone.now()}
            )
            comm_read.status = status
            comm_read.save()
            
            print(f"DEBUG: Status updated successfully to {status}")
            
            # If it's an AJAX request, return JSON
            if request.content_type == 'application/json':
                return JsonResponse({
                    'success': True, 
                    'status': status,
                    'status_display': comm_read.get_status_display()
                })
            
            messages.success(request, f'Status atualizado: {comm_read.get_status_display()}')
        else:
            print(f"DEBUG: Invalid status: {status}")
            # If it's an AJAX request, return JSON error
            if request.content_type == 'application/json':
                return JsonResponse({'success': False, 'error': 'Status inválido.'})
            
            messages.error(request, 'Status inválido.')
    
    return redirect('communications:communication_detail', communication_id=communication_id)


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
