from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from datetime import timedelta

from .models import Prize, PrizeCategory, Redemption, CSTransaction
from users.models import User


@login_required
def marketplace_view(request):
    """Marketplace de prêmios atualizado"""
    categories = PrizeCategory.objects.filter(active=True)
    
    # Filtros
    category_filter = request.GET.get('category')
    search = request.GET.get('search', '')
    
    prizes = Prize.objects.filter(is_active=True)
    
    if category_filter:
        prizes = prizes.filter(category_id=category_filter)
    
    if search:
        prizes = prizes.filter(name__icontains=search)
    
    # Ordenação por prioridade
    prizes = prizes.order_by('-priority', 'name')
    
    # Paginação
    paginator = Paginator(prizes, 12)
    page_number = request.GET.get('page')
    prizes_page = paginator.get_page(page_number)
    
    context = {
        'prizes': prizes_page,
        'categories': categories,
        'user_balance': request.user.balance_cs,
        'current_category': category_filter,
        'search': search,
    }
    return render(request, 'prizes/marketplace.html', context)


@login_required
@require_POST
def redeem_prize(request, prize_id):
    """Resgatar um prêmio"""
    prize = get_object_or_404(Prize, id=prize_id, is_active=True)
    
    if not prize.available:
        return JsonResponse({'success': False, 'error': 'Prêmio não disponível'})
    
    if request.user.balance_cs < prize.value_cs:
        return JsonResponse({'success': False, 'error': 'Saldo insuficiente'})
    
    try:
        with transaction.atomic():
            # Criar resgate
            redemption = Redemption.objects.create(
                user=request.user,
                prize=prize,
                status='PENDENTE'
            )
            
            # Debitar saldo
            request.user.balance_cs -= prize.value_cs
            request.user.save()
            
            # Registrar transação
            CSTransaction.objects.create(
                user=request.user,
                amount=-prize.value_cs,
                transaction_type='REDEMPTION',
                description=f'Resgate: {prize.name}',
                related_redemption=redemption,
                status='APPROVED',  # Resgates são aprovados automaticamente
                created_by=request.user
            )
            
            messages.success(request, f'Prêmio "{prize.name}" resgatado com sucesso! Aguardando aprovação.')
            return JsonResponse({'success': True})
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
def manage_prizes(request):
    """Gerenciar prêmios (admin)"""
    if not request.user.can_manage_users():
        messages.error(request, 'Acesso negado.')
        return redirect('marketplace')
    
    prizes = Prize.objects.all().order_by('-created_at')
    categories = PrizeCategory.objects.all()
    
    # Buscar resgates recentes (últimos 15 dias)
    fifteen_days_ago = timezone.now() - timedelta(days=15)
    recent_redemptions = Redemption.objects.filter(
        redeemed_at__gte=fifteen_days_ago
    ).select_related('user', 'prize').order_by('-redeemed_at')[:10]
    
    context = {
        'prizes': prizes,
        'categories': categories,
        'recent_redemptions': recent_redemptions,
    }
    return render(request, 'prizes/manage.html', context)


@login_required
def create_prize(request):
    """Criar novo prêmio"""
    if not request.user.can_manage_users():
        messages.error(request, 'Acesso negado.')
        return redirect('marketplace')
    
    if request.method == 'POST':
        try:
            prize = Prize.objects.create(
                name=request.POST['name'],
                description=request.POST['description'],
                category_id=request.POST.get('category') or None,
                value_cs=Decimal(request.POST['value_cs']),
                priority=request.POST.get('priority', 'NORMAL'),
                stock=int(request.POST.get('stock', 0)),
                unlimited_stock=request.POST.get('unlimited_stock') == 'on',
                terms=request.POST.get('terms', ''),
                valid_until=request.POST.get('valid_until') or None,
                created_by=request.user
            )
            
            if request.FILES.get('image'):
                prize.image = request.FILES['image']
                prize.save()
            
            messages.success(request, f'Prêmio "{prize.name}" criado com sucesso!')
            return redirect('manage_prizes')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar prêmio: {str(e)}')
    
    categories = PrizeCategory.objects.filter(active=True)
    return render(request, 'prizes/create.html', {'categories': categories})


@login_required
def manage_categories(request):
    """Gerenciar categorias de prêmios"""
    if not request.user.can_manage_users():
        messages.error(request, 'Acesso negado.')
        return redirect('marketplace')
    
    if request.method == 'POST':
        try:
            PrizeCategory.objects.create(
                name=request.POST['name'],
                description=request.POST.get('description', ''),
                icon=request.POST.get('icon', 'fas fa-gift'),
                color=request.POST.get('color', 'blue'),
            )
            messages.success(request, 'Categoria criada com sucesso!')
        except Exception as e:
            messages.error(request, f'Erro ao criar categoria: {str(e)}')
    
    categories = PrizeCategory.objects.all()
    return render(request, 'prizes/categories.html', {'categories': categories})


@login_required
def redemption_history(request):
    """Histórico de resgates do usuário"""
    redemptions = Redemption.objects.filter(user=request.user).order_by('-redeemed_at')
    
    paginator = Paginator(redemptions, 10)
    page_number = request.GET.get('page')
    redemptions_page = paginator.get_page(page_number)
    
    return render(request, 'prizes/redemption_history.html', {'redemptions': redemptions_page})


