from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, Http404
from django.core.paginator import Paginator
from django.db import transaction, models
from django.utils import timezone
from .models import Training, TrainingView
import os


def trainings_list_view(request):
    """Lista todos os treinamentos ativos"""
    trainings = Training.objects.filter(is_active=True).select_related('uploaded_by')
    
    # Busca
    search = request.GET.get('search', '')
    if search:
        trainings = trainings.filter(
            title__icontains=search
        )
    
    # Paginação
    paginator = Paginator(trainings, 12)  # 12 treinamentos por página
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj,
        'search': search,
        'total_trainings': trainings.count(),
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
    
    # Atualizar contador de visualizações se é uma nova visualização
    if created:
        Training.objects.filter(pk=training.pk).update(views_count=models.F('views_count') + 1)
        training.refresh_from_db()
    
    # Buscar outras visualizações do usuário
    user_progress = TrainingView.objects.filter(user=request.user).select_related('training')
    
    context = {
        'training': training,
        'training_view': training_view,
        'user_progress': user_progress,
        'can_manage': request.user.can_manage_users(),
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
                    # Criar o treinamento
                    training = Training.objects.create(
                        title=title,
                        description=description,
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
            training_view, created = TrainingView.objects.get_or_create(
                training=training,
                user=request.user
            )
            
            # Atualizar progresso
            training_view.duration_watched = max(int(duration_watched), training_view.duration_watched)
            training_view.completed = completed
            training_view.save()
            
            return JsonResponse({
                'success': True,
                'duration_watched': training_view.duration_watched,
                'completed': training_view.completed
            })
            
        except Training.DoesNotExist:
            return JsonResponse({'error': 'Treinamento não encontrado'}, status=404)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Método não permitido'}, status=405)
