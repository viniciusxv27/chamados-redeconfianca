from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, Http404
from django.core.paginator import Paginator
from django.db.models import Q
from django.utils.decorators import method_decorator
from django.views.generic import ListView
from django.urls import reverse
from .models import SharedFile, FileCategory, FileDownload, Folder, MovimentacaoArquivo
from users.models import User, Sector
from core.models import NotificationMixin
from . import acesso
from .acesso import Acao, pode_gerir, e_superadmin
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
    visao = acesso.Visao(request.user)
    current_folder_id = request.GET.get('folder')
    current_folder = None
    if current_folder_id:
        current_folder = get_object_or_404(acesso.pastas(), id=current_folder_id)
        if not visao.pasta(current_folder):
            messages.error(request, 'Você não tem acesso a esta pasta.')
            return redirect('files:files_list')

    # Obter pastas na pasta atual (só as que a pessoa enxerga)
    folders = [f for f in acesso.pastas().filter(parent=current_folder).order_by('name') if visao.pasta(f)]

    # Obter categorias na pasta atual com contagem de arquivos
    from django.db.models import Count, Q as QueryQ
    categories = acesso.categorias().filter(folder=current_folder).annotate(
        files_count=Count('sharedfile', filter=QueryQ(sharedfile__is_active=True,
                                                       sharedfile__excluido_em__isnull=True))
    ).order_by('order', 'name')

    # Obter todos os arquivos
    files = acesso.arquivos().select_related('category', 'uploaded_by').order_by('-created_at')
    
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
            root_category_ids = acesso.categorias().filter(folder__isnull=True).values_list('id', flat=True)
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
    
    files = [f for f in files if visao.arquivo(f)]

    return render(request, 'files/list_with_folders.html', {
        'pode_gerir': pode_gerir(request.user),
        'e_superadmin': e_superadmin(request.user),
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
        target_group_id = request.POST.get('target_group')
        target_user_id = request.POST.get('target_user')
        uploaded_files = request.FILES.getlist('files')  # Mudança: getlist para múltiplos arquivos
        
        if not all([title, category_id, visibility]):
            messages.error(request, 'Título, categoria e visibilidade são obrigatórios.')
            return render(request, 'files/upload.html', get_upload_context(folder_id))
        
        if not uploaded_files:
            messages.error(request, 'Selecione pelo menos um arquivo para enviar.')
            return render(request, 'files/upload.html', get_upload_context(folder_id))
        
        try:
            category = acesso.categorias().get(id=category_id, is_active=True)
            
            # Validações de visibilidade
            target_sector = None
            target_group = None
            target_user = None
            
            if visibility == 'SECTOR':
                if not target_sector_id:
                    messages.error(request, 'Selecione um setor para visibilidade por setor.')
                    return render(request, 'files/upload.html', get_upload_context(folder_id))
                target_sector = Sector.objects.get(id=target_sector_id)
            
            elif visibility == 'GROUP':
                if not target_group_id:
                    messages.error(request, 'Selecione um grupo para visibilidade por grupo.')
                    return render(request, 'files/upload.html', get_upload_context(folder_id))
                from communications.models import CommunicationGroup
                target_group = CommunicationGroup.objects.get(id=target_group_id)
            
            elif visibility == 'USER':
                if not target_user_id:
                    messages.error(request, 'Selecione um usuário para visibilidade específica.')
                    return render(request, 'files/upload.html', get_upload_context(folder_id))
                target_user = User.objects.get(id=target_user_id)
            
            # Criar arquivos (um para cada arquivo enviado)
            created_files = []
            for index, uploaded_file in enumerate(uploaded_files):
                # Se houver múltiplos arquivos, adicionar número ao título
                file_title = title
                if len(uploaded_files) > 1:
                    file_title = f"{title} ({index + 1})"
                
                shared_file = SharedFile.objects.create(
                    title=file_title,
                    description=description,
                    file=uploaded_file,
                    category=category,
                    visibility=visibility,
                    target_sector=target_sector,
                    target_group=target_group,
                    target_user=target_user,
                    uploaded_by=request.user,
                    file_size=uploaded_file.size
                )
                
                created_files.append(shared_file)
                acesso.registrar(Acao.ENVIO, shared_file, request=request,
                                 detalhe=f'Categoria "{category.name}" · {shared_file.get_visibility_display()}')
                
                # Criar notificações
                create_file_notifications(shared_file, request.user)
            
            # Mensagem de sucesso
            if len(created_files) == 1:
                messages.success(request, f'Arquivo "{title}" enviado com sucesso!')
            else:
                messages.success(request, f'{len(created_files)} arquivos enviados com sucesso!')
            
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
    from communications.models import CommunicationGroup
    
    current_folder = None
    if folder_id:
        try:
            current_folder = Folder.objects.get(id=folder_id)
        except Folder.DoesNotExist:
            pass
    
    # Categorias da pasta atual
    categories = acesso.categorias().filter(folder=current_folder, is_active=True).order_by('order', 'name')
    
    # Grupos disponíveis
    groups = CommunicationGroup.objects.filter(is_active=True).order_by('name')
    
    return {
        'categories': categories,
        'users': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'groups': groups,
        'current_folder': current_folder
    }


@login_required
def file_download(request, pk):
    """Download de arquivo - Redireciona para S3"""
    file = get_object_or_404(acesso.arquivos(), id=pk)
    if not acesso.Visao(request.user).arquivo(file):
        acesso.registrar(Acao.NEGADO, file, request=request, detalhe='Download sem acesso')
        messages.error(request, 'Você não tem acesso a este arquivo.')
        return redirect('files:files_list')
    acesso.registrar(Acao.DOWNLOAD, file, request=request, detalhe='Download')
    
    # Log do download
    file_download = FileDownload.objects.create(
        file=file,
        user=request.user,
        ip_address=get_client_ip(request)
    )
    
    # Incrementar contador de downloads
    file.increment_downloads()
    
    # Redirecionar para a URL S3
    try:
        if file.file:
            # Construir URL completa do S3
            s3_base_url = "https://s3.dev.redeconfianca.com.br/chamados/media/"
            file_url = s3_base_url + str(file.file)
            
            # Redirecionar diretamente para o S3
            return redirect(file_url)
        else:
            return HttpResponse("Arquivo não encontrado", status=404)
    except Exception as e:
        return HttpResponse(f"Erro ao baixar arquivo: {str(e)}", status=500)


@login_required
def create_folder(request):
    """Criar nova pasta - quem gerencia os arquivos"""
    if not pode_gerir(request.user):
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
                parent_folder = acesso.pastas().get(id=parent_id)
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
        acesso.registrar(Acao.CRIAR_PASTA, folder, request=request,
                         detalhe=f'Visibilidade: {folder.get_visibility_display()}')

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
    """Mover arquivo para outra pasta via AJAX - quem gerencia os arquivos"""
    if not pode_gerir(request.user):
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
            file_obj = acesso.arquivos().get(id=file_id)
            origem = file_obj.category.name
            
            # Se foi especificado folder_id, mover para uma pasta (criando/movendo para categoria da pasta)
            if folder_id:
                try:
                    target_folder = acesso.pastas().get(id=folder_id)

                    # Verificar se existe uma categoria "Geral" na pasta de destino
                    general_category = acesso.categorias().filter(
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
                    acesso.registrar(Acao.MOVER, file_obj, request=request,
                                     detalhe=f'De "{origem}" para a pasta "{target_folder.get_full_path()}"')
                    
                    return JsonResponse({
                        'success': True, 
                        'message': f'Arquivo movido para a pasta "{target_folder.name}" com sucesso!'
                    })
                    
                except Folder.DoesNotExist:
                    return JsonResponse({'success': False, 'message': 'Pasta não encontrada'})
            
            # Se foi especificado category_id, mover para categoria específica
            elif category_id:
                try:
                    category = acesso.categorias().get(id=category_id)
                    file_obj.category = category
                    file_obj.save()
                    acesso.registrar(Acao.MOVER, file_obj, request=request,
                                     detalhe=f'De "{origem}" para a categoria "{category.name}"')
                    
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
    """Criar nova categoria - quem gerencia os arquivos"""
    if not pode_gerir(request.user):
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
                folder = acesso.pastas().get(id=folder_id)
            except Folder.DoesNotExist:
                messages.error(request, 'Pasta não encontrada.')
                return redirect('files:files_list')
        
        category = FileCategory.objects.create(
            name=name,
            description=description,
            folder=folder,
            icon=icon
        )
        acesso.registrar(Acao.CRIAR_CATEGORIA, category, request=request,
                         detalhe=f'Na pasta "{folder.get_full_path()}"' if folder else 'Na raiz')

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
    """Retorna lista de setores, grupos e pastas disponíveis para o usuário via JSON"""
    if not pode_gerir(request.user):
        return JsonResponse({'sectors': [], 'groups': [], 'folders': []})
    
    from communications.models import CommunicationGroup
    
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
    
    # Buscar todos os grupos disponíveis
    groups = CommunicationGroup.objects.filter(is_active=True).order_by('name')
    groups_data = [
        {
            'id': group.id,
            'name': group.name,
            'description': group.description or '',
            'members_count': group.members.count()
        }
        for group in groups
    ]
    
    # Buscar todas as pastas disponíveis
    folders = acesso.pastas().order_by('name')
    folders_data = [
        {
            'id': folder.id,
            'name': folder.name,
            'description': folder.description or ''
        }
        for folder in folders
    ]
    
    # Buscar todas as categorias disponíveis
    categories = acesso.categorias().select_related('folder').order_by('name')
    categories_data = [
        {
            'id': category.id,
            'name': category.name,
            'description': category.description or '',
            'folder_name': category.folder.name if category.folder else 'Raiz'
        }
        for category in categories
    ]
    
    return JsonResponse({
        'sectors': sectors_data,
        'groups': groups_data,
        'folders': folders_data,
        'categories': categories_data
    })


@login_required
def delete_folder(request, folder_id):
    """Manda a pasta (e o que está dentro) para a lixeira — quem gerencia os arquivos"""
    folder = get_object_or_404(acesso.pastas(), id=folder_id)
    if not pode_gerir(request.user):
        acesso.registrar(Acao.NEGADO, folder, request=request, detalhe='Excluir pasta sem permissão')
        messages.error(request, 'Você não tem permissão para excluir pastas.')
        return redirect('files:files_list')

    if request.method == 'POST':
        folder_name = folder.name
        parent_folder = folder.parent

        # Nada é apagado: pasta, subpastas, categorias e arquivos vão juntos para a lixeira.
        acesso.mandar_para_lixeira(folder, request.user, request)

        messages.success(request, f'Pasta "{folder_name}" e todo o seu conteúdo foram para a lixeira.')
        
        if parent_folder:
            return redirect(f"{reverse('files:files_list')}?folder={parent_folder.id}")
        else:
            return redirect('files:files_list')
    
    # Contar arquivos e subpastas para mostrar no template
    total_files = acesso.arquivos().filter(category__folder=folder).count()
    total_subfolders = acesso.pastas().filter(parent=folder).count()
    total_categories = acesso.categorias().filter(folder=folder).count()
    
    return render(request, 'files/delete_folder.html', {
        'folder': folder,
        'total_files': total_files,
        'total_subfolders': total_subfolders,
        'total_categories': total_categories
    })


@login_required
def delete_category(request, category_id):
    """Manda a categoria (e os arquivos dela) para a lixeira — quem gerencia os arquivos"""
    category = get_object_or_404(acesso.categorias(), id=category_id)
    if not pode_gerir(request.user):
        acesso.registrar(Acao.NEGADO, category, request=request, detalhe='Excluir categoria sem permissão')
        messages.error(request, 'Você não tem permissão para excluir categorias.')
        return redirect('files:files_list')

    if request.method == 'POST':
        category_name = category.name
        folder = category.folder

        acesso.mandar_para_lixeira(category, request.user, request)

        messages.success(request, f'Categoria "{category_name}" e os arquivos dela foram para a lixeira.')
        
        if folder:
            return redirect(f"{reverse('files:files_list')}?folder={folder.id}")
        else:
            return redirect('files:files_list')
    
    # Contar arquivos para mostrar no template
    total_files = acesso.arquivos().filter(category=category).count()
    
    return render(request, 'files/delete_category.html', {
        'category': category,
        'total_files': total_files
    })


@login_required
def file_detail(request, pk):
    """Visualizar arquivo"""
    file = get_object_or_404(acesso.arquivos(), id=pk)
    if not acesso.Visao(request.user).arquivo(file):
        acesso.registrar(Acao.NEGADO, file, request=request, detalhe='Abrir sem acesso')
        messages.error(request, 'Você não tem acesso a este arquivo.')
        return redirect('files:files_list')
    acesso.registrar(Acao.DOWNLOAD, file, request=request, detalhe='Visualização')
    
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
        'content_type': content_type,
        'pode_excluir': acesso.pode_excluir_arquivo(request.user, file),
        'e_superadmin': e_superadmin(request.user),
    })


@login_required
def file_delete_view(request, file_id):
    """Manda o arquivo para a lixeira - quem enviou ou quem gerencia os arquivos"""
    file_obj = get_object_or_404(acesso.arquivos(), id=file_id)

    if not acesso.pode_excluir_arquivo(request.user, file_obj):
        acesso.registrar(Acao.NEGADO, file_obj, request=request, detalhe='Excluir arquivo sem permissão')
        messages.error(request, 'Você não tem permissão para excluir este arquivo.')
        return redirect('files:files_list')
    
    if request.method == 'POST':
        file_title = file_obj.title
        folder = file_obj.category.folder if file_obj.category else None
        
        # Nunca apaga: some das telas e fica na lixeira (o SUPERADMIN recupera).
        acesso.mandar_para_lixeira(file_obj, request.user, request)
        messages.success(request, f'Arquivo "{file_title}" foi para a lixeira.')
        
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
    
    elif shared_file.visibility == 'GROUP' and shared_file.target_group:
        # Notificar membros do grupo
        users_to_notify = shared_file.target_group.members.filter(is_active=True).exclude(id=uploader.id)
        
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


# ---------------------------------------------------------------------------
# SUPERADMIN: lixeira, movimentações, acesso por pessoa e visão por usuário
# ---------------------------------------------------------------------------
def so_superadmin(view):
    from functools import wraps

    @wraps(view)
    def _view(request, *args, **kwargs):
        if not e_superadmin(request.user):
            messages.error(request, 'Só o SUPERADMIN acessa esta área dos arquivos.')
            return redirect('files:files_list')
        return view(request, *args, **kwargs)
    return login_required(_view)


ITENS = {'arquivo': SharedFile, 'pasta': Folder, 'categoria': FileCategory}


def _voltar(request, padrao):
    destino = request.POST.get('voltar') or ''
    return redirect(destino if destino.startswith('/files/') else padrao)


@so_superadmin
def lixeira(request):
    """Tudo o que foi excluído, por lote: o que saiu junto volta junto."""
    pastas = list(Folder.objects.filter(excluido_em__isnull=False).select_related('excluido_por', 'parent'))
    categorias = list(FileCategory.objects.filter(excluido_em__isnull=False)
                      .select_related('excluido_por', 'folder'))
    arquivos = list(SharedFile.objects.filter(excluido_em__isnull=False)
                    .select_related('excluido_por', 'category', 'uploaded_by'))
    lotes = {}
    for tipo, itens in (('pasta', pastas), ('categoria', categorias), ('arquivo', arquivos)):
        for item in itens:
            chave = item.lote_exclusao or f'{tipo}-{item.pk}'
            lote = lotes.setdefault(chave, {'quando': item.excluido_em, 'por': item.excluido_por,
                                            'pastas': [], 'categorias': [], 'arquivos': []})
            lote[tipo + 's'].append(item)
            lote['quando'] = min(lote['quando'], item.excluido_em)
    for lote in lotes.values():
        # O item "de cima" do lote: o que a pessoa clicou para excluir.
        if lote['pastas']:
            ids = {p.pk for p in lote['pastas']}
            lote['principal'] = next((p for p in lote['pastas'] if p.parent_id not in ids), lote['pastas'][0])
            lote['tipo'] = 'pasta'
        elif lote['categorias']:
            lote['principal'], lote['tipo'] = lote['categorias'][0], 'categoria'
        else:
            lote['principal'], lote['tipo'] = lote['arquivos'][0], 'arquivo'
    ordenados = sorted(lotes.values(), key=lambda l: l['quando'], reverse=True)
    return render(request, 'files/lixeira.html', {'lotes': ordenados})


@so_superadmin
def restaurar(request, tipo, item_id):
    if request.method != 'POST' or tipo not in ITENS:
        return redirect('files:lixeira')
    item = get_object_or_404(ITENS[tipo].objects.filter(excluido_em__isnull=False), pk=item_id)
    voltaram = acesso.restaurar(item, request.user, request)
    total = sum(voltaram.values())
    messages.success(request, f'Recuperado: {total} item(ns) voltaram para o lugar de onde saíram.')
    return redirect('files:lixeira')


@so_superadmin
def movimentacoes(request):
    """O log de /files/: quem fez o quê, em qual item, quando."""
    from django.utils.dateparse import parse_date

    qs = MovimentacaoArquivo.objects.select_related('usuario', 'pessoa')
    acao = request.GET.get('acao') or ''
    if acao in MovimentacaoArquivo.Acao.values:
        qs = qs.filter(acao=acao)
    usuario_id = request.GET.get('usuario') or ''
    if usuario_id.isdigit():
        qs = qs.filter(Q(usuario_id=usuario_id) | Q(pessoa_id=usuario_id))
    busca = (request.GET.get('q') or '').strip()
    if busca:
        qs = qs.filter(Q(item_nome__icontains=busca) | Q(detalhe__icontains=busca))
    de, ate = parse_date(request.GET.get('de') or ''), parse_date(request.GET.get('ate') or '')
    if de:
        qs = qs.filter(quando__date__gte=de)
    if ate:
        qs = qs.filter(quando__date__lte=ate)
    tipo_item, item_id = (request.GET.get('tipo') or '').upper(), request.GET.get('item') or ''
    if tipo_item in MovimentacaoArquivo.Tipo.values and item_id.isdigit():
        qs = qs.filter(tipo=tipo_item, item_id=item_id)
    pagina = Paginator(qs, 50).get_page(request.GET.get('pagina'))
    parametros = request.GET.copy()
    parametros.pop('pagina', None)
    return render(request, 'files/movimentacoes.html', {
        'pagina': pagina, 'acoes': MovimentacaoArquivo.Acao.choices, 'acao': acao, 'usuario_id': usuario_id,
        'busca': busca, 'de': request.GET.get('de') or '', 'ate': request.GET.get('ate') or '',
        'usuarios': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
        'query': parametros.urlencode(),
    })


@so_superadmin
def acesso_item(request, tipo, item_id):
    """Quem tem acesso por pessoa a uma pasta ou arquivo — liberar e retirar."""
    if tipo not in ('arquivo', 'pasta'):
        raise Http404
    item = get_object_or_404(ITENS[tipo].objects.filter(excluido_em__isnull=True), pk=item_id)
    padrao = reverse('files:acesso_item', args=[tipo, item.pk])
    if request.method == 'POST':
        if request.POST.get('acao') == 'retirar':
            pessoa = get_object_or_404(User, pk=request.POST.get('pessoa') or 0)
            if acesso.retirar(item, pessoa, request.user, request):
                messages.success(request, f'Acesso de {pessoa.full_name} retirado.')
        else:
            ids = [i for i in request.POST.getlist('pessoas') if str(i).isdigit()]
            pessoas = list(User.objects.filter(pk__in=ids, is_active=True))
            n = acesso.liberar(item, pessoas, request.user, request)
            if n:
                messages.success(request, f'Acesso liberado para {n} pessoa(s).')
            else:
                messages.info(request, 'Escolha pelo menos uma pessoa que ainda não tenha acesso.')
        return _voltar(request, padrao)
    com_acesso = list(item.allowed_users.order_by('first_name', 'last_name'))
    tipo_log = MovimentacaoArquivo.Tipo.ARQUIVO if tipo == 'arquivo' else MovimentacaoArquivo.Tipo.PASTA
    return render(request, 'files/acesso_item.html', {
        'item': item, 'tipo': tipo, 'nome': item.title if tipo == 'arquivo' else item.get_full_path(),
        'com_acesso': com_acesso,
        'usuarios': (User.objects.filter(is_active=True).exclude(pk__in=[p.pk for p in com_acesso])
                     .order_by('first_name', 'last_name')),
        'historico': (MovimentacaoArquivo.objects.filter(tipo=tipo_log, item_id=item.pk)
                      .select_related('usuario', 'pessoa')[:30]),
    })


@so_superadmin
def por_usuario(request):
    """Escolhe uma pessoa e mostra o /files/ dela: o que ela vê, o que enviou e o que foi liberado.

    Daqui o SUPERADMIN libera e retira acesso por pessoa.
    """
    pessoa = None
    pessoa_id = request.GET.get('usuario') or request.POST.get('usuario') or ''
    if pessoa_id.isdigit():
        pessoa = User.objects.filter(pk=pessoa_id).first()
    contexto = {'usuarios': User.objects.filter(is_active=True).order_by('first_name', 'last_name'),
                'pessoa': pessoa}
    if pessoa is None:
        return render(request, 'files/por_usuario.html', contexto)

    voltar = f"{reverse('files:por_usuario')}?usuario={pessoa.pk}"
    if request.method == 'POST':
        acao = request.POST.get('acao')
        tipo = request.POST.get('tipo')
        if tipo in ('arquivo', 'pasta'):
            ids = [i for i in request.POST.getlist('item') if str(i).isdigit()]
            itens = list(ITENS[tipo].objects.filter(excluido_em__isnull=True, pk__in=ids))
            if acao == 'liberar':
                n = sum(acesso.liberar(item, [pessoa], request.user, request) for item in itens)
                messages.success(request, f'{n} item(ns) liberado(s) para {pessoa.full_name}.')
            elif acao == 'retirar':
                n = sum(1 for item in itens if acesso.retirar(item, pessoa, request.user, request))
                messages.success(request, f'Acesso retirado de {n} item(ns).')
        return redirect(voltar)

    visao = acesso.Visao(pessoa)
    todos = list(acesso.arquivos().select_related('category__folder', 'uploaded_by', 'target_sector',
                                                  'target_group', 'target_user').order_by('-created_at'))
    liberados_ids = set(pessoa.arquivos_liberados.values_list('pk', flat=True))
    pastas_liberadas = list(acesso.pastas().filter(allowed_users=pessoa).order_by('name'))
    contexto.update({
        've': [a for a in todos if visao.arquivo(a)],
        'nao_ve': [a for a in todos if not visao.arquivo(a)],
        'liberados_ids': liberados_ids,
        'enviados': [a for a in todos if a.uploaded_by_id == pessoa.pk],
        'pastas_liberadas': pastas_liberadas,
        'pastas_para_liberar': [p for p in acesso.pastas().order_by('name') if p not in pastas_liberadas],
        'movimentacoes': (MovimentacaoArquivo.objects.filter(Q(usuario=pessoa) | Q(pessoa=pessoa))
                          .select_related('usuario', 'pessoa')[:20]),
        'voltar': voltar,
    })
    return render(request, 'files/por_usuario.html', contexto)
