from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, Http404
from django.core.paginator import Paginator
from django.db import transaction, models
from django.utils import timezone
from .models import Training, TrainingView, TrainingCategory, TrainingProgress
import os


def trainings_list_view(request):
    """Lista todos os treinamentos ativos"""
    trainings = Training.objects.filter(is_active=True).select_related('uploaded_by', 'category')
    
    # Filtro por categoria
    category_id = request.GET.get('category')
    if category_id:
        trainings = trainings.filter(category_id=category_id)
    
    # Busca
    search = request.GET.get('search', '')
    if search:
        trainings = trainings.filter(
            models.Q(title__icontains=search) |
            models.Q(description__icontains=search) |
            models.Q(category__name__icontains=search)
        )
    
    # Paginação
    paginator = Paginator(trainings, 12)  # 12 treinamentos por página
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estatísticas do usuário logado
    user_stats = {'total': 0, 'completed': 0, 'in_progress': 0}
    if request.user.is_authenticated:
        user_views = TrainingView.objects.filter(user=request.user)
        user_stats = {
            'total': user_views.count(),
            'completed': user_views.filter(completed=True).count(),
            'in_progress': user_views.filter(completed=False).count()
        }
    
    context = {
        'page_obj': page_obj,
        'search': search,
        'category_id': int(category_id) if category_id else None,
        'categories': TrainingCategory.objects.filter(is_active=True),
        'total_trainings': trainings.count(),
        'user_stats': user_stats,
    }
    return render(request, 'trainings/list.html', context)


@login_required
def training_detail_view(request, pk):
    """Visualiza detalhes de um treinamento específico"""
    training = get_object_or_404(Training, pk=pk, is_active=True)
    
    # Registrar visualização
    training_view, created = TrainingView.objects.get_or_create(
        training=training,
        user=request.user,
        defaults={'viewed_at': timezone.now()}
    )
    
    # Criar/atualizar progresso
    training_progress, progress_created = TrainingProgress.objects.get_or_create(
        training=training,
        user=request.user,
        defaults={'started_at': timezone.now()}
    )
    
    # Atualizar contador de visualizações se é uma nova visualização
    if created:
        Training.objects.filter(pk=training.pk).update(views_count=models.F('views_count') + 1)
        training.refresh_from_db()
    
    # Estatísticas do treinamento
    total_viewers = TrainingView.objects.filter(training=training).count()
    completed_viewers = TrainingView.objects.filter(training=training, completed=True).count()
    completion_rate = (completed_viewers / total_viewers * 100) if total_viewers > 0 else 0
    
    # Lista de usuários que completaram (para admin)
    completed_users = []
    if request.user.can_manage_users():
        completed_views = TrainingView.objects.filter(
            training=training, 
            completed=True
        ).select_related('user')[:10]
        completed_users = [view.user for view in completed_views]
    
    context = {
        'training': training,
        'training_view': training_view,
        'training_progress': training_progress,
        'can_manage': request.user.can_manage_users(),
        'stats': {
            'total_viewers': total_viewers,
            'completed_viewers': completed_viewers,
            'completion_rate': completion_rate,
            'completed_users': completed_users
        }
    }
    return render(request, 'trainings/detail.html', context)


