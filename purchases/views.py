from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Purchase, PaymentMethod
from suppliers.models import Supplier


def user_can_manage_purchases(user):
    """Verifica se o usuário pode gerenciar compras"""
    if user.is_superuser:
        return True
    return user.groups.filter(name='Gestores de Compras').exists()


@login_required
def purchase_list(request):
    """Lista todas as compras com filtros e paginação"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    purchases = Purchase.objects.all()
    
    # Filtros
    search = request.GET.get('search')
    status_filter = request.GET.get('status')
    priority_filter = request.GET.get('priority')
    supplier_filter = request.GET.get('supplier')
    
    if search:
        purchases = purchases.filter(
            Q(description__icontains=search) |
            Q(supplier__name__icontains=search) |
            Q(notes__icontains=search)
        )
    
    if status_filter:
        purchases = purchases.filter(status=status_filter)
    
    if priority_filter:
        purchases = purchases.filter(priority=priority_filter)
    
    if supplier_filter:
        purchases = purchases.filter(supplier_id=supplier_filter)
    
    # Paginação
    paginator = Paginator(purchases, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Dados para filtros
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    
    context = {
        'page_obj': page_obj,
        'search': search,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'supplier_filter': supplier_filter,
        'suppliers': suppliers,
        'status_choices': Purchase.STATUS_CHOICES,
        'priority_choices': Purchase.PRIORITY_CHOICES,
        'total_purchases': Purchase.objects.count(),
    }
    
    return render(request, 'purchases/purchase_list.html', context)


@login_required
def purchase_detail(request, pk):
    """Detalhes de uma compra específica"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    purchase = get_object_or_404(Purchase, pk=pk)
    
    context = {
        'purchase': purchase,
    }
    
    return render(request, 'purchases/purchase_detail.html', context)


@login_required
def purchase_create(request):
    """Criar nova compra"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    
    if request.method == 'POST':
        try:
            # Obter fornecedor
            supplier_id = request.POST.get('supplier')
            supplier = get_object_or_404(Supplier, pk=supplier_id, is_active=True)
            
            # Calcular preço total se fornecidos os valores
            unit_price = request.POST.get('unit_price')
            quantity = request.POST.get('quantity', 1)
            
            unit_price_decimal = None
            total_price_decimal = None
            
            if unit_price:
                unit_price_decimal = float(unit_price)
                total_price_decimal = unit_price_decimal * int(quantity)
            
            # Pontos Livelo
            accumulated_livelo = request.POST.get('accumulated_livelo_points') == 'on'
            livelo_points = None
            if accumulated_livelo:
                livelo_points_str = request.POST.get('livelo_points_quantity')
                if livelo_points_str:
                    livelo_points = int(livelo_points_str)
            
            # Forma de Pagamento
            payment_method_id = request.POST.get('payment_method')
            payment_method = None
            if payment_method_id:
                payment_method = get_object_or_404(PaymentMethod, pk=payment_method_id, is_active=True)
            
            purchase = Purchase(
                supplier=supplier,
                description=request.POST.get('description'),
                quantity=int(quantity),
                unit_price=unit_price_decimal,
                total_price=total_price_decimal,
                priority=request.POST.get('priority', 'MEDIA'),
                expected_delivery=request.POST.get('expected_delivery') or None,
                notes=request.POST.get('notes', ''),
                accumulated_livelo_points=accumulated_livelo,
                livelo_points_quantity=livelo_points,
                payment_method=payment_method,
                requested_by=request.user
            )
            
            purchase.full_clean()
            purchase.save()
            
            messages.success(request, 'Compra criada com sucesso!')
            return redirect('purchases:purchase_detail', pk=purchase.pk)
        
        except Exception as e:
            messages.error(request, f'Erro ao criar compra: {str(e)}')
            # Preservar dados do formulário
            context = {
                'suppliers': suppliers,
                'form_data': request.POST,
                'status_choices': Purchase.STATUS_CHOICES,
                'priority_choices': Purchase.PRIORITY_CHOICES,
                'payment_methods': PaymentMethod.objects.filter(is_active=True).order_by('name'),
            }
            return render(request, 'purchases/purchase_create.html', context)
    
    context = {
        'suppliers': suppliers,
        'status_choices': Purchase.STATUS_CHOICES,
        'priority_choices': Purchase.PRIORITY_CHOICES,
        'payment_methods': PaymentMethod.objects.filter(is_active=True).order_by('name'),
    }
    
    return render(request, 'purchases/purchase_create.html', context)


@login_required
def purchase_edit(request, pk):
    """Editar compra existente"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    purchase = get_object_or_404(Purchase, pk=pk)
    suppliers = Supplier.objects.filter(is_active=True).order_by('name')
    
    if request.method == 'POST':
        try:
            # Obter fornecedor
            supplier_id = request.POST.get('supplier')
            supplier = get_object_or_404(Supplier, pk=supplier_id, is_active=True)
            
            # Calcular preço total se fornecidos os valores
            unit_price = request.POST.get('unit_price')
            quantity = request.POST.get('quantity', 1)
            
            unit_price_decimal = None
            total_price_decimal = None
            
            if unit_price:
                unit_price_decimal = float(unit_price)
                total_price_decimal = unit_price_decimal * int(quantity)
            
            # Pontos Livelo
            accumulated_livelo = request.POST.get('accumulated_livelo_points') == 'on'
            livelo_points = None
            if accumulated_livelo:
                livelo_points_str = request.POST.get('livelo_points_quantity')
                if livelo_points_str:
                    livelo_points = int(livelo_points_str)
            
            # Forma de Pagamento
            payment_method_id = request.POST.get('payment_method')
            payment_method = None
            if payment_method_id:
                payment_method = get_object_or_404(PaymentMethod, pk=payment_method_id, is_active=True)
            
            # Atualizar campos
            purchase.supplier = supplier
            purchase.description = request.POST.get('description')
            purchase.quantity = int(quantity)
            purchase.unit_price = unit_price_decimal
            purchase.total_price = total_price_decimal
            purchase.status = request.POST.get('status')
            purchase.priority = request.POST.get('priority')
            purchase.expected_delivery = request.POST.get('expected_delivery') or None
            purchase.delivery_date = request.POST.get('delivery_date') or None
            purchase.notes = request.POST.get('notes', '')
            purchase.accumulated_livelo_points = accumulated_livelo
            purchase.livelo_points_quantity = livelo_points
            purchase.payment_method = payment_method
            
            purchase.full_clean()
            purchase.save()
            
            messages.success(request, 'Compra atualizada com sucesso!')
            return redirect('purchases:purchase_detail', pk=purchase.pk)
        
        except Exception as e:
            messages.error(request, f'Erro ao atualizar compra: {str(e)}')
    
    context = {
        'purchase': purchase,
        'suppliers': suppliers,
        'status_choices': Purchase.STATUS_CHOICES,
        'priority_choices': Purchase.PRIORITY_CHOICES,
        'payment_methods': PaymentMethod.objects.filter(is_active=True).order_by('name'),
    }
    
    return render(request, 'purchases/purchase_edit.html', context)


