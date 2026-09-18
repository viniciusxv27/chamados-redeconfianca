from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, Http404
from django.core.paginator import Paginator
from django.db import transaction, models
from django.utils import timezone
from django.urls import reverse
from .models import Training, TrainingView, TrainingCategory, TrainingProgress
import logging
import os

logger = logging.getLogger(__name__)


def pode_gerenciar_treinamentos(user):
    """Publica, edita e desativa treinamentos.

    A mesma turma de sempre (`can_manage_users`) mais quem foi liberado na
    tela do usuário. Antes esta pergunta era feita com `can_manage_users`
    direto, e liberar "treinamentos" a alguém abriria junto a gestão de
    usuários — ou o contrário.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    from users.module_access import user_has_module
    return user.can_manage_users() or user_has_module(user, 'treinamentos.gestao')


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
    if pode_gerenciar_treinamentos(request.user):
        completed_views = TrainingView.objects.filter(
            training=training, 
            completed=True
        ).select_related('user')[:10]
        completed_users = [view.user for view in completed_views]
    
    context = {
        'training': training,
        'training_view': training_view,
        'training_progress': training_progress,
        'can_manage': pode_gerenciar_treinamentos(request.user),
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
    """Upload de novos treinamentos - apenas para admins

    A tela envia por XMLHttpRequest (com o progresso de verdade) e recebe JSON:
    {'ok': True, 'redirect': ...} ou {'ok': False, 'erro': ...}. Sem JavaScript,
    o formulário comum continua funcionando, com as mensagens de sempre.
    """
    if not pode_gerenciar_treinamentos(request.user):
        messages.error(request, 'Você não tem permissão para fazer upload de treinamentos.')
        return redirect('trainings_list')

    por_ajax = request.headers.get('x-requested-with') == 'XMLHttpRequest'
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        category_id = request.POST.get('category')
        video_file = request.FILES.get('video_file')
        thumbnail = request.FILES.get('thumbnail')
        duracao = (request.POST.get('duration_seconds') or '').strip()

        erro = _erro_do_envio(title, description, video_file, thumbnail)
        category = None
        if not erro and category_id:
            category = TrainingCategory.objects.filter(id=category_id, is_active=True).first() if category_id.isdigit() else None
            if category is None:
                erro = 'Categoria inválida.'
        if erro:
            if por_ajax:
                return JsonResponse({'ok': False, 'erro': erro}, status=400)
            messages.error(request, erro)
        else:
            training = None
            try:
                with transaction.atomic():
                    # Primeiro o registro, depois os arquivos: assim o vídeo sobe uma vez
                    # só, direto na pasta do id (ver training_video_path).
                    training = Training.objects.create(
                        title=title,
                        description=description,
                        category=category,
                        duration_seconds=int(duracao) if duracao.isdigit() else None,
                        file_size=video_file.size,
                        uploaded_by=request.user
                    )
                    training.video_file = video_file
                    training.thumbnail = thumbnail
                    training.save(update_fields=['video_file', 'thumbnail', 'updated_at'])
            except Exception:
                logger.exception('Treinamento "%s" não foi salvo (vídeo no armazenamento ou banco)', title)
                _apagar_arquivos_enviados(training)
                erro = ('O vídeo chegou, mas não foi guardado: o armazenamento não respondeu. '
                        'Nada foi gravado — tente de novo em instantes.')
                if por_ajax:
                    return JsonResponse({'ok': False, 'erro': erro}, status=500)
                messages.error(request, erro)
            else:
                messages.success(request, f'Treinamento "{title}" enviado com sucesso!')
                destino = reverse('training_detail', args=[training.pk])
                if por_ajax:
                    return JsonResponse({'ok': True, 'redirect': destino})
                return redirect(destino)

    context = {
        'categories': TrainingCategory.objects.filter(is_active=True),
        'max_file_size': VIDEO_MAX_BYTES,
        'accepted_formats': VIDEO_FORMATOS,
    }
    return render(request, 'trainings/upload.html', context)


VIDEO_MAX_BYTES = 500 * 1024 * 1024          # 500 MB por vídeo
VIDEO_FORMATOS = ['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv']
MINIATURA_FORMATOS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp', 'GIF': 'gif'}


def _erro_do_envio(titulo, descricao, video, miniatura):
    """Mensagem do que impede o envio, ou '' quando dá para guardar."""
    if not titulo:
        return 'Título é obrigatório.'
    if not descricao:
        return 'Descrição é obrigatória.'
    if not video:
        return 'Arquivo de vídeo é obrigatório.'
    extensao = os.path.splitext(video.name or '')[1].lower().lstrip('.')
    if extensao not in VIDEO_FORMATOS:
        return f'Formato {extensao.upper() or "desconhecido"} não aceito. Envie {", ".join(VIDEO_FORMATOS).upper()}.'
    if not video.size:
        return 'O arquivo de vídeo chegou vazio.'
    if video.size > VIDEO_MAX_BYTES:
        return f'O vídeo tem {video.size / (1024 * 1024):.0f} MB e o limite é 500 MB.'
    if miniatura:
        # O tipo que o navegador declara é só uma declaração: a miniatura precisa abrir como imagem.
        from PIL import Image
        try:
            miniatura.seek(0)
            with Image.open(miniatura) as imagem:
                formato = imagem.format
                imagem.verify()
        except Exception:
            formato = None
        finally:
            miniatura.seek(0)
        if formato not in MINIATURA_FORMATOS:
            return 'A miniatura precisa ser uma imagem JPG, PNG, WEBP ou GIF.'
    return ''


def _apagar_arquivos_enviados(training):
    """Se o envio falhou no meio, tira do armazenamento o que chegou a subir (o registro já foi desfeito)."""
    if training is None:
        return
    for campo in (training.video_file, training.thumbnail):
        try:
            if campo and campo.name and getattr(campo, '_committed', False):
                campo.storage.delete(campo.name)
        except Exception as exc:                     # noqa: BLE001 — o erro original é o que importa
            logger.warning('Arquivo do treinamento que falhou não saiu do armazenamento: %s', exc)


@login_required
def training_manage_view(request):
    """Gerenciar treinamentos - apenas para admins"""
    if not pode_gerenciar_treinamentos(request.user):
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
    if not pode_gerenciar_treinamentos(request.user):
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
    if not pode_gerenciar_treinamentos(request.user):
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
    if not pode_gerenciar_treinamentos(request.user):
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
    if not pode_gerenciar_treinamentos(request.user):
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