@login_required
def training_upload_view(request):
    """Upload de novos treinamentos - apenas para admins"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para fazer upload de treinamentos.')
        return redirect('trainings_list')
    
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        category_id = request.POST.get('category')
        video_file = request.FILES.get('video_file')
        thumbnail = request.FILES.get('thumbnail')
        duration_seconds = request.POST.get('duration_seconds')
        
        # Validações
        if not title:
            messages.error(request, 'Título é obrigatório.')
        elif not description:
            messages.error(request, 'Descrição é obrigatória.')
        elif not video_file:
            messages.error(request, 'Arquivo de vídeo é obrigatório.')
        else:
            try:
                with transaction.atomic():
                    # Buscar categoria se especificada
                    category = None
                    if category_id:
                        try:
                            category = TrainingCategory.objects.get(id=category_id, is_active=True)
                        except TrainingCategory.DoesNotExist:
                            messages.error(request, 'Categoria inválida.')
                            return render(request, 'trainings/upload.html', {
                                'categories': TrainingCategory.objects.filter(is_active=True),
                                'max_file_size': 500 * 1024 * 1024,
                                'accepted_formats': ['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv'],
                            })
                    
                    # Criar o treinamento
                    training = Training.objects.create(
                        title=title,
                        description=description,
                        category=category,
                        video_file=video_file,
                        thumbnail=thumbnail,
                        duration_seconds=int(duration_seconds) if duration_seconds else None,
                        file_size=video_file.size,
                        uploaded_by=request.user
                    )
                    
                    messages.success(request, f'Treinamento "{title}" enviado com sucesso!')
                    return redirect('training_detail', pk=training.pk)
                    
            except Exception as e:
                messages.error(request, f'Erro ao enviar treinamento: {str(e)}')
    
    context = {
        'categories': TrainingCategory.objects.filter(is_active=True),
        'max_file_size': 500 * 1024 * 1024,  # 500MB em bytes
        'accepted_formats': ['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv'],
    }
    return render(request, 'trainings/upload.html', context)


@login_required
def training_manage_view(request):
    """Gerenciar treinamentos - apenas para admins"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para gerenciar treinamentos.')
        return redirect('trainings_list')
    
    trainings = Training.objects.all().select_related('uploaded_by').order_by('-created_at')
    
    # Filtros
    status_filter = request.GET.get('status', 'all')
    if status_filter == 'active':
        trainings = trainings.filter(is_active=True)
    elif status_filter == 'inactive':
        trainings = trainings.filter(is_active=False)
    
    search = request.GET.get('search', '')
    if search:
        trainings = trainings.filter(title__icontains=search)
    
    # Paginação
    paginator = Paginator(trainings, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    # Estatísticas
    stats = {
        'total': Training.objects.count(),
        'active': Training.objects.filter(is_active=True).count(),
        'inactive': Training.objects.filter(is_active=False).count(),
        'total_views': Training.objects.aggregate(total_views=models.Sum('views_count'))['total_views'] or 0,
    }
    
    context = {
        'page_obj': page_obj,
        'stats': stats,
        'status_filter': status_filter,
        'search': search,
    }
    return render(request, 'trainings/manage.html', context)


@login_required
def training_toggle_status_view(request, pk):
    """Ativar/desativar treinamento - apenas para admins"""
    if not request.user.can_manage_users():
        return JsonResponse({'error': 'Permissão negada'}, status=403)
    
    if request.method == 'POST':
        training = get_object_or_404(Training, pk=pk)
        training.is_active = not training.is_active
        training.save()
        
        status = 'ativado' if training.is_active else 'desativado'
        return JsonResponse({
            'success': True,
            'message': f'Treinamento {status} com sucesso!',
            'is_active': training.is_active
        })
    
    return JsonResponse({'error': 'Método não permitido'}, status=405)


@login_required
def training_delete_view(request, pk):
    """Excluir treinamento - apenas para admins"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para excluir treinamentos.')
        return redirect('trainings_list')
    
    training = get_object_or_404(Training, pk=pk)
    
    if request.method == 'POST':
        title = training.title
        
        # Remover arquivos físicos
        try:
            if training.video_file and os.path.exists(training.video_file.path):
                os.remove(training.video_file.path)
            if training.thumbnail and os.path.exists(training.thumbnail.path):
                os.remove(training.thumbnail.path)
        except Exception as e:
            messages.warning(request, f'Arquivos podem não ter sido removidos completamente: {str(e)}')
        
        # Remover do banco de dados
        training.delete()
        
        messages.success(request, f'Treinamento "{title}" excluído com sucesso!')
        return redirect('trainings_manage')
    
    context = {'training': training}
    return render(request, 'trainings/delete.html', context)


@login_required
def update_training_progress(request):
    """API para atualizar progresso de visualização do treinamento"""
    if request.method == 'POST':
        training_id = request.POST.get('training_id')
        duration_watched = request.POST.get('duration_watched', 0)
        completed = request.POST.get('completed', 'false').lower() == 'true'
        
        try:
            training = Training.objects.get(pk=training_id, is_active=True)
            
            # Atualizar TrainingView
            training_view, created = TrainingView.objects.get_or_create(
                training=training,
                user=request.user
            )
            
            # Atualizar progresso
            training_view.duration_watched = max(int(duration_watched), training_view.duration_watched)
            training_view.completed = completed
            if completed and not training_view.completion_date:
                training_view.completion_date = timezone.now()
            training_view.save()
            
            # Atualizar TrainingProgress
            training_progress, progress_created = TrainingProgress.objects.get_or_create(
                training=training,
                user=request.user
            )
            
            # Calcular porcentagem de progresso
            if training.duration_seconds and training.duration_seconds > 0:
                progress_percentage = min((int(duration_watched) / training.duration_seconds) * 100, 100)
            else:
                # Se não temos duração, usar 100% quando completed
                progress_percentage = 100 if completed else 0
            
            training_progress.progress_percentage = progress_percentage
            training_progress.is_completed = completed
            
            if completed and not training_progress.completed_at:
                training_progress.completed_at = timezone.now()
            elif not completed:
                training_progress.completed_at = None
                
            training_progress.save()
            
            return JsonResponse({
                'success': True,
                'duration_watched': training_view.duration_watched,
                'completed': training_view.completed,
                'progress_percentage': training_progress.progress_percentage
            })
            
        except Training.DoesNotExist:
            return JsonResponse({'error': 'Treinamento não encontrado'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Método não permitido'}, status=405)


@login_required 
def mark_training_completed(request, pk):
    """Marcar treinamento como concluído"""
    if request.method == 'POST':
        training = get_object_or_404(Training, pk=pk, is_active=True)
        
        # Atualizar ou criar TrainingView
        training_view, created = TrainingView.objects.get_or_create(
            training=training,
            user=request.user,
            defaults={'viewed_at': timezone.now()}
        )
        
        # Atualizar ou criar TrainingProgress
        training_progress, progress_created = TrainingProgress.objects.get_or_create(
            training=training,
            user=request.user,
            defaults={'started_at': timezone.now()}
        )
        
        # Marcar como concluído
        training_view.completed = True
        training_view.completion_date = timezone.now()
        if training.duration_seconds:
            training_view.duration_watched = training.duration_seconds
        training_view.save()
        
        training_progress.is_completed = True
        training_progress.completed_at = timezone.now()
        training_progress.progress_percentage = 100.0
        training_progress.save()
        
        if request.headers.get('Content-Type') == 'application/json':
            return JsonResponse({
                'success': True,
                'message': 'Treinamento marcado como concluído!'
            })
        
        messages.success(request, 'Treinamento marcado como concluído!')
        return redirect('training_detail', pk=pk)
    
    return JsonResponse({'error': 'Método não permitido'}, status=405)


# Views para gerenciar categorias
@login_required
def manage_training_categories_view(request):
    """Gerenciar categorias de treinamento - apenas para admins"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para gerenciar categorias.')
        return redirect('trainings_list')
    
    if request.method == 'POST':
        # Criar nova categoria
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        color = request.POST.get('color', '#3B82F6')
        
        if not name:
            messages.error(request, 'Nome da categoria é obrigatório.')
        else:
            try:
                category = TrainingCategory.objects.create(
                    name=name,
                    description=description,
                    color=color
                )
                messages.success(request, f'Categoria "{name}" criada com sucesso!')
                return redirect('manage_training_categories')
            except Exception as e:
                messages.error(request, f'Erro ao criar categoria: {str(e)}')
    
    categories = TrainingCategory.objects.all().order_by('name')
    
    # Adicionar contagem de treinamentos para cada categoria
    for category in categories:
        category.trainings_count = category.trainings.count()
    
    context = {
        'categories': categories,
    }
    return render(request, 'trainings/manage_categories.html', context)


@login_required
def edit_training_category_view(request, pk):
    """Editar categoria de treinamento"""
    if not request.user.can_manage_users():
        return JsonResponse({'error': 'Permissão negada'}, status=403)
    
    category = get_object_or_404(TrainingCategory, pk=pk)
    
    if request.method == 'POST':
        action = request.POST.get('action')
        
        if action == 'toggle_status':
            category.is_active = not category.is_active
            category.save()
            status = 'ativada' if category.is_active else 'desativada'
            return JsonResponse({
                'success': True,
                'message': f'Categoria {status} com sucesso!',
                'is_active': category.is_active
            })
        
        elif action == 'update':
            category.name = request.POST.get('name', category.name)
            category.description = request.POST.get('description', category.description)
            category.color = request.POST.get('color', category.color)
            category.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Categoria atualizada com sucesso!'
            })
        
        elif action == 'delete':
            name = category.name
            category.delete()
            return JsonResponse({
                'success': True,
                'message': f'Categoria "{name}" excluída com sucesso!'
            })
    
    return JsonResponse({'error': 'Método não permitido'}, status=405)
