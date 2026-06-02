"""Views do módulo de Pedidos (Criação de pedidos) dentro de Suprimentos.

Fluxo (inspirado no app de prêmios):
- Usuários do grupo "GERENTES" (e superiores) fazem requisições/pedidos.
- Usuários da hierarquia SUPERVISOR para cima aprovam/reprovam, cadastram
  produtos e marcam pedidos como entregues.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Order, OrderItem, OrderProduct, OrderProductCategory


# ---------------------------------------------------------------------------
# Permissões
# ---------------------------------------------------------------------------
def can_request_orders(user):
    """Quem pode fazer pedidos: grupo GERENTES ou hierarquia SUPERVISOR+."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.hierarchy in ('SUPERVISOR', 'ADMIN', 'SUPERADMIN'):
        return True
    return user.communication_groups.filter(name__iexact='GERENTES').exists()


def can_manage_orders(user):
    """Quem aprova/reprova, entrega e cadastra produtos: SUPERVISOR+."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.hierarchy in ('SUPERVISOR', 'ADMIN', 'SUPERADMIN')


# ---------------------------------------------------------------------------
# Catálogo / criação de pedidos (solicitante)
# ---------------------------------------------------------------------------
@login_required
def order_catalog(request):
    """Vitrine de produtos onde o solicitante monta um pedido."""
    if not can_request_orders(request.user):
        messages.error(request, 'Você não tem permissão para fazer pedidos.')
        return redirect('assets:inventory_dashboard')

    categories = OrderProductCategory.objects.filter(is_active=True)
    products = OrderProduct.objects.filter(is_active=True).select_related('category')

    category_filter = request.GET.get('category')
    search = (request.GET.get('search') or '').strip()
    if category_filter:
        products = products.filter(category_id=category_filter)
    if search:
        products = products.filter(name__icontains=search)

    context = {
        'products': products,
        'categories': categories,
        'current_category': category_filter,
        'search': search,
        'can_manage': can_manage_orders(request.user),
    }
    return render(request, 'assets/orders/catalog.html', context)


@login_required
@require_POST
def order_create(request):
    """Cria um pedido com um ou mais itens."""
    if not can_request_orders(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissão para fazer pedidos.'}, status=403)

    product_ids = request.POST.getlist('product_id')
    quantities = request.POST.getlist('quantity')
    notes = (request.POST.get('notes') or '').strip()

    items = []
    for pid, qty in zip(product_ids, quantities):
        try:
            qty_int = int(qty)
        except (TypeError, ValueError):
            qty_int = 0
        if qty_int <= 0:
            continue
        product = OrderProduct.objects.filter(pk=pid, is_active=True).first()
        if product:
            items.append((product, qty_int))

    if not items:
        messages.error(request, 'Selecione ao menos um produto com quantidade válida.')
        return redirect('assets:order_catalog')

    with transaction.atomic():
        order = Order.objects.create(requested_by=request.user, notes=notes)
        OrderItem.objects.bulk_create([
            OrderItem(order=order, product=product, quantity=qty)
            for product, qty in items
        ])

    messages.success(request, f'Pedido #{order.pk} enviado para aprovação.')
    return redirect('assets:order_detail', pk=order.pk)


@login_required
def my_orders(request):
    """Lista os pedidos do próprio usuário."""
    orders = Order.objects.filter(requested_by=request.user).prefetch_related('items__product')

    status_filter = (request.GET.get('status') or '').strip()
    if status_filter:
        orders = orders.filter(status=status_filter)

    paginator = Paginator(orders, 15)
    page = paginator.get_page(request.GET.get('page'))

    context = {
        'orders': page,
        'status_choices': Order.STATUS_CHOICES,
        'status_filter': status_filter,
        'can_manage': can_manage_orders(request.user),
    }
    return render(request, 'assets/orders/my_orders.html', context)


@login_required
def order_detail(request, pk):
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product'), pk=pk
    )
    # Solicitante vê o próprio pedido; gestores veem todos.
    if order.requested_by_id != request.user.id and not can_manage_orders(request.user):
        messages.error(request, 'Você não tem permissão para ver este pedido.')
        return redirect('assets:my_orders')

    context = {
        'order': order,
        'can_manage': can_manage_orders(request.user),
    }
    return render(request, 'assets/orders/detail.html', context)


# ---------------------------------------------------------------------------
# Gestão de pedidos (SUPERVISOR+)
# ---------------------------------------------------------------------------
@login_required
def manage_orders(request):
    if not can_manage_orders(request.user):
        messages.error(request, 'Você não tem permissão para gerenciar pedidos.')
        return redirect('assets:inventory_dashboard')

    orders = Order.objects.select_related('requested_by').prefetch_related('items__product')

    status_filter = (request.GET.get('status') or '').strip()
    search = (request.GET.get('search') or '').strip()
    if status_filter:
        orders = orders.filter(status=status_filter)
    if search:
        orders = orders.filter(
            Q(requested_by__first_name__icontains=search)
            | Q(requested_by__last_name__icontains=search)
            | Q(requested_by__email__icontains=search)
        )

    counts = {
        'pendentes': Order.objects.filter(status=Order.STATUS_PENDENTE).count(),
        'aprovados': Order.objects.filter(status=Order.STATUS_APROVADO).count(),
        'entregues': Order.objects.filter(status=Order.STATUS_ENTREGUE).count(),
        'reprovados': Order.objects.filter(status=Order.STATUS_REPROVADO).count(),
    }

    paginator = Paginator(orders, 20)
    page = paginator.get_page(request.GET.get('page'))

    context = {
        'orders': page,
        'status_choices': Order.STATUS_CHOICES,
        'status_filter': status_filter,
        'search': search,
        'counts': counts,
    }
    return render(request, 'assets/orders/manage_orders.html', context)


@login_required
@require_POST
def order_approve(request, pk):
    if not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('assets:manage_orders')
    order = get_object_or_404(Order, pk=pk)
    if not order.can_review:
        messages.warning(request, 'Este pedido já foi avaliado.')
        return redirect('assets:order_detail', pk=pk)
    order.status = Order.STATUS_APROVADO
    order.reviewed_by = request.user
    order.reviewed_at = timezone.now()
    order.review_notes = (request.POST.get('review_notes') or '').strip()
    order.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes', 'updated_at'])
    messages.success(request, f'Pedido #{order.pk} aprovado.')
    return redirect('assets:order_detail', pk=pk)


@login_required
@require_POST
def order_reject(request, pk):
    if not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('assets:manage_orders')
    order = get_object_or_404(Order, pk=pk)
    if not order.can_review:
        messages.warning(request, 'Este pedido já foi avaliado.')
        return redirect('assets:order_detail', pk=pk)
    order.status = Order.STATUS_REPROVADO
    order.reviewed_by = request.user
    order.reviewed_at = timezone.now()
    order.review_notes = (request.POST.get('review_notes') or '').strip()
    order.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_notes', 'updated_at'])
    messages.success(request, f'Pedido #{order.pk} reprovado.')
    return redirect('assets:order_detail', pk=pk)


@login_required
@require_POST
def order_deliver(request, pk):
    if not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('assets:manage_orders')
    order = get_object_or_404(Order, pk=pk)
    if not order.can_deliver:
        messages.warning(request, 'Só é possível entregar pedidos aprovados.')
        return redirect('assets:order_detail', pk=pk)
    order.status = Order.STATUS_ENTREGUE
    order.delivered_by = request.user
    order.delivered_at = timezone.now()
    order.delivery_notes = (request.POST.get('delivery_notes') or '').strip()
    order.save(update_fields=['status', 'delivered_by', 'delivered_at', 'delivery_notes', 'updated_at'])
    messages.success(request, f'Pedido #{order.pk} marcado como entregue.')
    return redirect('assets:order_detail', pk=pk)


@login_required
@require_POST
def order_cancel(request, pk):
    order = get_object_or_404(Order, pk=pk)
    is_owner = order.requested_by_id == request.user.id
    if not is_owner and not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('assets:my_orders')
    if not order.can_cancel:
        messages.warning(request, 'Este pedido não pode mais ser cancelado.')
        return redirect('assets:order_detail', pk=pk)
    order.status = Order.STATUS_CANCELADO
    order.save(update_fields=['status', 'updated_at'])
    messages.success(request, f'Pedido #{order.pk} cancelado.')
    return redirect('assets:order_detail', pk=pk)


# ---------------------------------------------------------------------------
# Cadastro de produtos (SUPERVISOR+)
# ---------------------------------------------------------------------------
@login_required
def order_product_list(request):
    if not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão para gerenciar produtos.')
        return redirect('assets:inventory_dashboard')

    products = OrderProduct.objects.select_related('category').all()
    categories = OrderProductCategory.objects.all()

    search = (request.GET.get('search') or '').strip()
    if search:
        products = products.filter(name__icontains=search)

    context = {
        'products': products,
        'categories': categories,
        'search': search,
    }
    return render(request, 'assets/orders/product_list.html', context)


@login_required
def order_product_create(request):
    if not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão para cadastrar produtos.')
        return redirect('assets:inventory_dashboard')

    categories = OrderProductCategory.objects.filter(is_active=True)
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'Informe o nome do produto.')
            return redirect('assets:order_product_create')
        OrderProduct.objects.create(
            name=name,
            description=(request.POST.get('description') or '').strip(),
            category_id=request.POST.get('category') or None,
            unit=(request.POST.get('unit') or 'Unidade').strip(),
            is_active=request.POST.get('is_active') == 'on',
            image=request.FILES.get('image'),
            created_by=request.user,
        )
        messages.success(request, 'Produto cadastrado com sucesso.')
        return redirect('assets:order_product_list')

    return render(request, 'assets/orders/product_form.html', {
        'categories': categories,
        'product': None,
    })


@login_required
def order_product_edit(request, pk):
    if not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão para editar produtos.')
        return redirect('assets:inventory_dashboard')

    product = get_object_or_404(OrderProduct, pk=pk)
    categories = OrderProductCategory.objects.filter(is_active=True)
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'Informe o nome do produto.')
            return redirect('assets:order_product_edit', pk=pk)
        product.name = name
        product.description = (request.POST.get('description') or '').strip()
        product.category_id = request.POST.get('category') or None
        product.unit = (request.POST.get('unit') or 'Unidade').strip()
        product.is_active = request.POST.get('is_active') == 'on'
        if request.FILES.get('image'):
            product.image = request.FILES['image']
        product.save()
        messages.success(request, 'Produto atualizado.')
        return redirect('assets:order_product_list')

    return render(request, 'assets/orders/product_form.html', {
        'categories': categories,
        'product': product,
    })


@login_required
@require_POST
def order_product_delete(request, pk):
    if not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('assets:inventory_dashboard')
    product = get_object_or_404(OrderProduct, pk=pk)
    product.is_active = False
    product.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, 'Produto desativado.')
    return redirect('assets:order_product_list')


@login_required
@require_POST
def order_category_create(request):
    if not can_manage_orders(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('assets:inventory_dashboard')
    name = (request.POST.get('name') or '').strip()
    if name:
        OrderProductCategory.objects.create(
            name=name,
            icon=(request.POST.get('icon') or 'fas fa-box-open').strip(),
            color=(request.POST.get('color') or 'blue').strip(),
        )
        messages.success(request, 'Categoria criada.')
    else:
        messages.error(request, 'Informe o nome da categoria.')
    return redirect('assets:order_product_list')