@login_required
def manage_redemptions(request):
    """Gerenciar resgates (admin)"""
    if not request.user.can_manage_users():
        messages.error(request, 'Acesso negado.')
        return redirect('marketplace')
    
    status_filter = request.GET.get('status', 'PENDENTE')
    redemptions = Redemption.objects.filter(status=status_filter).order_by('-redeemed_at')
    
    # Buscar resgates recentes (últimos 15 dias)
    from datetime import timedelta
    fifteen_days_ago = timezone.now() - timedelta(days=15)
    recent_redemptions = Redemption.objects.filter(
        redeemed_at__gte=fifteen_days_ago
    ).select_related('user', 'prize').order_by('-redeemed_at')[:10]
    
    paginator = Paginator(redemptions, 20)
    page_number = request.GET.get('page')
    redemptions_page = paginator.get_page(page_number)
    
    context = {
        'redemptions': redemptions_page,
        'current_status': status_filter,
        'status_choices': Redemption.STATUS_CHOICES,
        'recent_redemptions': recent_redemptions,
    }
    return render(request, 'prizes/manage_redemptions.html', context)


@login_required
def update_redemption_status(request, redemption_id):
    """Atualizar status do resgate"""
    if not request.user.can_manage_users():
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido'})
    
    redemption = get_object_or_404(Redemption, id=redemption_id)
    
    # Aceitar dados JSON ou form data
    if request.content_type == 'application/json':
        import json
        data = json.loads(request.body)
        new_status = data.get('status')
        delivery_notes = data.get('delivery_notes', '')
    else:
        new_status = request.POST.get('status')
        delivery_notes = request.POST.get('delivery_notes', '')
    
    if new_status not in dict(Redemption.STATUS_CHOICES):
        return JsonResponse({'success': False, 'error': 'Status inválido'})
    
    try:
        old_status = redemption.status
        redemption.status = new_status
        redemption.approved_by = request.user
        
        if new_status == 'APROVADO':
            redemption.approved_at = timezone.now()
            if delivery_notes:
                redemption.delivery_notes = delivery_notes
        elif new_status == 'ENTREGUE':
            redemption.delivered_at = timezone.now()
            if delivery_notes:
                redemption.delivery_notes = delivery_notes
        elif new_status == 'CANCELADO':
            if delivery_notes:
                redemption.notes = delivery_notes
        
        redemption.save()
        
        # Gerenciar estoque baseado no status
        if new_status == 'CANCELADO' and old_status != 'CANCELADO':
            # Se cancelado, devolver o C$ para o usuário
            redemption.user.balance_cs += redemption.prize.value_cs
            redemption.user.save()
            
            # Registrar transação de devolução
            CSTransaction.objects.create(
                user=redemption.user,
                amount=redemption.prize.value_cs,
                transaction_type='CREDIT',
                description=f'Devolução por cancelamento: {redemption.prize.name}',
                related_redemption=redemption,
                status='APPROVED',  # Devoluções são aprovadas automaticamente
                created_by=request.user
            )
        
        # Log da ação
        from core.middleware import log_action
        log_action(
            request.user,
            'REDEMPTION_STATUS_UPDATE',
            f'Status do resgate #{redemption.id} alterado de {old_status} para {new_status}',
            request
        )
        
        return JsonResponse({'success': True})
        
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@login_required
@require_POST
def cancel_redemption(request, redemption_id):
    """Cancelar resgate - Admin ou próprio usuário"""
    redemption = get_object_or_404(Redemption, id=redemption_id)
    
    # Verificar permissão: admin ou próprio usuário
    if not (request.user.can_manage_users() or redemption.user == request.user):
        return JsonResponse({'success': False, 'error': 'Acesso negado'})
    
    # Só pode cancelar se estiver pendente ou aprovado
    if redemption.status not in ['PENDENTE', 'APROVADO']:
        return JsonResponse({'success': False, 'error': 'Este resgate não pode ser cancelado'})
    
    try:
        with transaction.atomic():
            old_status = redemption.status
            redemption.status = 'CANCELADO'
            redemption.approved_by = request.user
            
            # Adicionar nota sobre quem cancelou
            if request.user == redemption.user:
                redemption.notes = f'Cancelado pelo próprio usuário em {timezone.now().strftime("%d/%m/%Y %H:%M")}'
            else:
                redemption.notes = f'Cancelado por {request.user.get_full_name()} em {timezone.now().strftime("%d/%m/%Y %H:%M")}'
            
            redemption.save()
            
            # Devolver o C$ para o usuário
            redemption.user.balance_cs += redemption.prize.value_cs
            redemption.user.save()
            
            # Registrar transação de devolução
            CSTransaction.objects.create(
                user=redemption.user,
                amount=redemption.prize.value_cs,
                transaction_type='REFUND',
                description=f'Devolução por cancelamento: {redemption.prize.name}',
                related_redemption=redemption,
                status='APPROVED',
                created_by=request.user
            )
            
            # Log da ação
            from core.middleware import log_action
            log_action(
                request.user,
                'REDEMPTION_CANCELLED',
                f'Resgate #{redemption.id} ({redemption.prize.name}) cancelado',
                request
            )
            
            return JsonResponse({
                'success': True, 
                'message': f'Resgate cancelado com sucesso! C${redemption.prize.value_cs} foi devolvido.'
            })
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})
