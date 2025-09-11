from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, Http404
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.generic import ListView
from .models import SharedFile, FileCategory, FileDownload
from users.models import User, Sector
from core.models import NotificationMixin
import os


@login_required
def files_list_view(request):
    """Lista os arquivos que o usuário pode visualizar"""
    
    # Filtrar arquivos baseado nas permissões do usuário
    files_query = SharedFile.objects.filter(is_active=True)
    
    # Aplicar filtros de visibilidade
    user_files = []
    for file in files_query:
        if file.can_be_viewed_by(request.user):
            user_files.append(file)
    
    # Filtros adicionais
    category_filter = request.GET.get('category')
    search = request.GET.get('search')
    
    if category_filter:
        user_files = [f for f in user_files if str(f.category.id) == category_filter]
    
    if search:
        user_files = [f for f in user_files if search.lower() in f.title.lower() or search.lower() in f.description.lower()]
    
    # Paginação
    paginator = Paginator(user_files, 12)
    page_number = request.GET.get('page')
    files = paginator.get_page(page_number)
    
    categories = FileCategory.objects.filter(is_active=True)
    
    context = {
        'files': files,
        'categories': categories,
        'current_category': category_filter,
        'search_query': search,
    }
    
    return render(request, 'files/list.html', context)


@login_required
def file_upload_view(request):
    """View para upload de arquivos - apenas para hierarquias administrativas"""
    if not request.user.can_upload_files():
        messages.error(request, 'Você não tem permissão para fazer upload de arquivos.')
        return redirect('files_list')
    
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description', '')
        category_id = request.POST.get('category')
        visibility = request.POST.get('visibility')
        target_sector_id = request.POST.get('target_sector')
        target_user_id = request.POST.get('target_user')
        uploaded_file = request.FILES.get('file')
        
        if not all([title, category_id, visibility, uploaded_file]):
            messages.error(request, 'Todos os campos obrigatórios devem ser preenchidos.')
            return render(request, 'files/upload.html', get_upload_context())
        
        try:
            category = FileCategory.objects.get(id=category_id, is_active=True)
            
            # Validações de visibilidade
            target_sector = None
            target_user = None
            
            if visibility == 'SECTOR':
                if not target_sector_id:
                    messages.error(request, 'Selecione um setor para visibilidade por setor.')
                    return render(request, 'files/upload.html', get_upload_context())
                target_sector = Sector.objects.get(id=target_sector_id)
            
            elif visibility == 'USER':
                if not target_user_id:
                    messages.error(request, 'Selecione um usuário para visibilidade específica.')
                    return render(request, 'files/upload.html', get_upload_context())
                target_user = User.objects.get(id=target_user_id)
            
            # Criar arquivo
            shared_file = SharedFile.objects.create(
                title=title,
                description=description,
                file=uploaded_file,
                category=category,
                visibility=visibility,
                target_sector=target_sector,
                target_user=target_user,
                uploaded_by=request.user,
                file_size=uploaded_file.size
            )
            
            # Criar notificações
            create_file_notifications(shared_file, request.user)
            
            messages.success(request, f'Arquivo "{title}" enviado com sucesso!')
            return redirect('files_list')
            
        except (FileCategory.DoesNotExist, Sector.DoesNotExist, User.DoesNotExist) as e:
            messages.error(request, 'Erro nos dados fornecidos. Tente novamente.')
            return render(request, 'files/upload.html', get_upload_context())
        except Exception as e:
            messages.error(request, f'Erro ao enviar arquivo: {str(e)}')
            return render(request, 'files/upload.html', get_upload_context())
    
    return render(request, 'files/upload.html', get_upload_context())


def get_upload_context():
    """Retorna o contexto necessário para o template de upload"""
    return {
        'categories': FileCategory.objects.filter(is_active=True),
        'sectors': Sector.objects.all(),
        'users': User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    }


@login_required
def file_download_view(request, file_id):
    """Download de arquivo com log"""
    file_obj = get_object_or_404(SharedFile, id=file_id, is_active=True)
    
    # Verificar se o usuário pode ver o arquivo
    if not file_obj.can_be_viewed_by(request.user):
        raise Http404("Arquivo não encontrado")
    
    # Criar log de download
    FileDownload.objects.create(
        file=file_obj,
        user=request.user,
        ip_address=request.META.get('REMOTE_ADDR')
    )
    
    # Incrementar contador
    file_obj.increment_downloads()
    
    # Servir arquivo
    file_path = file_obj.file.path
    if os.path.exists(file_path):
        with open(file_path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type="application/octet-stream")
            response['Content-Disposition'] = f'attachment; filename="{file_obj.file.name}"'
            return response
    
    raise Http404("Arquivo não encontrado no sistema")


@login_required
def file_detail_view(request, file_id):
    """Detalhes do arquivo"""
    file_obj = get_object_or_404(SharedFile, id=file_id, is_active=True)
    
    # Verificar se o usuário pode ver o arquivo
    if not file_obj.can_be_viewed_by(request.user):
        raise Http404("Arquivo não encontrado")
    
    # Logs de download (apenas para quem enviou ou admins)
    download_logs = []
    if request.user == file_obj.uploaded_by or request.user.can_access_admin_panel():
        download_logs = file_obj.download_logs.all()[:10]
    
    context = {
        'file': file_obj,
        'download_logs': download_logs,
        'can_manage': request.user == file_obj.uploaded_by or request.user.can_access_admin_panel()
    }
    
    return render(request, 'files/detail.html', context)


@login_required
def file_delete_view(request, file_id):
    """Deletar arquivo - apenas para quem enviou ou admins"""
    file_obj = get_object_or_404(SharedFile, id=file_id)
    
    # Verificar permissões
    if request.user != file_obj.uploaded_by and not request.user.can_access_admin_panel():
        messages.error(request, 'Você não tem permissão para deletar este arquivo.')
        return redirect('files_list')
    
    if request.method == 'POST':
        file_title = file_obj.title
        file_obj.delete()  # Isso também remove o arquivo físico se configurado
        messages.success(request, f'Arquivo "{file_title}" foi deletado.')
        return redirect('files_list')
    
    return render(request, 'files/delete_confirm.html', {'file': file_obj})


def create_file_notifications(shared_file, uploader):
    """Cria notificações para usuários baseado na visibilidade do arquivo"""
    
    users_to_notify = []
    
    if shared_file.visibility == 'ALL':
        # Notificar todos os usuários ativos
        users_to_notify = User.objects.filter(is_active=True).exclude(id=uploader.id)
        
    elif shared_file.visibility == 'SECTOR' and shared_file.target_sector:
        # Notificar usuários do setor
        users_to_notify = shared_file.target_sector.users.filter(is_active=True).exclude(id=uploader.id)
        
    elif shared_file.visibility == 'USER' and shared_file.target_user:
        # Notificar usuário específico
        if shared_file.target_user != uploader:
            users_to_notify = [shared_file.target_user]
    
    if users_to_notify:
        title = f"Novo arquivo disponível: {shared_file.title}"
        message = f"Um novo arquivo foi compartilhado por {uploader.full_name}."
        
        NotificationMixin.create_notifications_for_users(
            users_to_notify,
            title,
            message,
            'FILE',
            related_object_id=shared_file.id,
            related_url=f'/files/{shared_file.id}/'
        )
