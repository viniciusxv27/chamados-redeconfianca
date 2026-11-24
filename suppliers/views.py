import re

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
    from users.module_access import user_has_module
    return (user.groups.filter(name='Gestores de Fornecedores').exists()
            or user_has_module(user, 'fornecedores'))


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
            Q(services__icontains=search) |
            Q(categoria__icontains=search)
        )
    categoria = (request.GET.get('categoria') or '').strip()
    if categoria:
        suppliers = suppliers.filter(categoria__iexact=categoria)
    
    # Paginação
    paginator = Paginator(suppliers, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search': search,
        'categoria': categoria,
        'categorias': lista_de_categorias(),
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


def _valor_reais(texto):
    """'1.234,56' / '1234.56' / 'R$ 50' → Decimal; vazio → None. Valor ruim levanta ValueError."""
    from decimal import Decimal, InvalidOperation
    t = (texto or '').strip().replace('R$', '').replace(' ', '')
    if not t:
        return None
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    try:
        v = Decimal(t).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise ValueError(f'Valor inválido: "{texto}". Use números, como 1.234,56.')
    if v < 0:
        raise ValueError('O valor não pode ser negativo.')
    return v


def _condicoes(request):
    """Preço, frete, tempo de entrega e categoria do POST (todos opcionais).

    Preço sob consulta (25/11/2026) não guarda valor: o que vale é o "referente a".
    """
    sob_consulta = request.POST.get('preco_tipo') == 'consulta'
    return {
        'preco': None if sob_consulta else _valor_reais(request.POST.get('preco')),
        'preco_sob_consulta': sob_consulta,
        'preco_referencia': ' '.join((request.POST.get('preco_referencia') or '').split())[:200],
        'frete': _valor_reais(request.POST.get('frete')),
        'tempo_entrega': (request.POST.get('tempo_entrega') or '').strip()[:120],
        'categoria': padronizar_categoria(request.POST.get('categoria')),
    }


# Categoria (25/11/2026): o mesmo jeito do gasto nos cartões — um select com as que já existem e
# "+ Criar nova". Grafias do mesmo nome ("Manutenção", "manutencao ") viram uma, com a mais usada,
# e a digitada que já existe com outra grafia é gravada com a de sempre.
def _chave_categoria(texto):
    import unicodedata
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c)).lower()
    return re.sub(r'[^0-9a-z]', '', texto)


def categorias_existentes():
    """{chave: grafia} das categorias dos fornecedores ativos."""
    from django.db.models import Count
    contagem = {}
    for nome, n in (Supplier.objects.filter(is_active=True).exclude(categoria='')
                    .values_list('categoria').annotate(n=Count('id'))):
        nome = ' '.join(nome.split())
        if nome:
            contagem[nome] = contagem.get(nome, 0) + n
    por_chave = {}
    for nome, n in contagem.items():
        chave = _chave_categoria(nome)
        if chave:
            por_chave.setdefault(chave, []).append((n, not nome.isupper(), nome))
    return {chave: max(grafias)[2] for chave, grafias in por_chave.items()}


def padronizar_categoria(texto):
    texto = ' '.join(str(texto or '').split())[:120]
    if not texto:
        return ''
    return categorias_existentes().get(_chave_categoria(texto), texto)


def lista_de_categorias():
    return sorted(categorias_existentes().values(), key=_chave_categoria)


def _valores_do_form(supplier=None, post=None):
    """O que o formulário mostra nos campos de categoria e preço (cadastro novo, volta com erro ou edição)."""
    if post is not None:
        return {'categoria': post.get('categoria', ''), 'preco_tipo': post.get('preco_tipo') or 'fixo',
                'preco': post.get('preco', ''), 'preco_referencia': post.get('preco_referencia', ''),
                'frete': post.get('frete', ''), 'tempo_entrega': post.get('tempo_entrega', '')}
    if supplier is not None:
        fmt = lambda v: '' if v is None else f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        return {'categoria': supplier.categoria, 'preco_tipo': 'consulta' if supplier.preco_sob_consulta else 'fixo',
                'preco': fmt(supplier.preco), 'preco_referencia': supplier.preco_referencia,
                'frete': fmt(supplier.frete), 'tempo_entrega': supplier.tempo_entrega}
    return {'preco_tipo': 'fixo'}


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
                created_by=request.user,
                **_condicoes(request),
            )
            supplier.full_clean()
            supplier.save()
            
            messages.success(request, f'Fornecedor "{supplier.name}" criado com sucesso!')
            return redirect('suppliers:supplier_detail', pk=supplier.pk)
        
        except Exception as e:
            messages.error(request, f'Erro ao criar fornecedor: {str(e)}')
            # Preservar dados do formulário
            context = {
                'form_data': request.POST, 'v': _valores_do_form(post=request.POST),
                'categorias': lista_de_categorias(),
            }
            return render(request, 'suppliers/supplier_create.html', context)
    
    return render(request, 'suppliers/supplier_create.html', {'v': _valores_do_form(),
                                                              'categorias': lista_de_categorias()})


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
            for campo, valor in _condicoes(request).items():
                setattr(supplier, campo, valor)
            
            supplier.full_clean()
            supplier.save()
            
            messages.success(request, f'Fornecedor "{supplier.name}" atualizado com sucesso!')
            return redirect('suppliers:supplier_detail', pk=supplier.pk)
        
        except Exception as e:
            messages.error(request, f'Erro ao atualizar fornecedor: {str(e)}')
            return render(request, 'suppliers/supplier_edit.html', {
                'supplier': supplier, 'v': _valores_do_form(post=request.POST), 'categorias': lista_de_categorias()})
    
    context = {
        'supplier': supplier, 'v': _valores_do_form(supplier), 'categorias': lista_de_categorias(),
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