@login_required
def purchase_delete(request, pk):
    """Excluir compra"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    purchase = get_object_or_404(Purchase, pk=pk)
    
    if request.method == 'POST':
        try:
            purchase_description = purchase.description
            purchase.delete()
            messages.success(request, f'Compra "{purchase_description}" excluída com sucesso!')
            return redirect('purchases:purchase_list')
        except Exception as e:
            messages.error(request, f'Erro ao excluir compra: {str(e)}')
            return redirect('purchases:purchase_detail', pk=pk)
    
    # If GET request, redirect to detail page
    return redirect('purchases:purchase_detail', pk=pk)


# ===== PAYMENT METHODS CRUD =====

@login_required
def payment_method_list(request):
    """Lista todas as formas de pagamento"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    payment_methods = PaymentMethod.objects.all().order_by('name')
    
    # Filtro de status
    status_filter = request.GET.get('status', '')
    if status_filter == 'active':
        payment_methods = payment_methods.filter(is_active=True)
    elif status_filter == 'inactive':
        payment_methods = payment_methods.filter(is_active=False)
    
    context = {
        'payment_methods': payment_methods,
        'status_filter': status_filter,
        'total_count': PaymentMethod.objects.count(),
        'active_count': PaymentMethod.objects.filter(is_active=True).count(),
    }
    
    return render(request, 'purchases/payment_method_list.html', context)


@login_required
def payment_method_create(request):
    """Criar nova forma de pagamento"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()
            requires_approval = request.POST.get('requires_approval') == 'on'
            
            if not name:
                messages.error(request, 'O nome da forma de pagamento é obrigatório.')
                return redirect('purchases:payment_method_create')
            
            PaymentMethod.objects.create(
                name=name,
                description=description,
                requires_approval=requires_approval
            )
            
            messages.success(request, f'Forma de pagamento "{name}" criada com sucesso!')
            return redirect('purchases:payment_method_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar forma de pagamento: {str(e)}')
            return redirect('purchases:payment_method_create')
    
    return render(request, 'purchases/payment_method_form.html', {
        'is_edit': False
    })


@login_required
def payment_method_edit(request, pk):
    """Editar forma de pagamento"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    payment_method = get_object_or_404(PaymentMethod, pk=pk)
    
    if request.method == 'POST':
        try:
            name = request.POST.get('name', '').strip()
            description = request.POST.get('description', '').strip()
            is_active = request.POST.get('is_active') == 'on'
            requires_approval = request.POST.get('requires_approval') == 'on'
            
            if not name:
                messages.error(request, 'O nome da forma de pagamento é obrigatório.')
                return redirect('purchases:payment_method_edit', pk=pk)
            
            payment_method.name = name
            payment_method.description = description
            payment_method.is_active = is_active
            payment_method.requires_approval = requires_approval
            payment_method.save()
            
            messages.success(request, f'Forma de pagamento "{name}" atualizada com sucesso!')
            return redirect('purchases:payment_method_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao atualizar forma de pagamento: {str(e)}')
            return redirect('purchases:payment_method_edit', pk=pk)
    
    context = {
        'payment_method': payment_method,
        'is_edit': True
    }
    
    return render(request, 'purchases/payment_method_form.html', context)


@login_required
def payment_method_delete(request, pk):
    """Excluir forma de pagamento"""
    if not user_can_manage_purchases(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    payment_method = get_object_or_404(PaymentMethod, pk=pk)
    
    if request.method == 'POST':
        try:
            # Verificar se está em uso
            purchases_count = payment_method.purchases.count()
            if purchases_count > 0:
                messages.error(request, f'Não é possível excluir esta forma de pagamento pois ela está sendo usada em {purchases_count} compra(s).')
                return redirect('purchases:payment_method_list')
            
            name = payment_method.name
            payment_method.delete()
            messages.success(request, f'Forma de pagamento "{name}" excluída com sucesso!')
            return redirect('purchases:payment_method_list')
            
        except Exception as e:
            messages.error(request, f'Erro ao excluir forma de pagamento: {str(e)}')
            return redirect('purchases:payment_method_list')
    
    # If GET request, redirect to list
    return redirect('purchases:payment_method_list')
    return redirect('purchases:purchase_detail', pk=pk)