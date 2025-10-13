from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q, Avg, Count
from django.core.paginator import Paginator
from .models import Compliment
from users.models import User, Sector


@login_required
def compliments_dashboard(request):
    """Painel principal de elogios"""
    user = request.user
    
    # Elogios recebidos pelo usuário
    received_compliments = Compliment.objects.filter(
        to_user=user,
        is_active=True
    ).select_related('from_user').order_by('-created_at')
    
    # Elogios recebidos pelo setor do usuário
    if user.sector:
        sector_compliments = Compliment.objects.filter(
            to_sector=user.sector,
            is_active=True
        ).select_related('from_user').order_by('-created_at')[:5]
    else:
        sector_compliments = Compliment.objects.none()
    
    # Estatísticas do usuário
    user_stats = {
        'total_received': received_compliments.count(),
        'avg_rating': received_compliments.aggregate(avg=Avg('rating'))['avg'] or 0,
        'total_given': user.compliments_given.filter(is_active=True).count(),
        'rating_distribution': {
            i: received_compliments.filter(rating=i).count() 
            for i in range(1, 6)
        }
    }
    
    # Estatísticas do setor
    sector_stats = {}
    if user.sector:
        sector_stats = {
            'total_received': sector_compliments.count(),
            'avg_rating': sector_compliments.aggregate(avg=Avg('rating'))['avg'] or 0,
        }
    
    context = {
        'received_compliments': received_compliments[:10],  # Primeiros 10
        'sector_compliments': sector_compliments[:5],  # Primeiros 5
        'user_stats': user_stats,
        'sector_stats': sector_stats,
    }
    return render(request, 'compliments/dashboard.html', context)


@login_required
def create_compliment(request):
    """Criar novo elogio"""
    if request.method == 'POST':
        from_user = request.user
        target_type = request.POST.get('target_type')  # 'user' ou 'sector'
        target_id = request.POST.get('target_id')
        rating = request.POST.get('rating')
        comment = request.POST.get('comment')
        
        # Validações
        if not all([target_type, target_id, rating, comment]):
            messages.error(request, 'Todos os campos são obrigatórios.')
            return redirect('compliments:create')
        
        try:
            rating = int(rating)
            if rating < 1 or rating > 5:
                raise ValueError()
        except ValueError:
            messages.error(request, 'Avaliação deve ser entre 1 e 5.')
            return redirect('compliments:create')
        
        # Criar elogio
        try:
            compliment = Compliment(
                from_user=from_user,
                rating=rating,
                comment=comment
            )
            
            if target_type == 'user':
                target_user = get_object_or_404(User, id=target_id)
                if target_user == from_user:
                    messages.error(request, 'Você não pode elogiar a si mesmo.')
                    return redirect('compliments:create')
                compliment.to_user = target_user
            elif target_type == 'sector':
                try:
                    target_sector = Sector.objects.get(id=target_id)
                    compliment.to_sector = target_sector
                except Sector.DoesNotExist:
                    messages.error(request, 'Setor não encontrado.')
                    return redirect('compliments:create')
            else:
                messages.error(request, 'Tipo de destinatário inválido.')
                return redirect('compliments:create')
            
            compliment.save()
            
            target_name = compliment.target_name
            messages.success(request, f'Elogio enviado para {target_name} com sucesso!')
            return redirect('compliments:dashboard')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar elogio: {str(e)}')
            return redirect('compliments:create')
    
    # GET - mostrar formulário
    users = User.objects.exclude(id=request.user.id).order_by('first_name', 'last_name')
    sectors = Sector.objects.all().order_by('name')
    
    context = {
        'users': users,
        'sectors': sectors,
    }
    return render(request, 'compliments/create.html', context)


@login_required
def my_compliments(request):
    """Listar todos os elogios recebidos pelo usuário"""
    user = request.user
    
    # Filtros
    rating_filter = request.GET.get('rating')
    
    # Elogios recebidos
    compliments = Compliment.objects.filter(
        to_user=user,
        is_active=True
    ).select_related('from_user')
    
    if rating_filter:
        try:
            rating_filter = int(rating_filter)
            compliments = compliments.filter(rating=rating_filter)
        except ValueError:
            pass
    
    # Paginação
    paginator = Paginator(compliments.order_by('-created_at'), 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estatísticas
    stats = {
        'total': compliments.count(),
        'avg_rating': compliments.aggregate(avg=Avg('rating'))['avg'] or 0,
        'by_rating': {i: compliments.filter(rating=i).count() for i in range(1, 6)}
    }
    
    context = {
        'compliments': page_obj,
        'stats': stats,
        'current_rating_filter': rating_filter,
    }
    return render(request, 'compliments/my_compliments.html', context)


@login_required
def compliment_detail(request, compliment_id):
    """Visualizar detalhes de um elogio"""
    compliment = get_object_or_404(
        Compliment,
        id=compliment_id,
        is_active=True
    )
    
    # Verificar permissão para visualizar
    can_view = (
        compliment.from_user == request.user or
        compliment.to_user == request.user or
        (compliment.to_sector and request.user.sector == compliment.to_sector) or
        request.user.can_manage_users()
    )
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para visualizar este elogio.')
        return redirect('compliments_dashboard')
    
    context = {
        'compliment': compliment,
    }
    return render(request, 'compliments/detail.html', context)


@login_required
def api_search_users(request):
    """API para buscar usuários"""
    query = request.GET.get('q', '').strip()
    
    if not query or len(query) < 2:
        return JsonResponse({'users': []})
    
    users = User.objects.filter(
        Q(first_name__icontains=query) |
        Q(last_name__icontains=query) |
        Q(username__icontains=query) |
        Q(email__icontains=query),
        is_active=True
    ).exclude(id=request.user.id)[:10]
    
    users_data = [
        {
            'id': user.id,
            'name': user.get_full_name() or user.username,
            'email': user.email,
            'sector': user.sector.name if user.sector else 'Sem setor'
        }
        for user in users
    ]
    
    return JsonResponse({'users': users_data})


def compliments_for_feed(limit=5):
    """Função para buscar elogios para o feed de comunicados"""
    return Compliment.objects.filter(
        is_active=True
    ).select_related(
        'from_user', 'to_user', 'to_sector'
    ).order_by('-created_at')[:limit]