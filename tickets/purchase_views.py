from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.db.models import Q, Sum, Count
from .models import PurchaseOrderApprover, PurchaseOrderApproval, Ticket
from users.models import User
from core.middleware import log_action
import logging

logger = logging.getLogger(__name__)


@login_required
def manage_purchase_approvers_view(request):
    """View para gerenciar aprovadores de ordem de compra (apenas superadmin)"""
    if not request.user.hierarchy == 'SUPERADMIN':
        messages.error(request, 'Acesso negado. Apenas superadministradores podem acessar esta área.')
        return redirect('dashboard')
    
    approvers = PurchaseOrderApprover.objects.all().order_by('approval_order')
    available_users = User.objects.filter(is_active=True).exclude(
        id__in=approvers.values_list('user_id', flat=True)
    ).order_by('first_name', 'last_name')
    
    context = {
        'approvers': approvers,
        'available_users': available_users,
        'page_title': 'Gestão de Aprovadores - Ordem de Compra'
    }
    
    return render(request, 'tickets/admin/purchase_approvers.html', context)


@login_required
@require_POST
def create_purchase_approver_view(request):
    """Criar novo aprovador"""
    if not request.user.hierarchy == 'SUPERADMIN':
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    try:
        user_id = request.POST.get('user_id')
        max_amount = float(request.POST.get('max_amount', 0))
        approval_order = int(request.POST.get('approval_order', 1))
        
        if not user_id or max_amount <= 0:
            return JsonResponse({'error': 'Dados inválidos'}, status=400)
        
        user = User.objects.get(id=user_id)
        
        # Verificar se a ordem já existe
        if PurchaseOrderApprover.objects.filter(approval_order=approval_order).exists():
            return JsonResponse({'error': f'Já existe um aprovador na ordem {approval_order}'}, status=400)
        
        # Criar aprovador
        approver = PurchaseOrderApprover.objects.create(
            user=user,
            max_amount=max_amount,
            approval_order=approval_order
        )
        
        # Log da ação
        log_action(
            request.user,
            'APPROVER_CREATE',
            f'Aprovador criado: {user.full_name} - Ordem {approval_order} - R$ {max_amount}',
            request
        )
        
        messages.success(request, f'Aprovador {user.full_name} criado com sucesso!')
        return JsonResponse({'success': True, 'message': 'Aprovador criado com sucesso'})
        
    except User.DoesNotExist:
        return JsonResponse({'error': 'Usuário não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': f'Erro interno: {str(e)}'}, status=500)


@login_required
@require_POST
def update_purchase_approver_view(request, approver_id):
    """Atualizar aprovador existente"""
    if not request.user.hierarchy == 'SUPERADMIN':
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    try:
        approver = PurchaseOrderApprover.objects.get(id=approver_id)
        old_amount = approver.max_amount
        old_order = approver.approval_order
        
        max_amount = float(request.POST.get('max_amount', approver.max_amount))
        approval_order = int(request.POST.get('approval_order', approver.approval_order))
        is_active = request.POST.get('is_active', 'true').lower() == 'true'
        
        # Verificar se a nova ordem já existe (exceto para o próprio aprovador)
        if approval_order != old_order and PurchaseOrderApprover.objects.filter(
            approval_order=approval_order
        ).exclude(id=approver_id).exists():
            return JsonResponse({'error': f'Já existe um aprovador na ordem {approval_order}'}, status=400)
        
        # Atualizar
        approver.max_amount = max_amount
        approver.approval_order = approval_order
        approver.is_active = is_active
        approver.save()
        
        # Log da ação
        log_action(
            request.user,
            'APPROVER_UPDATE',
            f'Aprovador atualizado: {approver.user.full_name} - Ordem {old_order}→{approval_order} - R$ {old_amount}→{max_amount}',
            request
        )
        
        messages.success(request, f'Aprovador {approver.user.full_name} atualizado com sucesso!')
        return JsonResponse({'success': True, 'message': 'Aprovador atualizado com sucesso'})
        
    except PurchaseOrderApprover.DoesNotExist:
        return JsonResponse({'error': 'Aprovador não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': f'Erro interno: {str(e)}'}, status=500)


