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
    # Verificar permissão - ADMIN, SUPERADMIN, SUPERVISOR e ADMINISTRATIVO podem gerenciar
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO'])):
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
    # Verificar permissão - ADMIN, SUPERADMIN, SUPERVISOR e ADMINISTRATIVO podem gerenciar
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO'])):
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
    # Verificar permissão - ADMIN, SUPERADMIN, SUPERVISOR e ADMINISTRATIVO podem gerenciar
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO'])):
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
    # Verificar permissão - ADMIN, SUPERADMIN, SUPERVISOR e ADMINISTRATIVO podem gerenciar
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO'])):
        messages.error(request, 'Você não tem permissão para realizar esta ação.')
        return redirect('benefits:list')
    
    if request.method == 'POST':
        benefit = get_object_or_404(Benefit, id=benefit_id)
        benefit_title = benefit.title
        benefit.delete()
        
        messages.success(request, f'🗑️ Benefício "{benefit_title}" deletado com sucesso!')
    
    return redirect('benefits:admin_list')


@login_required
def admin_history(request):
    """Histórico de resgates de benefícios (para supervisores+)"""
    # Verificar permissão
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO'])):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('benefits:list')
    
    from django.db.models import Count, Q
    from django.db.models.functions import TruncDate
    
    # Filtros
    benefit_filter = request.GET.get('benefit', '')
    user_filter = request.GET.get('user', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Todos os resgates
    redeems = BenefitRedeem.objects.select_related('benefit', 'user', 'user__sector').order_by('-redeemed_at')
    
    # Aplicar filtros
    if benefit_filter:
        redeems = redeems.filter(benefit_id=benefit_filter)
    if user_filter:
        redeems = redeems.filter(
            Q(user__first_name__icontains=user_filter) | 
            Q(user__last_name__icontains=user_filter) |
            Q(user__email__icontains=user_filter)
        )
    if date_from:
        redeems = redeems.filter(redeemed_at__date__gte=date_from)
    if date_to:
        redeems = redeems.filter(redeemed_at__date__lte=date_to)
    
    # Estatísticas gerais
    total_redeems = BenefitRedeem.objects.count()
    unique_users = BenefitRedeem.objects.values('user').distinct().count()
    
    # Benefícios mais resgatados
    most_redeemed = Benefit.objects.annotate(
        total_redeems=Count('redeems')
    ).filter(total_redeems__gt=0).order_by('-total_redeems')[:10]
    
    # Usuários que mais resgataram
    from users.models import User
    top_users = User.objects.annotate(
        total_redeems=Count('benefit_redeems')
    ).filter(total_redeems__gt=0).order_by('-total_redeems')[:10]
    
    # Resgates por dia (últimos 30 dias)
    from datetime import timedelta
    thirty_days_ago = timezone.now() - timedelta(days=30)
    redeems_by_day = BenefitRedeem.objects.filter(
        redeemed_at__gte=thirty_days_ago
    ).annotate(
        date=TruncDate('redeemed_at')
    ).values('date').annotate(
        count=Count('id')
    ).order_by('date')
    
    # Lista de benefícios para o filtro
    all_benefits = Benefit.objects.all().order_by('title')
    
    context = {
        'redeems': redeems[:100],  # Limitar a 100 registros
        'total_redeems': total_redeems,
        'unique_users': unique_users,
        'most_redeemed': most_redeemed,
        'top_users': top_users,
        'redeems_by_day': list(redeems_by_day),
        'all_benefits': all_benefits,
        'filters': {
            'benefit': benefit_filter,
            'user': user_filter,
            'date_from': date_from,
            'date_to': date_to,
        }
    }
    
    return render(request, 'benefits/admin_history.html', context)


@login_required
def admin_benefit_history(request, benefit_id):
    """Histórico de resgates de um benefício específico"""
    # Verificar permissão
    if not (request.user.is_superuser or (hasattr(request.user, 'hierarchy') and request.user.hierarchy in ['ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO'])):
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('benefits:list')
    
    benefit = get_object_or_404(Benefit, id=benefit_id)
    redeems = BenefitRedeem.objects.filter(benefit=benefit).select_related('user', 'user__sector').order_by('-redeemed_at')
    
    context = {
        'benefit': benefit,
        'redeems': redeems,
    }
    
    return render(request, 'benefits/admin_benefit_history.html', context)


# Importar models para usar Q
from django.db import models

