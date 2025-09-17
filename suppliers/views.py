from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from .models import Supplier


def user_can_manage_suppliers(user):
    """Verifica se o usuário pode gerenciar fornecedores"""
    if user.is_superuser:
        return True
    return user.groups.filter(name='Gestores de Fornecedores').exists()


@login_required
def supplier_list(request):
    """Lista todos os fornecedores com filtros e paginação"""
    if not user_can_manage_suppliers(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    suppliers = Supplier.objects.filter(is_active=True)
    
    # Filtros
    search = request.GET.get('search')
    if search:
        suppliers = suppliers.filter(
            Q(name__icontains=search) |
            Q(cnpj__icontains=search) |
            Q(contact__icontains=search) |
            Q(area_of_operation__icontains=search) |
            Q(services__icontains=search)
        )
    
    # Paginação
    paginator = Paginator(suppliers, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search': search,
        'total_suppliers': Supplier.objects.filter(is_active=True).count(),
    }
    
    return render(request, 'suppliers/supplier_list.html', context)


@login_required
def supplier_detail(request, pk):
    """Detalhes de um fornecedor específico"""
    if not user_can_manage_suppliers(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    supplier = get_object_or_404(Supplier, pk=pk, is_active=True)
    purchases = supplier.purchases.all()[:5]  # Últimas 5 compras
    
    context = {
        'supplier': supplier,
        'recent_purchases': purchases,
        'total_purchases': supplier.purchases.count(),
        'delivered_purchases': supplier.purchases.filter(status='ENTREGUE').count(),
    }
    
    return render(request, 'suppliers/supplier_detail.html', context)


@login_required
def supplier_create(request):
    """Criar novo fornecedor"""
    if not user_can_manage_suppliers(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    if request.method == 'POST':
        try:
            supplier = Supplier(
                name=request.POST.get('name'),
                cnpj=request.POST.get('cnpj'),
                contact=request.POST.get('contact'),
                area_of_operation=request.POST.get('area_of_operation'),
                services=request.POST.get('services'),
                created_by=request.user
            )
            supplier.full_clean()
            supplier.save()
            
            messages.success(request, f'Fornecedor "{supplier.name}" criado com sucesso!')
            return redirect('suppliers:supplier_detail', pk=supplier.pk)
        
        except Exception as e:
            messages.error(request, f'Erro ao criar fornecedor: {str(e)}')
            # Preservar dados do formulário
            context = {
                'form_data': request.POST
            }
            return render(request, 'suppliers/supplier_create.html', context)
    
    return render(request, 'suppliers/supplier_create.html')


@login_required
def supplier_edit(request, pk):
    """Editar fornecedor existente"""
    if not user_can_manage_suppliers(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    supplier = get_object_or_404(Supplier, pk=pk, is_active=True)
    
    if request.method == 'POST':
        try:
            supplier.name = request.POST.get('name')
            supplier.cnpj = request.POST.get('cnpj')
            supplier.contact = request.POST.get('contact')
            supplier.area_of_operation = request.POST.get('area_of_operation')
            supplier.services = request.POST.get('services')
            
            supplier.full_clean()
            supplier.save()
            
            messages.success(request, f'Fornecedor "{supplier.name}" atualizado com sucesso!')
            return redirect('suppliers:supplier_detail', pk=supplier.pk)
        
        except Exception as e:
            messages.error(request, f'Erro ao atualizar fornecedor: {str(e)}')
    
    context = {
        'supplier': supplier
    }
    
    return render(request, 'suppliers/supplier_edit.html', context)


@login_required
def supplier_delete(request, pk):
    """Desativar fornecedor (soft delete)"""
    if not user_can_manage_suppliers(request.user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('home')
    
    supplier = get_object_or_404(Supplier, pk=pk, is_active=True)
    
    if request.method == 'POST':
        supplier.is_active = False
        supplier.save()
        
        messages.success(request, f'Fornecedor "{supplier.name}" removido com sucesso!')
        return redirect('suppliers:supplier_list')
    
    context = {
        'supplier': supplier
    }
    
    return render(request, 'suppliers/supplier_delete.html', context)