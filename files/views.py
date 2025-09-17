from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, Http404
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.generic import ListView
from django.urls import reverse
from .models import SharedFile, FileCategory, FileDownload, Folder
from users.models import User, Sector
from core.models import NotificationMixin
import os
import mimetypes


def get_client_ip(request):
    """Obter IP do cliente"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


@login_required
def files_list(request):
    """Lista de arquivos"""
    # Pasta atual
    current_folder_id = request.GET.get('folder')
    current_folder = None
    if current_folder_id:
        current_folder = get_object_or_404(Folder, id=current_folder_id)
    
    # Obter pastas na pasta atual
    folders = Folder.objects.filter(parent=current_folder).order_by('name')
    
    # Obter categorias na pasta atual com contagem de arquivos
    from django.db.models import Count, Q as QueryQ
    categories = FileCategory.objects.filter(folder=current_folder).annotate(
        files_count=Count('sharedfile', filter=QueryQ(sharedfile__is_active=True))
    ).order_by('order', 'name')
    
    # Obter todos os arquivos
    files = SharedFile.objects.select_related('category', 'uploaded_by').order_by('-created_at')
    
    # Filtros
    category_filter = request.GET.get('category')
    search_filter = request.GET.get('search')
    
    if category_filter:
        files = files.filter(category_id=category_filter)
    else:
        # Se não há categoria específica, mostrar arquivos das categorias da pasta atual
        if current_folder:
            category_ids = categories.values_list('id', flat=True)
            files = files.filter(category_id__in=category_ids)
        else:
            # Se não está em nenhuma pasta, mostrar apenas arquivos de categorias sem pasta (raiz)
            root_category_ids = FileCategory.objects.filter(folder__isnull=True).values_list('id', flat=True)
            files = files.filter(category_id__in=root_category_ids)
    
    if search_filter:
        files = files.filter(
            Q(title__icontains=search_filter) | 
            Q(description__icontains=search_filter)
        )
    
    # Breadcrumb para navegação
    breadcrumb = []
    folder = current_folder
    while folder:
        breadcrumb.insert(0, folder)
        folder = folder.parent
    
    # Nome da categoria atual
    current_category_name = None
    if category_filter:
        try:
            current_category_obj = FileCategory.objects.get(id=category_filter)
            current_category_name = current_category_obj.name
        except FileCategory.DoesNotExist:
            pass
    
    return render(request, 'files/list_with_folders.html', {
        'files': files,
        'folders': folders,
        'categories': categories,
        'current_folder': current_folder,
        'current_category': category_filter,
        'current_category_name': current_category_name,
        'search': search_filter,
        'breadcrumb': breadcrumb
    })


@login_required
def file_upload_view(request):
    """View para upload de arquivos - apenas para hierarquias administrativas"""
    if not request.user.can_upload_files():
        messages.error(request, 'Você não tem permissão para fazer upload de arquivos.')
        return redirect('files:files_list')
    
    folder_id = request.GET.get('folder') or request.POST.get('folder')
    
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
            
            # Voltar para a pasta de origem
            if folder_id:
                return redirect(f"{reverse('files:files_list')}?folder={folder_id}")
            else:
                return redirect('files:files_list')
            
        except (FileCategory.DoesNotExist, Sector.DoesNotExist, User.DoesNotExist) as e:
            messages.error(request, 'Erro nos dados fornecidos. Tente novamente.')
            return render(request, 'files/upload.html', get_upload_context(folder_id))
        except Exception as e:
            messages.error(request, f'Erro ao enviar arquivo: {str(e)}')
            return render(request, 'files/upload.html', get_upload_context(folder_id))
    
    return render(request, 'files/upload.html', get_upload_context(folder_id))


def get_upload_context(folder_id=None):
    """Retorna o contexto necessário para o template de upload"""
    current_folder = None
    if folder_id:
        try:
            current_folder = Folder.objects.get(id=folder_id)
        except Folder.DoesNotExist:
            pass
    
    # Categorias da pasta atual
    categories = FileCategory.objects.filter(folder=current_folder, is_active=True).order_by('order', 'name')
    
    return {
        'categories': categories,
        'users': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'current_folder': current_folder
    }


@login_required
def file_download(request, pk):
    """Download de arquivo"""
    file = get_object_or_404(SharedFile, id=pk)
    
    # Log do download
    file_download = FileDownload.objects.create(
        file=file,
        user=request.user,
        ip_address=get_client_ip(request)
    )
    
    # Servir o arquivo
    try:
        if file.file and hasattr(file.file, 'path'):
            file_path = file.file.path
            if os.path.exists(file_path):
                # Usar mimetypes para determinar o content-type
                content_type, _ = mimetypes.guess_type(file_path)
                if not content_type:
                    content_type = "application/octet-stream"
                
                with open(file_path, 'rb') as fh:
                    response = HttpResponse(fh.read(), content_type=content_type)
                    # Usar o nome original ou nome do arquivo
                    filename = file.name if file.name else os.path.basename(file.file.name)
                    response['Content-Disposition'] = f'attachment; filename="{filename}"'
                    return response
            else:
                return HttpResponse("Arquivo não encontrado no sistema de arquivos", status=404)
        else:
            return HttpResponse("Arquivo não encontrado", status=404)
    except Exception as e:
        return HttpResponse(f"Erro ao baixar arquivo: {str(e)}", status=500)


@login_required
def create_folder(request):
    """Criar nova pasta - apenas supervisores ou superiores"""
    if not request.user.can_view_sector_tickets():
        messages.error(request, 'Você não tem permissão para criar pastas.')
        return redirect('files:files_list')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        parent_id = request.POST.get('parent')
        visibility = request.POST.get('visibility', 'ALL')
        target_sector_id = request.POST.get('target_sector')
        
        if not name:
            messages.error(request, 'O nome da pasta é obrigatório.')
            return redirect('files:files_list')
        
        parent_folder = None
        if parent_id:
            try:
                parent_folder = Folder.objects.get(id=parent_id)
            except Folder.DoesNotExist:
                messages.error(request, 'Pasta pai não encontrada.')
                return redirect('files:files_list')
        
        target_sector = None
        if visibility == 'SECTOR' and target_sector_id:
            try:
                target_sector = Sector.objects.get(id=target_sector_id)
            except Sector.DoesNotExist:
                messages.error(request, 'Setor não encontrado.')
                return redirect('files:files_list')
        
        folder = Folder.objects.create(
            name=name,
            description=description,
            parent=parent_folder,
            visibility=visibility,
            target_sector=target_sector,
            created_by=request.user
        )
        
        messages.success(request, f'Pasta "{name}" criada com sucesso!')
        
        if parent_folder:
            return redirect(f"{reverse('files:files_list')}?folder={parent_folder.id}")
        else:
            return redirect('files:files_list')
    
    # GET request
    parent_id = request.GET.get('parent')
    parent_folder = None
    if parent_id:
        try:
            parent_folder = Folder.objects.get(id=parent_id)
        except Folder.DoesNotExist:
            pass
    
    return render(request, 'files/create_folder.html', {
        'parent_folder': parent_folder
    })


@login_required
def move_file(request):
    """Mover arquivo para outra pasta via AJAX - apenas supervisores ou superiores"""
    if not request.user.can_view_sector_tickets():
        return JsonResponse({'success': False, 'message': 'Sem permissão'})
    
    if request.method == 'POST':
        import json
        
        try:
            # Tentar parsear JSON do body da requisição
            data = json.loads(request.body)
            file_id = data.get('file_id')
            folder_id = data.get('folder_id')
            category_id = data.get('category_id')
        except (json.JSONDecodeError, AttributeError):
            # Fallback para dados de formulário
            file_id = request.POST.get('file_id')
            folder_id = request.POST.get('folder_id')
            category_id = request.POST.get('category_id')
        
        if not file_id:
            return JsonResponse({'success': False, 'message': 'ID do arquivo é obrigatório'})
        
        try:
            file_obj = SharedFile.objects.get(id=file_id)
            
            # Se foi especificado folder_id, mover para uma pasta (criando/movendo para categoria da pasta)
            if folder_id:
                try:
                    target_folder = Folder.objects.get(id=folder_id)
                    
                    # Verificar se existe uma categoria "Geral" na pasta de destino
                    general_category = FileCategory.objects.filter(
                        folder=target_folder, 
                        name__iexact='geral'
                    ).first()
                    
                    # Se não existe, criar uma categoria "Geral" na pasta
                    if not general_category:
                        general_category = FileCategory.objects.create(
                            name='Geral',
                            description='Categoria geral para arquivos',
                            folder=target_folder,
                            icon='fas fa-file'
                        )
                    
                    # Mover o arquivo para a categoria da pasta de destino
                    file_obj.category = general_category
                    file_obj.save()
                    
                    return JsonResponse({
                        'success': True, 
                        'message': f'Arquivo movido para a pasta "{target_folder.name}" com sucesso!'
                    })
                    
                except Folder.DoesNotExist:
                    return JsonResponse({'success': False, 'message': 'Pasta não encontrada'})
            
            # Se foi especificado category_id, mover para categoria específica
            elif category_id:
                try:
                    category = FileCategory.objects.get(id=category_id)
                    file_obj.category = category
                    file_obj.save()
                    
                    return JsonResponse({
                        'success': True, 
                        'message': f'Arquivo movido para a categoria "{category.name}" com sucesso!'
                    })
                    
                except FileCategory.DoesNotExist:
                    return JsonResponse({'success': False, 'message': 'Categoria não encontrada'})
            
            else:
                return JsonResponse({'success': False, 'message': 'É necessário especificar pasta ou categoria de destino'})
            
        except SharedFile.DoesNotExist:
            return JsonResponse({'success': False, 'message': 'Arquivo não encontrado'})
        except Exception as e:
            return JsonResponse({'success': False, 'message': f'Erro interno: {str(e)}'})
    
    return JsonResponse({'success': False, 'message': 'Método inválido'})


@login_required
def create_category(request):
    """Criar nova categoria - apenas supervisores ou superiores"""
    if not request.user.can_view_sector_tickets():
        messages.error(request, 'Você não tem permissão para criar categorias.')
        return redirect('files:files_list')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        folder_id = request.POST.get('folder')
        icon = request.POST.get('icon', 'fas fa-tag')
        
        if not name:
            messages.error(request, 'O nome da categoria é obrigatório.')
            return redirect('files:files_list')
        
        folder = None
        if folder_id:
            try:
                folder = Folder.objects.get(id=folder_id)
            except Folder.DoesNotExist:
                messages.error(request, 'Pasta não encontrada.')
                return redirect('files:files_list')
        
        category = FileCategory.objects.create(
            name=name,
            description=description,
            folder=folder,
            icon=icon
        )
        
        messages.success(request, f'Categoria "{name}" criada com sucesso!')
        
        if folder:
            return redirect(f"{reverse('files:files_list')}?folder={folder.id}")
        else:
            return redirect('files:files_list')
    
    # GET request
    folder_id = request.GET.get('folder')
    folder = None
    if folder_id:
        try:
            folder = Folder.objects.get(id=folder_id)
        except Folder.DoesNotExist:
            pass
    
    return render(request, 'files/create_category.html', {
        'folder': folder
    })


@login_required
def get_sectors(request):
    """Retorna lista de setores e pastas disponíveis para o usuário via JSON"""
    if not request.user.can_view_sector_tickets():
        return JsonResponse({'sectors': [], 'folders': []})
    
    # Para supervisores e admins, mostrar TODOS os setores
    sectors = Sector.objects.all().order_by('name')
    
    sectors_data = [
        {
            'id': sector.id,
            'name': sector.name,
            'description': getattr(sector, 'description', '')
        }
        for sector in sectors
    ]
    
    # Buscar todas as pastas disponíveis
    folders = Folder.objects.all().order_by('name')
    folders_data = [
        {
            'id': folder.id,
            'name': folder.name,
            'description': folder.description or ''
        }
        for folder in folders
    ]
    
    # Buscar todas as categorias disponíveis
    categories = FileCategory.objects.all().order_by('name')
    categories_data = [
        {
            'id': category.id,
            'name': category.name,
            'description': category.description or '',
            'folder_name': category.folder.name if category.folder else 'Raiz'
        }
        for category in categories
    ]
    
    print(f"DEBUG: User {request.user.email} can access {len(sectors_data)} sectors, {len(folders_data)} folders, and {len(categories_data)} categories")
    
    return JsonResponse({
        'sectors': sectors_data,
        'folders': folders_data,
        'categories': categories_data
    })


@login_required
def delete_folder(request, folder_id):
    """Deletar pasta e todos os arquivos dentro dela - apenas supervisores ou superiores"""
    if not request.user.can_view_sector_tickets():
        messages.error(request, 'Você não tem permissão para deletar pastas.')
        return redirect('files:files_list')
    
    folder = get_object_or_404(Folder, id=folder_id)
    
    if request.method == 'POST':
        folder_name = folder.name
        parent_folder = folder.parent
        
        # Deletar todos os arquivos nas categorias da pasta (cascade)
        # Deletar todas as categorias da pasta (cascade)
        # Deletar todas as subpastas (cascade)
        folder.delete()
        
        messages.success(request, f'Pasta "{folder_name}" e todo seu conteúdo foram deletados.')
        
        if parent_folder:
            return redirect(f"{reverse('files:files_list')}?folder={parent_folder.id}")
        else:
            return redirect('files:files_list')
    
    # Contar arquivos e subpastas para mostrar no template
    total_files = SharedFile.objects.filter(category__folder=folder).count()
    total_subfolders = folder.subfolders.count()
    total_categories = folder.categories.count()
    
    return render(request, 'files/delete_folder.html', {
        'folder': folder,
        'total_files': total_files,
        'total_subfolders': total_subfolders,
        'total_categories': total_categories
    })


@login_required
def delete_category(request, category_id):
    """Deletar categoria e todos os arquivos dentro dela - apenas supervisores ou superiores"""
    if not request.user.can_view_sector_tickets():
        messages.error(request, 'Você não tem permissão para deletar categorias.')
        return redirect('files:files_list')
    
    category = get_object_or_404(FileCategory, id=category_id)
    
    if request.method == 'POST':
        category_name = category.name
        folder = category.folder
        
        # Deletar todos os arquivos da categoria
        category.delete()
        
        messages.success(request, f'Categoria "{category_name}" e todos os arquivos foram deletados.')
        
        if folder:
            return redirect(f"{reverse('files:files_list')}?folder={folder.id}")
        else:
            return redirect('files:files_list')
    
    # Contar arquivos para mostrar no template
    total_files = category.sharedfile_set.count()
    
    return render(request, 'files/delete_category.html', {
        'category': category,
        'total_files': total_files
    })


@login_required
def file_detail(request, pk):
    """Visualizar arquivo"""
    file = get_object_or_404(SharedFile, id=pk)
    
    # Log do download
    file_download = FileDownload.objects.create(
        file=file,
        user=request.user,
        ip_address=get_client_ip(request)
    )
    
    # Determinar o tipo de conteúdo usando mimetypes
    content_type = 'application/octet-stream'
    if file.file:
        try:
            content_type, _ = mimetypes.guess_type(file.file.name)
            if not content_type:
                content_type = 'application/octet-stream'
        except:
            content_type = 'application/octet-stream'
    
    return render(request, 'files/file_detail.html', {
        'file': file,
        'content_type': content_type
    })


@login_required
def file_delete_view(request, file_id):
    """Deletar arquivo - apenas para quem enviou, supervisores ou admins"""
    file_obj = get_object_or_404(SharedFile, id=file_id)
    
    # Verificar permissões - supervisor pode deletar qualquer arquivo
    can_delete = (
        request.user == file_obj.uploaded_by or 
        request.user.can_view_sector_tickets() or 
        request.user.can_access_admin_panel()
    )
    
    if not can_delete:
        messages.error(request, 'Você não tem permissão para deletar este arquivo.')
        return redirect('files:files_list')
    
    if request.method == 'POST':
        file_title = file_obj.title
        folder = file_obj.category.folder if file_obj.category else None
        
        file_obj.delete()  # Isso também remove o arquivo físico se configurado
        messages.success(request, f'Arquivo "{file_title}" foi deletado com sucesso.')
        
        # Redirecionar para a pasta onde o arquivo estava
        if folder:
            return redirect(f"{reverse('files:files_list')}?folder={folder.id}")
        else:
            return redirect('files:files_list')
    
    return render(request, 'files/delete_confirm.html', {
        'file': file_obj,
        'can_delete': True
    })


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