@login_required
@require_POST
def delete_purchase_approver_view(request, approver_id):
    """Deletar aprovador"""
    if not request.user.hierarchy == 'SUPERADMIN':
        return JsonResponse({'error': 'Acesso negado'}, status=403)
    
    try:
        approver = PurchaseOrderApprover.objects.get(id=approver_id)
        user_name = approver.user.full_name
        order = approver.approval_order
        
        # Verificar se há aprovações pendentes
        pending_approvals = PurchaseOrderApproval.objects.filter(
            approver=approver.user,
            status='PENDING'
        ).count()
        
        if pending_approvals > 0:
            return JsonResponse({
                'error': f'Não é possível excluir. Há {pending_approvals} aprovações pendentes para este usuário.'
            }, status=400)
        
        approver.delete()
        
        # Log da ação
        log_action(
            request.user,
            'APPROVER_DELETE',
            f'Aprovador removido: {user_name} - Ordem {order}',
            request
        )
        
        messages.success(request, f'Aprovador {user_name} removido com sucesso!')
        return JsonResponse({'success': True, 'message': 'Aprovador removido com sucesso'})
        
    except PurchaseOrderApprover.DoesNotExist:
        return JsonResponse({'error': 'Aprovador não encontrado'}, status=404)
    except Exception as e:
        return JsonResponse({'error': f'Erro interno: {str(e)}'}, status=500)


@login_required
def purchase_approvals_history_view(request):
    """Histórico de aprovações de ordens de compra"""
    if not request.user.hierarchy in ['SUPERADMIN', 'ADMIN']:
        messages.error(request, 'Acesso negado.')
        return redirect('dashboard')
    
    # Filtros
    status_filter = request.GET.get('status', '')
    approver_filter = request.GET.get('approver', '')
    
    approvals = PurchaseOrderApproval.objects.select_related(
        'ticket', 'approver', 'ticket__created_by', 'ticket__category'
    ).order_by('-created_at')
    
    if status_filter:
        approvals = approvals.filter(status=status_filter)
    
    if approver_filter:
        approvals = approvals.filter(approver_id=approver_filter)
    
    # Paginação
    from django.core.paginator import Paginator
    paginator = Paginator(approvals, 20)
    page_number = request.GET.get('page')
    approvals_page = paginator.get_page(page_number)
    
    # Dados para filtros
    approvers = User.objects.filter(
        purchase_approval_config__isnull=False
    ).order_by('first_name', 'last_name')
    
    context = {
        'approvals': approvals_page,
        'approvers': approvers,
        'status_choices': PurchaseOrderApproval.STATUS_CHOICES,
        'current_status': status_filter,
        'current_approver': approver_filter,
        'page_title': 'Histórico de Aprovações - Ordem de Compra'
    }
    
    return render(request, 'tickets/admin/purchase_approvals_history.html', context)


@login_required
def pending_approvals_view(request):
    """View para mostrar aprovações pendentes do usuário"""
    
    # Buscar aprovações pendentes do usuário
    approvals = PurchaseOrderApproval.objects.filter(
        status='PENDING'
    ).select_related(
        'ticket', 'ticket__category', 'ticket__created_by', 
        'ticket__assigned_to', 'approver', 'approver__user'
    ).order_by('-created_at')
    
    # Filtrar apenas as aprovações que o usuário pode ver
    if not request.user.is_superuser:
        # Se não é superadmin, mostra apenas suas aprovações ou do seu setor
        if request.user.hierarchy in ['SUPERVISOR', 'GERENTE']:
            # Supervisores e gerentes podem ver aprovações do seu setor
            approvals = approvals.filter(
                Q(approver__user=request.user) |
                Q(ticket__created_by__sector=request.user.sector)
            )
        else:
            # Outros usuários veem apenas suas próprias aprovações
            approvals = approvals.filter(approver__user=request.user)
    
    # Aplicar filtros se fornecidos
    ticket_filter = request.GET.get('ticket', '')
    approver_filter = request.GET.get('approver', '')
    
    if ticket_filter:
        approvals = approvals.filter(
            Q(ticket__id__icontains=ticket_filter) |
            Q(ticket__title__icontains=ticket_filter)
        )
    
    if approver_filter:
        approvals = approvals.filter(approver_id=approver_filter)
    
    # Paginação
    paginator = Paginator(approvals, 20)
    page_number = request.GET.get('page')
    approvals_page = paginator.get_page(page_number)
    
    # Estatísticas rápidas
    my_pending = approvals.filter(approver__user=request.user).count()
    total_pending = approvals.count() if request.user.is_superuser else my_pending
    
    # Dados para filtros
    available_approvers = User.objects.filter(
        purchase_approval_config__isnull=False,
        purchase_approval_config__is_active=True
    ).order_by('first_name', 'last_name')
    
    # Se não é superadmin, limitar aos aprovadores relevantes
    if not request.user.is_superuser:
        if request.user.hierarchy in ['SUPERVISOR', 'GERENTE']:
            available_approvers = available_approvers.filter(
                Q(id=request.user.id) | Q(sector=request.user.sector)
            )
        else:
            available_approvers = available_approvers.filter(id=request.user.id)
    
    context = {
        'approvals': approvals_page,
        'available_approvers': available_approvers,
        'my_pending_count': my_pending,
        'total_pending_count': total_pending,
        'page_title': 'Aprovações Pendentes'
    }
    
    return render(request, 'tickets/admin/pending_approvals.html', context)