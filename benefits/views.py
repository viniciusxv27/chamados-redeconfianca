from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db import IntegrityError
from .models import Benefit, BenefitRedeem


@login_required
def benefits_list(request):
    """Lista todos os benefícios ativos"""
    today = timezone.now().date()
    
    # Buscar benefícios ativos
    benefits = Benefit.objects.filter(
        status='active'
    ).filter(
        models.Q(valid_from__isnull=True) | models.Q(valid_from__lte=today)
    ).filter(
        models.Q(valid_until__isnull=True) | models.Q(valid_until__gte=today)
    ).order_by('-is_featured', '-created_at')
    
    # Verificar quais benefícios o usuário já resgatou
    user_redeemed_ids = BenefitRedeem.objects.filter(
        user=request.user
    ).values_list('benefit_id', flat=True)
    
    context = {
        'benefits': benefits,
        'user_redeemed_ids': list(user_redeemed_ids),
    }
    
    return render(request, 'benefits/list.html', context)


@login_required
def benefit_detail(request, benefit_id):
    """Visualizar detalhes de um benefício"""
    benefit = get_object_or_404(Benefit, id=benefit_id, status='active')
    
    # Incrementar visualizações
    benefit.increment_views()
    
    # Verificar se o usuário já resgatou
    has_redeemed = BenefitRedeem.objects.filter(
        benefit=benefit,
        user=request.user
    ).exists()
    
    context = {
        'benefit': benefit,
        'has_redeemed': has_redeemed,
    }
    
    return render(request, 'benefits/detail.html', context)


@login_required
def redeem_benefit(request, benefit_id):
    """Resgatar um benefício"""
    if request.method != 'POST':
        return redirect('benefits:list')
    
    benefit = get_object_or_404(Benefit, id=benefit_id, status='active')
    
    try:
        # Criar registro de resgate
        redeem = BenefitRedeem.objects.create(
            benefit=benefit,
            user=request.user
        )
        
        # Incrementar contador de resgates
        benefit.increment_redeems()
        
        messages.success(
            request,
            f'🎉 Benefício resgatado com sucesso! Seu cupom: <strong>{benefit.coupon_code}</strong>',
            extra_tags='safe'
        )
        
    except IntegrityError:
        # Usuário já resgatou este benefício
        messages.warning(request, '⚠️ Você já resgatou este benefício anteriormente.')
    
    return redirect('benefits:detail', benefit_id=benefit_id)


# Views de administração (apenas para ADMIN e SUPERADMIN)

@login_required
def admin_benefits_list(request):
    """Lista todos os benefícios (admin)"""
    # Verificar permissão
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN'])):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('benefits:list')
    
    benefits = Benefit.objects.all().order_by('-created_at')
    
    context = {
        'benefits': benefits,
    }
    
    return render(request, 'benefits/admin_list.html', context)


@login_required
def admin_create_benefit(request):
    """Criar novo benefício (admin)"""
    # Verificar permissão
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN'])):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('benefits:list')
    
    if request.method == 'POST':
        # Processar formulário
        title = request.POST.get('title')
        description = request.POST.get('description')
        full_description = request.POST.get('full_description')
        coupon_code = request.POST.get('coupon_code')
        status = request.POST.get('status', 'active')
        is_featured = request.POST.get('is_featured') == 'on'
        valid_from = request.POST.get('valid_from') or None
        valid_until = request.POST.get('valid_until') or None
        image = request.FILES.get('image')
        
        # Validações
        if not all([title, description, full_description, coupon_code]):
            messages.error(request, '❌ Todos os campos obrigatórios devem ser preenchidos.')
            return render(request, 'benefits/admin_form.html')
        
        # Criar benefício
        benefit = Benefit.objects.create(
            title=title,
            description=description,
            full_description=full_description,
            coupon_code=coupon_code,
            status=status,
            is_featured=is_featured,
            valid_from=valid_from,
            valid_until=valid_until,
            image=image,
            created_by=request.user
        )
        
        messages.success(request, f'✅ Benefício "{benefit.title}" criado com sucesso!')
        return redirect('benefits:admin_list')
    
    context = {
        'action': 'create',
    }
    
    return render(request, 'benefits/admin_form.html', context)


@login_required
def admin_edit_benefit(request, benefit_id):
    """Editar benefício (admin)"""
    # Verificar permissão
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN'])):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('benefits:list')
    
    benefit = get_object_or_404(Benefit, id=benefit_id)
    
    if request.method == 'POST':
        # Processar formulário
        benefit.title = request.POST.get('title')
        benefit.description = request.POST.get('description')
        benefit.full_description = request.POST.get('full_description')
        benefit.coupon_code = request.POST.get('coupon_code')
        benefit.status = request.POST.get('status', 'active')
        benefit.is_featured = request.POST.get('is_featured') == 'on'
        benefit.valid_from = request.POST.get('valid_from') or None
        benefit.valid_until = request.POST.get('valid_until') or None
        
        # Atualizar imagem se foi enviada
        if request.FILES.get('image'):
            benefit.image = request.FILES.get('image')
        
        # Validações
        if not all([benefit.title, benefit.description, benefit.full_description, benefit.coupon_code]):
            messages.error(request, '❌ Todos os campos obrigatórios devem ser preenchidos.')
            return render(request, 'benefits/admin_form.html', {'benefit': benefit, 'action': 'edit'})
        
        benefit.save()
        
        messages.success(request, f'✅ Benefício "{benefit.title}" atualizado com sucesso!')
        return redirect('benefits:admin_list')
    
    context = {
        'benefit': benefit,
        'action': 'edit',
    }
    
    return render(request, 'benefits/admin_form.html', context)


@login_required
def admin_delete_benefit(request, benefit_id):
    """Deletar benefício (admin)"""
    # Verificar permissão
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN'])):
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('benefits:list')
    
    if request.method == 'POST':
        benefit = get_object_or_404(Benefit, id=benefit_id)
        benefit_title = benefit.title
        benefit.delete()
        
        messages.success(request, f'🗑️ Benefício "{benefit_title}" deletado com sucesso!')
    
    return redirect('benefits:admin_list')


# Importar models para usar Q
from django.db import models

