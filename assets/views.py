from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from django.core.paginator import Paginator
from .models import Asset
from .forms import AssetForm


@login_required
def asset_list(request):
    """Lista todos os ativos com funcionalidade de busca e paginação"""
    query = request.GET.get('q', '')
    estado_filter = request.GET.get('estado', '')
    setor_filter = request.GET.get('setor', '')
    
    assets = Asset.objects.all()
    
    # Filtros
    if query:
        assets = assets.filter(
            Q(patrimonio_numero__icontains=query) |
            Q(nome__icontains=query) |
            Q(localizado__icontains=query) |
            Q(setor__icontains=query) |
            Q(pdv__icontains=query)
        )
    
    if estado_filter:
        assets = assets.filter(estado_fisico=estado_filter)
    
    if setor_filter:
        assets = assets.filter(setor__icontains=setor_filter)
    
    # Paginação
    paginator = Paginator(assets, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Para os filtros
    estados = Asset.ESTADO_FISICO_CHOICES
    setores = Asset.objects.values_list('setor', flat=True).distinct().order_by('setor')
    
    context = {
        'page_obj': page_obj,
        'query': query,
        'estado_filter': estado_filter,
        'setor_filter': setor_filter,
        'estados': estados,
        'setores': setores,
        'total_assets': assets.count(),
    }
    
    return render(request, 'assets/list.html', context)


@login_required
def asset_detail(request, pk):
    """Exibe detalhes de um ativo específico"""
    asset = get_object_or_404(Asset, pk=pk)
    
    context = {
        'asset': asset,
    }
    
    return render(request, 'assets/detail.html', context)


@login_required
def asset_create(request):
    """Cria um novo ativo"""
    if request.method == 'POST':
        form = AssetForm(request.POST)
        if form.is_valid():
            asset = form.save(commit=False)
            asset.created_by = request.user
            asset.save()
            messages.success(request, f'Ativo {asset.patrimonio_numero} criado com sucesso!')
            return redirect('assets:detail', pk=asset.pk)
    else:
        form = AssetForm()
    
    context = {
        'form': form,
        'title': 'Cadastrar Novo Ativo',
    }
    
    return render(request, 'assets/form.html', context)


@login_required
def asset_edit(request, pk):
    """Edita um ativo existente"""
    asset = get_object_or_404(Asset, pk=pk)
    
    if request.method == 'POST':
        form = AssetForm(request.POST, instance=asset)
        if form.is_valid():
            form.save()
            messages.success(request, f'Ativo {asset.patrimonio_numero} atualizado com sucesso!')
            return redirect('assets:detail', pk=asset.pk)
    else:
        form = AssetForm(instance=asset)
    
    context = {
        'form': form,
        'asset': asset,
        'title': f'Editar Ativo - {asset.patrimonio_numero}',
    }
    
    return render(request, 'assets/form.html', context)


@login_required
def asset_delete(request, pk):
    """Deleta um ativo"""
    asset = get_object_or_404(Asset, pk=pk)
    
    if request.method == 'POST':
        patrimonio_numero = asset.patrimonio_numero
        asset.delete()
        messages.success(request, f'Ativo {patrimonio_numero} removido com sucesso!')
        return redirect('assets:list')
    
    context = {
        'asset': asset,
    }
    
    return render(request, 'assets/delete.html', context)
