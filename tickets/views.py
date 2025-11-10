from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.db import models
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.conf import settings
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Ticket, Category, TicketLog, TicketComment, Webhook, TicketView, TicketAssignment
from .serializers import TicketSerializer, CategorySerializer, TicketLogSerializer, TicketCommentSerializer, WebhookSerializer
from users.models import Sector, User
from core.middleware import log_action
import csv
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


@login_required
def tickets_list_view(request):
    user = request.user
    
    # Filtros de pesquisa
    search = request.GET.get('search', '')
    status_filter = request.GET.getlist('status')  # Mudado para getlist para múltiplos valores
    origem_filter = request.GET.get('origem', '')
    categoria_filter = request.GET.get('categoria', '')
    setor_filter = request.GET.get('setor', '')
    prioridade_filter = request.GET.get('prioridade', '')
    carteira_filter = request.GET.get('carteira', '')
    atribuidos_filter = request.GET.get('atribuidos', '')  # Novo filtro para chamados atribuídos
    
    # Filtros avançados para SUPERADMIN - definir logo no início
    created_by_filter = request.GET.get('created_by', '')
    created_by_sector_filter = request.GET.get('created_by_sector', '')
    assigned_to_filter = request.GET.get('assigned_to', '')
    date_from_filter = request.GET.get('date_from', '')
    date_to_filter = request.GET.get('date_to', '')
    user_hierarchy_filter = request.GET.get('user_hierarchy', '')
    has_attachments_filter = request.GET.get('has_attachments', '')
    has_comments_filter = request.GET.get('has_comments', '')
    overdue_filter = request.GET.get('overdue', '')
    
    # Filtro base: TODOS os usuários sempre veem seus próprios chamados
    base_filter = models.Q(created_by=user)
    
    # Filtrar tickets baseado na hierarquia do usuário
    if user.can_view_all_tickets():
        # Admin vê todos os tickets (incluindo fechados)
        tickets = Ticket.objects.all()
    elif user.can_view_sector_tickets():
        # Supervisores veem: seus próprios tickets + tickets dos setores + tickets atribuídos
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        
        tickets = Ticket.objects.filter(
            base_filter |  # Sempre inclui próprios tickets
            models.Q(sector__in=user_sectors) |
            models.Q(assigned_to=user) |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
        ).distinct()
    else:
        # Usuários comuns veem: seus próprios tickets + tickets atribuídos
        # Excluindo tickets fechados
        tickets = Ticket.objects.filter(
            base_filter |  # Sempre inclui próprios tickets
            models.Q(assigned_to=user) |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
        ).exclude(status='FECHADO').distinct()
    
    # Aplicar filtros adicionais
    
    # Filtro por origem
    if origem_filter == 'meus':
        tickets = tickets.filter(created_by=user)
    elif origem_filter == 'setor':
        # Tickets dos setores do usuário (excluindo os próprios)
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        tickets = tickets.filter(sector__in=user_sectors).exclude(created_by=user)
    
    # Filtro por chamados atribuídos (atribuído a mim)
    if atribuidos_filter == 'sim':
        tickets = tickets.filter(
            models.Q(assigned_to=user) |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
        ).distinct()
    
    # Filtro por status - suporte para múltiplos valores
    if status_filter:
        if 'abertos' in status_filter:
            tickets = tickets.filter(status='ABERTO')
        elif 'nao_resolvidos' in status_filter:
            tickets = tickets.exclude(status__in=['RESOLVIDO', 'FECHADO'])
        else:
            # Filtrar por múltiplos status específicos
            valid_statuses = [s for s in status_filter if s in ['ABERTO', 'EM_ANDAMENTO', 'RESOLVIDO', 'FECHADO']]
            if valid_statuses:
                tickets = tickets.filter(status__in=valid_statuses)
    
    # Filtro por categoria - SUPERADMINs podem filtrar por qualquer categoria
    if categoria_filter:
        if user.hierarchy == 'SUPERADMIN' or user.can_view_all_tickets():
            # SUPERADMIN pode filtrar por qualquer categoria
            tickets = tickets.filter(category_id=categoria_filter)
        else:
            # Outros usuários só podem filtrar pelas categorias que têm acesso
            tickets = tickets.filter(category_id=categoria_filter)
    
    # Filtro por setor - SUPERADMINs podem filtrar por qualquer setor
    if setor_filter:
        if user.hierarchy == 'SUPERADMIN' or user.can_view_all_tickets():
            # SUPERADMIN pode filtrar por qualquer setor
            tickets = tickets.filter(sector_id=setor_filter)
        else:
            # Outros usuários só podem filtrar pelos setores que têm acesso
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            sector_ids = [s.id for s in user_sectors] if user_sectors else []
            if int(setor_filter) in sector_ids:
                tickets = tickets.filter(sector_id=setor_filter)
    
    # Filtro por prioridade
    if prioridade_filter:
        tickets = tickets.filter(priority=prioridade_filter)
    
    # Filtro por carteira específica (chamados direcionados PARA setores da carteira selecionada)
    if carteira_filter:
        from communications.models import CommunicationGroup
        try:
            # Buscar o grupo de carteira específico pelo ID
            carteira_group = CommunicationGroup.objects.get(id=carteira_filter)
            
            # Obter usuários deste grupo de carteira
            carteira_users = carteira_group.members.filter(is_active=True)
            
            # Obter setores onde os usuários da carteira trabalham (corrigir relacionamentos)
            carteira_sectors = Sector.objects.filter(
                models.Q(users__in=carteira_users) |  # Setores ManyToMany
                models.Q(primary_users__in=carteira_users)  # Setor principal (ForeignKey)
            ).distinct()
            
            print(f"DEBUG: Filtro carteira ativo: Grupo '{carteira_group.name}' (ID: {carteira_filter})")
            print(f"DEBUG: Usuários da carteira ({carteira_users.count()}): {[u.username for u in carteira_users]}")
            print(f"DEBUG: Setores da carteira ({carteira_sectors.count()}): {[s.name for s in carteira_sectors]}")
            
            # Contar tickets antes do filtro
            tickets_antes = tickets.count()
            print(f"DEBUG: Tickets antes do filtro carteira: {tickets_antes}")
            
            # Filtrar chamados que foram direcionados PARA os setores da carteira
            # OU que foram atribuídos a usuários da carteira
            tickets = tickets.filter(
                models.Q(sector__in=carteira_sectors) |  # Chamados para setores da carteira
                models.Q(assigned_to__in=carteira_users) |  # Chamados atribuídos a usuários da carteira
                models.Q(additional_assignments__user__in=carteira_users, additional_assignments__is_active=True)  # Atribuições adicionais
            ).distinct()
            
            print(f"DEBUG: Tickets após filtro carteira: {tickets.count()}")
            
            # Debug adicional: verificar se há tickets nos setores da carteira
            tickets_por_setor = tickets.filter(sector__in=carteira_sectors).count()
            tickets_atribuidos = tickets.filter(assigned_to__in=carteira_users).count()
            print(f"DEBUG: Tickets por setor da carteira: {tickets_por_setor}")
            print(f"DEBUG: Tickets atribuídos a usuários da carteira: {tickets_atribuidos}")
            
        except CommunicationGroup.DoesNotExist:
            print(f"DEBUG: Grupo de carteira com ID {carteira_filter} não encontrado")
        except Exception as e:
            print(f"DEBUG: Erro no filtro carteira: {str(e)}")
    
    # Filtro por pesquisa
    if search:
        tickets = tickets.filter(
            models.Q(title__icontains=search) |
            models.Q(description__icontains=search) |
            models.Q(id__icontains=search)
        )
    
    # Aplicar ordenação
    tickets = tickets.order_by('-created_at')
    
    # Configurar paginação
    paginator = Paginator(tickets, 10)  # 10 tickets por página
    page = request.GET.get('page')
    
    try:
        tickets_page = paginator.page(page)
    except PageNotAnInteger:
        # Se a página não for um inteiro, mostrar a primeira página
        tickets_page = paginator.page(1)
    except EmptyPage:
        # Se a página estiver fora do range, mostrar a última página
        tickets_page = paginator.page(paginator.num_pages)
    
    # Preservar parâmetros de filtro para a paginação
    filter_params = {}
    if search:
        filter_params['search'] = search
    if status_filter:
        # Para múltiplos valores, precisamos tratá-los de forma especial na URL
        for status in status_filter:
            filter_params.setdefault('status', []).append(status)
    if origem_filter:
        filter_params['origem'] = origem_filter
    if categoria_filter:
        filter_params['categoria'] = categoria_filter
    if setor_filter:
        filter_params['setor'] = setor_filter
    if prioridade_filter:
        filter_params['prioridade'] = prioridade_filter
    if carteira_filter:
        filter_params['carteira'] = carteira_filter
    if atribuidos_filter:
        filter_params['atribuidos'] = atribuidos_filter
    
    # Filtros avançados para SUPERADMIN
    if user.hierarchy == 'SUPERADMIN':
        if created_by_filter:
            filter_params['created_by'] = created_by_filter
        if created_by_sector_filter:
            filter_params['created_by_sector'] = created_by_sector_filter
        if assigned_to_filter:
            filter_params['assigned_to'] = assigned_to_filter
        if date_from_filter:
            filter_params['date_from'] = date_from_filter
        if date_to_filter:
            filter_params['date_to'] = date_to_filter
        if user_hierarchy_filter:
            filter_params['user_hierarchy'] = user_hierarchy_filter
        if has_attachments_filter:
            filter_params['has_attachments'] = has_attachments_filter
        if has_comments_filter:
            filter_params['has_comments'] = has_comments_filter
        if overdue_filter:
            filter_params['overdue'] = overdue_filter
    
    # Converter parâmetros para query string
    from urllib.parse import urlencode
    filter_query_string = urlencode(filter_params)
    
    # Obter categorias e setores do usuário para os filtros
    user_sectors = list(user.sectors.all())
    if user.sector:
        user_sectors.append(user.sector)
    
    # Remover duplicatas
    user_sectors = list(set(user_sectors))
    
    # Aplicar filtros avançados apenas para SUPERADMIN
    if user.hierarchy == 'SUPERADMIN':
        # Filtro por usuário que criou
        if created_by_filter:
            tickets = tickets.filter(created_by_id=created_by_filter)
        
        # Filtro por setor do solicitante
        if created_by_sector_filter:
            tickets = tickets.filter(
                models.Q(created_by__sector_id=created_by_sector_filter) |
                models.Q(created_by__sectors__id=created_by_sector_filter)
            ).distinct()
        
        # Filtro por responsável
        if assigned_to_filter:
            if assigned_to_filter == 'unassigned':
                tickets = tickets.filter(assigned_to__isnull=True)
            else:
                tickets = tickets.filter(assigned_to_id=assigned_to_filter)
        
        # Filtro por data
        if date_from_filter:
            from django.utils.dateparse import parse_date
            date_from = parse_date(date_from_filter)
            if date_from:
                tickets = tickets.filter(created_at__date__gte=date_from)
        
        if date_to_filter:
            from django.utils.dateparse import parse_date
            date_to = parse_date(date_to_filter)
            if date_to:
                tickets = tickets.filter(created_at__date__lte=date_to)
        
        # Filtro por hierarquia do usuário
        if user_hierarchy_filter:
            tickets = tickets.filter(created_by__hierarchy=user_hierarchy_filter)
        
        # Filtro por anexos
        if has_attachments_filter == 'yes':
            tickets = tickets.filter(attachments__isnull=False).distinct()
        elif has_attachments_filter == 'no':
            tickets = tickets.filter(attachments__isnull=True)
        
        # Filtro por comentários
        if has_comments_filter == 'yes':
            tickets = tickets.filter(comments__isnull=False).distinct()
        elif has_comments_filter == 'no':
            tickets = tickets.filter(comments__isnull=True)
        
        # Filtro por prazo (em atraso)
        if overdue_filter:
            from django.utils import timezone
            now = timezone.now()
            if overdue_filter == 'yes':
                # Tickets em atraso (não resolvidos e com data de vencimento passada)
                tickets = tickets.filter(
                    models.Q(due_date__lt=now) & 
                    ~models.Q(status__in=['RESOLVIDO', 'FECHADO'])
                )
            elif overdue_filter == 'no':
                # Tickets no prazo
                tickets = tickets.filter(
                    models.Q(due_date__gte=now) | 
                    models.Q(status__in=['RESOLVIDO', 'FECHADO'])
                )

    # Verificar se é solicitação de exportação (apenas para SUPERADMIN)
    export_format = request.GET.get('export', '')
    if export_format and user.hierarchy == 'SUPERADMIN':
        if export_format == 'csv':
            return export_tickets_csv(tickets)
        elif export_format == 'xlsx':
            return export_tickets_xlsx(tickets)

    # Obter categorias e setores baseado na hierarquia do usuário
    try:
        if user.hierarchy == 'SUPERADMIN':
            # SUPERADMIN pode ver todas as categorias e setores
            user_categories = Category.objects.all().order_by('sector__name', 'name')
            all_categories = Category.objects.all().order_by('sector__name', 'name')
            all_sectors = Sector.objects.all().order_by('name')
            all_users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
        else:
            # Usuários normais veem apenas as categorias dos seus setores
            user_categories = Category.objects.filter(sector__in=user_sectors).order_by('sector__name', 'name')
            all_categories = user_categories  # Mesma coisa para usuários normais
            all_sectors = user_sectors
            all_users = []  # Usuários normais não precisam desta lista
        
        # Obter grupos de carteira para todos os usuários (busca case-insensitive)
        from communications.models import CommunicationGroup
        carteira_groups = CommunicationGroup.objects.filter(name__icontains='carteira').order_by('name')
        
    except Exception as e:
        # Em caso de erro, usar valores padrão vazios
        print(f"Erro ao carregar categorias e setores: {str(e)}")
        user_categories = []
        all_categories = []
        all_sectors = []
        all_users = []
        carteira_groups = []
    
    # Obter nomes para exibição dos filtros aplicados
    categoria_name = ''
    setor_name = ''
    created_by_name = ''
    assigned_to_name = ''
    
    if categoria_filter:
        try:
            categoria_obj = Category.objects.get(id=categoria_filter)
            categoria_name = categoria_obj.name
        except Category.DoesNotExist:
            pass
    
    if setor_filter:
        try:
            setor_obj = Sector.objects.get(id=setor_filter)
            setor_name = setor_obj.name
        except Sector.DoesNotExist:
            pass
    
    if created_by_filter and user.hierarchy == 'SUPERADMIN':
        try:
            created_by_obj = User.objects.get(id=created_by_filter)
            created_by_name = created_by_obj.get_full_name()
        except User.DoesNotExist:
            pass
    
    if assigned_to_filter and user.hierarchy == 'SUPERADMIN':
        if assigned_to_filter == 'unassigned':
            assigned_to_name = 'Não Atribuído'
        else:
            try:
                assigned_to_obj = User.objects.get(id=assigned_to_filter)
                assigned_to_name = assigned_to_obj.get_full_name()
            except User.DoesNotExist:
                pass
    
    context = {
        'tickets': tickets_page,
        'user': user,
        'search': search,
        'status': status_filter,
        'origem': origem_filter,
        'categoria': categoria_filter,
        'setor': setor_filter,
        'prioridade': prioridade_filter,
        'carteira': carteira_filter,
        'atribuidos': atribuidos_filter,
        'categoria_name': categoria_name,
        'setor_name': setor_name,
        'user_categories': user_categories,
        'user_sectors': user_sectors,
        'carteira_groups': carteira_groups,
        'paginator': paginator,
        'page_obj': tickets_page,
        # Filtros avançados para SUPERADMIN
        'created_by': created_by_filter,
        'created_by_sector': created_by_sector_filter,
        'assigned_to': assigned_to_filter,
        'date_from': date_from_filter,
        'date_to': date_to_filter,
        'user_hierarchy': user_hierarchy_filter,
        'has_attachments': has_attachments_filter,
        'has_comments': has_comments_filter,
        'overdue': overdue_filter,
        'created_by_name': created_by_name,
        'assigned_to_name': assigned_to_name,
        # Dados completos para SUPERADMIN
        'all_categories': all_categories if user.hierarchy == 'SUPERADMIN' else user_categories,
        'all_sectors': all_sectors if user.hierarchy == 'SUPERADMIN' else user_sectors,
        'all_users': all_users if user.hierarchy == 'SUPERADMIN' else [],
        # Parâmetros de filtro para preservar na paginação
        'filter_query_string': filter_query_string,
        'filter_params': filter_params,
    }
    return render(request, 'tickets/list.html', context)


@login_required
def tickets_history_view(request):
    """View para mostrar histórico de chamados concluídos"""
    user = request.user
    
    # Filtro base: TODOS os usuários sempre veem seus próprios chamados fechados
    base_filter = models.Q(created_by=user, status='FECHADO')
    
    # Filtrar apenas tickets fechados baseado na hierarquia do usuário
    if user.can_view_all_tickets():
        # Admin vê todos os tickets fechados
        tickets = Ticket.objects.filter(status='FECHADO')
    elif user.can_view_sector_tickets():
        # Supervisores veem: próprios tickets fechados + tickets fechados dos setores + atribuídos fechados
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
            
        tickets = Ticket.objects.filter(
            base_filter |  # Sempre inclui próprios tickets fechados
            models.Q(sector__in=user_sectors, status='FECHADO') |
            models.Q(assigned_to=user, status='FECHADO')
        ).distinct()
    else:
        # Usuários comuns veem: próprios tickets fechados + atribuídos fechados
        tickets = Ticket.objects.filter(
            base_filter |  # Sempre inclui próprios tickets fechados
            models.Q(assigned_to=user, status='FECHADO') |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True, status='FECHADO')
        ).distinct()
    
    # Aplicar ordenação
    tickets = tickets.order_by('-closed_at')
    
    # Configurar paginação
    paginator = Paginator(tickets, 10)  # 15 tickets por página
    page = request.GET.get('page')
    
    try:
        tickets_page = paginator.page(page)
    except PageNotAnInteger:
        # Se a página não for um inteiro, mostrar a primeira página
        tickets_page = paginator.page(1)
    except EmptyPage:
        # Se a página estiver fora do range, mostrar a última página
        tickets_page = paginator.page(paginator.num_pages)
    
    context = {
        'tickets': tickets_page,
        'user': user,
        'is_history': True,
        'paginator': paginator,
        'page_obj': tickets_page,
    }
    return render(request, 'tickets/history.html', context)


@login_required
def ticket_detail_view(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    user = request.user
    
    # Verificar permissão para visualizar o ticket
    user_sectors = list(user.sectors.all())
    if user.sector:
        user_sectors.append(user.sector)
    
    can_view = (
        user.can_view_all_tickets() or 
        (user.can_view_sector_tickets() and ticket.sector in user_sectors) or
        ticket.created_by == user or
        user in ticket.get_all_assigned_users()
    )
    
    if not can_view:
        messages.error(request, 'Você não tem permissão para visualizar este chamado.')
        return redirect('tickets_list')
    
    # Processar upload de arquivos via POST
    if request.method == 'POST' and 'upload_files' in request.POST:
        # Verificar permissão para adicionar arquivos
        can_upload = (
            user.can_view_all_tickets() or 
            (user.can_view_sector_tickets() and ticket.sector in user_sectors) or
            ticket.created_by == user or
            ticket.assigned_to == user or
            user in ticket.get_all_assigned_users()
        )
        
        if not can_upload:
            messages.error(request, 'Você não tem permissão para adicionar arquivos neste chamado.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        # Processar arquivos anexados
        from .models import TicketAttachment
        attachments = request.FILES.getlist('new_attachments')
        
        if not attachments:
            messages.error(request, 'Nenhum arquivo foi selecionado.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        uploaded_count = 0
        for attachment in attachments:
            # Verificar tamanho do arquivo (limite de 50MB por exemplo)
            if attachment.size > 50 * 1024 * 1024:  # 50MB
                messages.warning(request, f'Arquivo "{attachment.name}" é muito grande (máximo 50MB). Arquivo ignorado.')
                continue
                
            TicketAttachment.objects.create(
                ticket=ticket,
                file=attachment,
                original_filename=attachment.name,
                file_size=attachment.size,
                content_type=attachment.content_type,
                uploaded_by=user
            )
            uploaded_count += 1
        
        if uploaded_count > 0:
            # Adicionar comentário informativo sobre os arquivos adicionados
            TicketComment.objects.create(
                ticket=ticket,
                user=user,
                comment=f'{uploaded_count} arquivo(s) adicionado(s) ao chamado.',
                comment_type='COMMENT'
            )
            
            messages.success(request, f'{uploaded_count} arquivo(s) adicionado(s) com sucesso!')
            log_action(
                user, 
                'TICKET_ATTACHMENT', 
                f'{uploaded_count} arquivo(s) adicionado(s) ao chamado #{ticket.id}',
                request
            )
        
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    # Marcar como visualizado
    ticket.mark_as_viewed(user)
    
    # Verificar se pode assumir o chamado
    can_assume = ticket.can_assume(user)
    
    # Verificar se pode atribuir outros usuários
    can_assign = (
        user.can_view_sector_tickets() or 
        user.can_view_all_tickets() or
        ticket.assigned_to == user
    )
    
    # Verificar se pode fazer upload de arquivos
    can_upload = (
        user.can_view_all_tickets() or 
        (user.can_view_sector_tickets() and ticket.sector in user_sectors) or
        ticket.created_by == user or
        ticket.assigned_to == user or
        user in ticket.get_all_assigned_users()
    )
    
    # Buscar usuários para atribuição (todos os setores) - sempre disponível
    sector_users = User.objects.filter(is_active=True).exclude(id=user.id).order_by('sector__name', 'first_name')
    
    context = {
        'ticket': ticket,
        'logs': ticket.logs.all(),
        'comments': ticket.comments.all(),
        'user': user,
        'can_assume': can_assume,
        'can_assign': can_assign,
        'can_upload': can_upload,
        'assigned_users': ticket.get_all_assigned_users(),
        'additional_assignments': ticket.additional_assignments.filter(is_active=True).select_related('user', 'assigned_by'),
        'sector_users': sector_users,
    }
    return render(request, 'tickets/detail.html', context)


@login_required
def assume_ticket_view(request, ticket_id):
    """Assumir um chamado"""
    if request.method == 'POST':
        ticket = get_object_or_404(Ticket, id=ticket_id)
        comment = request.POST.get('comment', '')
        
        if ticket.assume_ticket(request.user, comment):
            messages.success(request, f'Chamado #{ticket.id} assumido com sucesso!')
            log_action(
                request.user, 
                'TICKET_ASSUME', 
                f'Chamado #{ticket.id} assumido',
                request
            )
        else:
            messages.error(request, 'Não foi possível assumir este chamado.')
        
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    return redirect('tickets_list')


@login_required
def add_comment_view(request, ticket_id):
    """Adicionar comentário ao chamado"""
    if request.method == 'POST':
        ticket = get_object_or_404(Ticket, id=ticket_id)
        comment_text = request.POST.get('comment')
        comment_type = request.POST.get('comment_type', 'COMMENT')
        assigned_user_id = request.POST.get('assigned_to')
        
        # Verificar permissão para comentar
        can_comment = (
            request.user.can_view_all_tickets() or 
            (request.user.can_view_sector_tickets() and ticket.sector == request.user.sector) or
            ticket.created_by == request.user or
            request.user in ticket.get_all_assigned_users()
        )
        
        if not can_comment:
            messages.error(request, 'Você não tem permissão para comentar neste chamado.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        if not comment_text:
            messages.error(request, 'Comentário é obrigatório.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        # Criar comentário
        comment = TicketComment.objects.create(
            ticket=ticket,
            user=request.user,
            comment=comment_text,
            comment_type=comment_type
        )
        
        # Se é uma atribuição, adicionar usuário
        if comment_type == 'ASSIGNMENT' and assigned_user_id:
            from users.models import User
            assigned_user = get_object_or_404(User, id=assigned_user_id)
            assignment = ticket.assign_additional_user(assigned_user, request.user, comment_text)
            comment.assigned_to = assigned_user
            comment.save()
        
        messages.success(request, 'Comentário adicionado com sucesso!')
        log_action(
            request.user, 
            'TICKET_COMMENT', 
            f'Comentário adicionado ao chamado #{ticket.id}',
            request
        )
        
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    return redirect('tickets_list')


@login_required
def update_ticket_status_view(request, ticket_id):
    """Atualizar status do chamado"""
    if request.method == 'POST':
        ticket = get_object_or_404(Ticket, id=ticket_id)
        new_status = request.POST.get('status')
        observation = request.POST.get('observation', '')
        solution = request.POST.get('solution', '')
        
        # Verificar permissão para atualizar
        can_update = (
            request.user.can_view_all_tickets() or 
            (request.user.can_view_sector_tickets() and ticket.sector == request.user.sector) or
            ticket.assigned_to == request.user or
            request.user in ticket.get_all_assigned_users() or
            ticket.created_by == request.user  # Criador pode aprovar/reprovar
        )
        
        if not can_update:
            messages.error(request, 'Você não tem permissão para atualizar este chamado.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        if not new_status:
            messages.error(request, 'Status é obrigatório.')
            return redirect('ticket_detail', ticket_id=ticket.id)
        
        old_status = ticket.status
        ticket.status = new_status
        
        if new_status == 'RESOLVIDO':
            ticket.resolved_at = timezone.now()
            if solution:
                ticket.solution = solution
            else:
                messages.error(request, 'Solução é obrigatória para marcar como resolvido.')
                return redirect('ticket_detail', ticket_id=ticket.id)
            
            # Se a categoria não requer aprovação, fechar direto
            if not ticket.category.requires_approval:
                ticket.status = 'FECHADO'
                ticket.closed_at = timezone.now()
        elif new_status == 'FECHADO':
            ticket.closed_at = timezone.now()
        elif new_status == 'EM_ANDAMENTO' and old_status in ['RESOLVIDO', 'FECHADO', 'AGUARDANDO_APROVACAO']:
            # Reabertura do chamado - limpar campos de resolução se necessário
            if old_status in ['RESOLVIDO', 'FECHADO']:
                ticket.resolved_at = None
                ticket.closed_at = None
        
        ticket.save()
        
        # Criar log
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            old_status=old_status,
            new_status=new_status,
            observation=observation
        )
        
        # Criar comentário se houver observação
        if observation:
            comment_type = 'STATUS_CHANGE'
            if new_status == 'FECHADO' and old_status == 'RESOLVIDO':
                comment_type = 'COMMENT'
                observation = f"Solução aprovada pelo usuário. {observation}"
            elif new_status == 'EM_ANDAMENTO' and old_status in ['RESOLVIDO', 'FECHADO', 'AGUARDANDO_APROVACAO']:
                comment_type = 'COMMENT'
                if old_status == 'RESOLVIDO':
                    observation = f"Solução reprovada pelo usuário. Motivo: {observation}"
                else:
                    observation = f"Chamado reaberto. Motivo: {observation}"
                
            TicketComment.objects.create(
                ticket=ticket,
                user=request.user,
                comment=observation,
                comment_type=comment_type
            )
        
        # Mensagem de sucesso personalizada
        if ticket.status == 'FECHADO' and old_status == 'RESOLVIDO':
            # Fechamento direto sem aprovação
            messages.success(request, f'Chamado #{ticket.id} resolvido e fechado automaticamente (categoria não requer aprovação do usuário).')
        elif new_status == 'RESOLVIDO' and ticket.category.requires_approval:
            messages.success(request, f'Chamado #{ticket.id} marcado como resolvido. Aguardando aprovação do usuário.')
        elif new_status == 'FECHADO':
            messages.success(request, f'Chamado #{ticket.id} fechado com sucesso!')
        elif new_status == 'EM_ANDAMENTO' and old_status in ['RESOLVIDO', 'FECHADO', 'AGUARDANDO_APROVACAO']:
            messages.warning(request, f'Chamado #{ticket.id} foi reaberto e retornou para "Em Andamento".')
        else:
            messages.success(request, f'Status do chamado #{ticket.id} atualizado com sucesso!')
        
        log_action(
            request.user, 
            'TICKET_UPDATE', 
            f'Status do chamado #{ticket.id} alterado: {old_status} → {new_status}',
            request
        )
        
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    return redirect('tickets_list')


@login_required
def ticket_create_view(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        sector_id = request.POST.get('sector')
        category_id = request.POST.get('category')
        requires_approval = request.POST.get('requires_approval') == 'on'
        approval_user_id = request.POST.get('approval_user')
        
        # Validar que a descrição não está vazia
        if not description or description.strip() == '':
            messages.error(request, 'O campo Mensagem é obrigatório.')
            sectors = Sector.objects.all()
            users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('sector__name', 'first_name')
            return render(request, 'tickets/create.html', {
                'sectors': sectors,
                'users': users,
                'title': title,
                'sector_id': sector_id,
                'category_id': category_id,
            })
        
        sector = get_object_or_404(Sector, id=sector_id)
        category = get_object_or_404(Category, id=category_id)
        
        # Novos campos opcionais
        store_location = request.POST.get('store_location', '').strip() or None
        responsible_person = request.POST.get('responsible_person', '').strip() or None
        phone = request.POST.get('phone', '').strip() or None
        
        # Criar ticket
        ticket = Ticket.objects.create(
            title=title,
            description=description,
            sector=sector,
            category=category,
            created_by=request.user,
            requires_approval=requires_approval or category.requires_approval,
            approval_user_id=approval_user_id if requires_approval else None,
            solution_time_hours=int(request.POST.get('solution_time_hours', 24)),
            priority=request.POST.get('priority', 'MEDIA'),
            store_location=store_location,
            responsible_person=responsible_person,
            phone=phone
        )
        
        # Processar arquivos anexados
        from .models import TicketAttachment
        attachments = request.FILES.getlist('attachments')
        for attachment in attachments:
            TicketAttachment.objects.create(
                ticket=ticket,
                file=attachment,
                original_filename=attachment.name,
                file_size=attachment.size,
                content_type=attachment.content_type,
                uploaded_by=request.user
            )
        
        # Criar log inicial
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            new_status='ABERTO',
            observation='Chamado criado'
        )
        
        log_action(
            request.user, 
            'TICKET_CREATE', 
            f'Chamado criado: #{ticket.id} - {ticket.title}',
            request
        )
        
        if attachments:
            messages.success(request, f'Chamado #{ticket.id} criado com sucesso! {len(attachments)} arquivo(s) anexado(s).')
        else:
            messages.success(request, f'Chamado #{ticket.id} criado com sucesso!')
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    sectors = Sector.objects.all()
    # Buscar todos os usuários ativos para seleção
    users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('sector__name', 'first_name')
    context = {
        'sectors': sectors,
        'users': users,
    }
    return render(request, 'tickets/create.html', context)


def get_categories_by_sector(request):
    sector_id = request.GET.get('sector_id')
    if sector_id:
        categories = Category.objects.filter(sector_id=sector_id, is_active=True)
        data = [{'id': cat.id, 'name': cat.name, 'default_description': cat.default_description, 'default_solution_time_hours': cat.default_solution_time_hours} for cat in categories]
        return JsonResponse({'categories': data})
    return JsonResponse({'categories': []})
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from .models import Ticket, Category, TicketLog, TicketComment, Webhook
from .serializers import TicketSerializer, CategorySerializer, TicketLogSerializer, TicketCommentSerializer, WebhookSerializer
from users.models import Sector
from core.middleware import log_action


@login_required
def tickets_list_view_duplicate(request):
    user = request.user
    
    # Filtro base: TODOS os usuários sempre veem seus próprios chamados
    base_filter = models.Q(created_by=user)
    
    # Filtrar tickets baseado na hierarquia do usuário
    if user.can_view_all_tickets():
        tickets = Ticket.objects.all()
    elif user.can_view_sector_tickets():
        # Ver tickets dos setores + próprios tickets + atribuídos
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        tickets = Ticket.objects.filter(
            base_filter |  # Sempre inclui próprios tickets
            models.Q(sector__in=user_sectors) |
            models.Q(assigned_to=user)
        ).distinct()
    else:
        tickets = Ticket.objects.filter(
            base_filter |  # Sempre inclui próprios tickets
            models.Q(assigned_to=user) |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
        ).exclude(status='FECHADO').distinct()
    
    context = {
        'tickets': tickets.order_by('-created_at'),
        'user': user,
    }
    return render(request, 'tickets/list.html', context)


def get_categories_by_sector(request):
    sector_id = request.GET.get('sector_id')
    if sector_id:
        categories = Category.objects.filter(sector_id=sector_id, is_active=True)
        data = [{'id': cat.id, 'name': cat.name, 'default_description': cat.default_description, 'default_solution_time_hours': cat.default_solution_time_hours} for cat in categories]
        return JsonResponse({'categories': data})
    return JsonResponse({'categories': []})


class TicketViewSet(viewsets.ModelViewSet):
    queryset = Ticket.objects.all()
    serializer_class = TicketSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        
        # Filtro base: TODOS os usuários sempre veem seus próprios chamados
        base_filter = models.Q(created_by=user)
        
        if user.can_view_all_tickets():
            return Ticket.objects.all()
        elif user.can_view_sector_tickets():
            # Ver tickets dos setores + próprios tickets + atribuídos
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            return Ticket.objects.filter(
                base_filter |  # Sempre inclui próprios tickets
                models.Q(sector__in=user_sectors) |
                models.Q(assigned_to=user)
            ).distinct()
        else:
            return Ticket.objects.filter(
                base_filter |  # Sempre inclui próprios tickets
                models.Q(assigned_to=user) |
                models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
            ).exclude(status='FECHADO').distinct()
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        ticket = self.get_object()
        new_status = request.data.get('status')
        observation = request.data.get('observation', '')
        solution = request.data.get('solution', '')
        
        if not new_status:
            return Response(
                {'error': 'Status é obrigatório'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        old_status = ticket.status
        ticket.status = new_status
        
        if new_status == 'RESOLVIDO':
            ticket.resolved_at = timezone.now()
            ticket.solution = solution
            # Se a categoria não requer aprovação, vai direto para fechado
            if not ticket.category.requires_approval:
                ticket.status = 'FECHADO'
                ticket.closed_at = timezone.now()
        elif new_status == 'FECHADO':
            ticket.closed_at = timezone.now()
        elif new_status == 'EM_ANDAMENTO' and old_status in ['RESOLVIDO', 'FECHADO', 'AGUARDANDO_APROVACAO']:
            # Reabertura do chamado - limpar campos de resolução se necessário
            if old_status in ['RESOLVIDO', 'FECHADO']:
                ticket.resolved_at = None
                ticket.closed_at = None
        
        ticket.save()
        
        # Criar log
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            old_status=old_status,
            new_status=new_status,
            observation=observation
        )
        
        log_action(
            request.user, 
            'TICKET_UPDATE', 
            f'Status do chamado #{ticket.id} alterado: {old_status} → {new_status}',
            request
        )
        
        return Response({'message': 'Status atualizado com sucesso'})
    
    @action(detail=True, methods=['post'])
    def add_comment(self, request, pk=None):
        ticket = self.get_object()
        comment_text = request.data.get('comment')
        
        if not comment_text:
            return Response(
                {'error': 'Comentário é obrigatório'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        comment = TicketComment.objects.create(
            ticket=ticket,
            user=request.user,
            comment=comment_text
        )
        
        serializer = TicketCommentSerializer(comment)
        return Response(serializer.data)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated]


# ViewSets públicos (sem autenticação) para produção
class PublicTicketViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet público apenas para leitura de tickets"""
    queryset = Ticket.objects.all()
    serializer_class = TicketSerializer
    permission_classes = []  # Sem autenticação
    
    def get_queryset(self):
        """Retorna apenas informações básicas dos tickets"""
        return Ticket.objects.filter(
            status__in=['open', 'in_progress', 'waiting']
        ).select_related('category', 'sector', 'created_by')


class PublicCategoryViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet público apenas para leitura de categorias"""
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = []  # Sem autenticação


@login_required
def manage_webhooks_view(request):
    """Gerenciar webhooks"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    webhooks = Webhook.objects.all().select_related('category', 'sector')
    active_webhooks_count = webhooks.filter(is_active=True).count()
    inactive_webhooks_count = webhooks.filter(is_active=False).count()
    
    context = {
        'webhooks': webhooks,
        'active_webhooks_count': active_webhooks_count,
        'inactive_webhooks_count': inactive_webhooks_count,
        'user': request.user,
    }
    return render(request, 'admin/webhooks.html', context)


@login_required
def create_webhook_view(request):
    """Criar novo webhook"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        url = request.POST.get('url')
        events = request.POST.getlist('events')  # Pegar lista de eventos selecionados
        category_id = request.POST.get('category')
        sector_id = request.POST.get('sector')
        is_active = request.POST.get('is_active') == 'on'
        headers = request.POST.get('headers', '{}')
        
        try:
            # Validar se pelo menos um evento foi selecionado
            if not events:
                messages.error(request, 'Por favor, selecione pelo menos um evento.')
                context = {
                    'event_choices': Webhook.EVENT_CHOICES,
                    'categories': Category.objects.all(),
                    'sectors': Sector.objects.all(),
                    'user': request.user,
                }
                return render(request, 'admin/create_webhook.html', context)
            
            # Validar e parsear headers JSON
            import json
            try:
                headers_dict = json.loads(headers) if headers.strip() else {}
            except json.JSONDecodeError:
                headers_dict = {}
            
            category = get_object_or_404(Category, id=category_id) if category_id else None
            sector = get_object_or_404(Sector, id=sector_id) if sector_id else None
            
            # Criar um webhook para cada evento selecionado
            created_webhooks = []
            for event in events:
                webhook_name = f"{name} - {dict(Webhook.EVENT_CHOICES)[event]}"
                
                webhook = Webhook.objects.create(
                    name=webhook_name,
                    url=url,
                    event=event,
                    category=category,
                    sector=sector,
                    is_active=is_active,
                    headers=headers_dict
                )
                created_webhooks.append(webhook)
            
            log_action(
                request.user, 
                'WEBHOOK_CREATE', 
                f'Webhooks criados: {len(created_webhooks)} webhook(s) para {name}',
                request
            )
            
            if len(created_webhooks) == 1:
                messages.success(request, f'Webhook "{created_webhooks[0].name}" criado com sucesso!')
            else:
                messages.success(request, f'{len(created_webhooks)} webhooks criados com sucesso para "{name}"!')
            return redirect('manage_webhooks')
            
        except Exception as e:
            messages.error(request, f'Erro ao criar webhook: {str(e)}')
    
    context = {
        'event_choices': Webhook.EVENT_CHOICES,
        'categories': Category.objects.all(),
        'sectors': Sector.objects.all(),
        'user': request.user,
    }
    return render(request, 'admin/create_webhook.html', context)


@login_required
def edit_webhook_view(request, webhook_id):
    """Editar webhook existente"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    webhook = get_object_or_404(Webhook, id=webhook_id)
    
    if request.method == 'POST':
        webhook.name = request.POST.get('name')
        webhook.url = request.POST.get('url')
        webhook.event = request.POST.get('event')
        
        category_id = request.POST.get('category')
        sector_id = request.POST.get('sector')
        
        webhook.category = get_object_or_404(Category, id=category_id) if category_id else None
        webhook.sector = get_object_or_404(Sector, id=sector_id) if sector_id else None
        webhook.is_active = request.POST.get('is_active') == 'on'
        
        headers = request.POST.get('headers', '{}')
        try:
            import json
            webhook.headers = json.loads(headers) if headers.strip() else {}
        except json.JSONDecodeError:
            webhook.headers = {}
        
        try:
            webhook.save()
            
            log_action(
                request.user,
                'WEBHOOK_UPDATE',
                f'Webhook atualizado: {webhook.name}',
                request
            )
            
            messages.success(request, f'Webhook "{webhook.name}" atualizado com sucesso!')
            return redirect('manage_webhooks')
            
        except Exception as e:
            messages.error(request, f'Erro ao atualizar webhook: {str(e)}')
    
    context = {
        'webhook': webhook,
        'event_choices': Webhook.EVENT_CHOICES,
        'categories': Category.objects.all(),
        'sectors': Sector.objects.all(),
        'user': request.user,
    }
    return render(request, 'admin/edit_webhook.html', context)


@login_required
def delete_webhook_view(request, webhook_id):
    """Excluir webhook"""
    if not request.user.can_manage_users():
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('dashboard')
    
    webhook = get_object_or_404(Webhook, id=webhook_id)
    
    if request.method == 'POST':
        webhook_name = webhook.name
        webhook.delete()
        
        log_action(
            request.user,
            'WEBHOOK_DELETE',
            f'Webhook excluído: {webhook_name}',
            request
        )
        
        messages.success(request, f'Webhook "{webhook_name}" excluído com sucesso!')
        return redirect('manage_webhooks')
    
    context = {
        'webhook': webhook,
        'user': request.user,
    }
    return render(request, 'admin/delete_webhook.html', context)


class WebhookViewSet(viewsets.ModelViewSet):
    queryset = Webhook.objects.all()
    serializer_class = WebhookSerializer
    permission_classes = [IsAuthenticated]
    
    @action(detail=True, methods=['post'])
    def test(self, request, pk=None):
        webhook = self.get_object()
        
        # Criar payload de teste
        test_payload = {
            'event': webhook.event,
            'webhook_name': webhook.name,
            'test': True,
            'timestamp': timezone.now().isoformat(),
            'message': 'Este é um teste do webhook'
        }
        
        try:
            import requests
            response = requests.post(webhook.url, json=test_payload, timeout=10)
            return Response({
                'message': 'Webhook testado com sucesso',
                'status_code': response.status_code,
                'response': response.text[:500]
            })
        except Exception as e:
            return Response({
                'error': f'Erro ao testar webhook: {str(e)}'
            }, status=status.HTTP_400_BAD_REQUEST)


@login_required
def ticket_create_fixed_view(request):
    """View corrigida para criação de tickets com todos os usuários"""
    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        sector_id = request.POST.get('sector')
        category_id = request.POST.get('category')
        requires_approval = request.POST.get('requires_approval') == 'on'
        approval_user_id = request.POST.get('approval_user')
        assigned_user_id = request.POST.get('copy')
        
        sector = get_object_or_404(Sector, id=sector_id)
        category = get_object_or_404(Category, id=category_id)
        
        # Criar ticket
        ticket = Ticket.objects.create(
            title=title,
            description=description,
            sector=sector,
            category=category,
            created_by=request.user,
            requires_approval=requires_approval or category.requires_approval,
            approval_user_id=approval_user_id if requires_approval else None,
            assigned_to_id=assigned_user_id if assigned_user_id else None,
            solution_time_hours=int(request.POST.get('solution_time_hours', 24)),
            priority=request.POST.get('priority', 'MEDIA')
        )
        
        # Processar arquivos anexados
        from .models import TicketAttachment
        attachments = request.FILES.getlist('attachments')
        for attachment in attachments:
            TicketAttachment.objects.create(
                ticket=ticket,
                file=attachment,
                original_filename=attachment.name,
                file_size=attachment.size,
                content_type=attachment.content_type,
                uploaded_by=request.user
            )
        
        # Criar log inicial
        TicketLog.objects.create(
            ticket=ticket,
            user=request.user,
            new_status='ABERTO',
            observation='Chamado criado'
        )
        
        log_action(
            request.user, 
            'TICKET_CREATE', 
            f'Chamado criado: #{ticket.id} - {ticket.title}',
            request
        )
        
        if attachments:
            messages.success(request, f'Chamado #{ticket.id} criado com sucesso! {len(attachments)} arquivo(s) anexado(s).')
        else:
            messages.success(request, f'Chamado #{ticket.id} criado com sucesso!')
        return redirect('ticket_detail', ticket_id=ticket.id)
    
    sectors = Sector.objects.all()
    # Buscar todos os usuários ativos para cópia, exceto o usuário atual
    users = User.objects.filter(is_active=True).exclude(id=request.user.id).order_by('sector__name', 'first_name')
    context = {
        'sectors': sectors,
        'users': users,
    }
    return render(request, 'tickets/create.html', context)


# ========================
# PURCHASE ORDER API VIEWS
# ========================

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from .models import PurchaseOrderApproval, TicketComment
import json


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve_purchase_order(request, ticket_id, approval_id):
    """API para aprovar uma ordem de compra"""
    try:
        approval = PurchaseOrderApproval.objects.get(
            id=approval_id,
            ticket_id=ticket_id,
            approver=request.user,
            status='PENDING'
        )
        
        comment = request.data.get('comment', '')
        approval.approve(comment)
        
        # Adicionar comentário no ticket
        TicketComment.objects.create(
            ticket=approval.ticket,
            user=request.user,
            comment=f"Ordem de compra aprovada (R$ {approval.amount:.2f}). {comment}".strip(),
            comment_type='COMMENT'
        )
        
        return Response({
            'message': 'Ordem de compra aprovada com sucesso',
            'status': 'approved',
            'next_step': approval.approval_step + 1 if approval.approval_step < 3 else 'completed'
        })
        
    except PurchaseOrderApproval.DoesNotExist:
        return Response({
            'error': 'Aprovação não encontrada ou você não tem permissão para aprová-la'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            'error': f'Erro ao processar aprovação: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject_purchase_order(request, ticket_id, approval_id):
    """API para rejeitar uma ordem de compra"""
    try:
        approval = PurchaseOrderApproval.objects.get(
            id=approval_id,
            ticket_id=ticket_id,
            approver=request.user,
            status='PENDING'
        )
        
        comment = request.data.get('comment', 'Ordem rejeitada')
        approval.reject(comment)
        
        # Adicionar comentário no ticket
        TicketComment.objects.create(
            ticket=approval.ticket,
            user=request.user,
            comment=f"Ordem de compra rejeitada (R$ {approval.amount:.2f}). Motivo: {comment}",
            comment_type='COMMENT'
        )
        
        # Atualizar status do ticket
        approval.ticket.status = 'REJEITADO'
        approval.ticket.save()
        
        return Response({
            'message': 'Ordem de compra rejeitada',
            'status': 'rejected',
            'reason': comment
        })
        
    except PurchaseOrderApproval.DoesNotExist:
        return Response({
            'error': 'Aprovação não encontrada ou você não tem permissão para rejeitá-la'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            'error': f'Erro ao processar rejeição: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def pending_approvals(request):
    """API para listar aprovações pendentes do usuário"""
    try:
        approvals = PurchaseOrderApproval.objects.filter(
            approver=request.user,
            status='PENDING'
        ).select_related('ticket', 'ticket__category', 'ticket__created_by')
        
        approvals_data = []
        for approval in approvals:
            approvals_data.append({
                'id': approval.id,
                'ticket_id': approval.ticket.id,
                'ticket_title': approval.ticket.title,
                'amount': float(approval.amount),
                'created_at': approval.created_at.isoformat(),
                'approval_step': approval.approval_step,
                'created_by': approval.ticket.created_by.full_name,
                'category': approval.ticket.category.name,
            })
        
        return Response({
            'approvals': approvals_data,
            'count': len(approvals_data)
        })
        
    except Exception as e:
        return Response({
            'error': f'Erro ao carregar aprovações: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def user_tickets_api(request, user_id):
    """
    API REST para buscar chamados de um usuário específico
    URL: /api/users/{user_id}/tickets/
    Retorna quantidade e títulos dos chamados do usuário
    """
    try:
        # Buscar o usuário
        user = get_object_or_404(User, id=user_id)
        
        # Buscar todos os tickets do usuário
        tickets = Ticket.objects.filter(created_by=user).select_related('category', 'sector')
        
        # Preparar dados para resposta
        tickets_data = []
        for ticket in tickets:
            tickets_data.append({
                'id': ticket.id,
                'title': ticket.title,
                'status': ticket.status,
                'category': ticket.category.name if ticket.category else None,
                'sector': ticket.sector.name if ticket.sector else None,
                'created_at': ticket.created_at.isoformat(),
                'updated_at': ticket.updated_at.isoformat(),
            })
        
        # Contar por status
        status_counts = {
            'total': tickets.count(),
            'open': tickets.filter(status='OPEN').count(),
            'in_progress': tickets.filter(status='IN_PROGRESS').count(),
            'closed': tickets.filter(status='CLOSED').count(),
            'cancelled': tickets.filter(status='CANCELLED').count(),
        }
        
        return JsonResponse({
            'user': {
                'id': user.id,
                'username': user.username,
                'full_name': user.full_name,
                'email': user.email,
            },
            'tickets': {
                'count': status_counts['total'],
                'status_breakdown': status_counts,
                'data': tickets_data
            },
            'success': True
        })
        
    except User.DoesNotExist:
        return JsonResponse({
            'error': 'Usuário não encontrado',
            'success': False
        }, status=404)
        
    except Exception as e:
        return JsonResponse({
            'error': f'Erro interno do servidor: {str(e)}',
            'success': False
        }, status=500)


def export_tickets_csv(tickets):
    """Exporta tickets para CSV com descrição completa"""
    # Criar resposta HTTP com tipo CSV
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="chamados_completo_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    
    # Adicionar BOM para Excel reconhecer UTF-8
    response.write('\ufeff')
    
    writer = csv.writer(response)
    
    # Cabeçalhos expandidos
    writer.writerow([
        'ID',
        'Título',
        'Status',
        'Prioridade',
        'Categoria',
        'Setor',
        'Solicitante',
        'Email Solicitante',
        'Responsável',
        'Data Criação',
        'Data Atualização',
        'Descrição Completa',
        'Imagens Anexadas',
        'Arquivos Anexados'
    ])
    
    # Dados
    for ticket in tickets.select_related('created_by', 'assigned_to', 'category', 'sector').prefetch_related('attachments'):
        # Processar anexos
        attachments = ticket.attachments.all()
        images_list = []
        files_list = []
        
        if attachments.exists():
            for attachment in attachments:
                # Construir URL completo do anexo
                # Em produção com MinIO, MEDIA_URL já contém o endpoint completo
                if hasattr(settings, 'USE_S3') and settings.USE_S3:
                    # MinIO: MEDIA_URL já inclui endpoint + bucket, precisa adicionar /media/
                    file_url = f"{settings.MEDIA_URL.rstrip('/')}/media/{attachment.file.name}"
                elif hasattr(settings, 'MEDIA_URL') and settings.MEDIA_URL.startswith('http'):
                    # URL absoluta (CDN ou similar)
                    file_url = f"{settings.MEDIA_URL.rstrip('/')}/{attachment.file.name}"
                elif hasattr(settings, 'MEDIA_URL') and hasattr(settings, 'SITE_URL'):
                    # URL relativa + domínio do site
                    file_url = f"{settings.SITE_URL.rstrip('/')}{settings.MEDIA_URL.rstrip('/')}/{attachment.file.name}"
                else:
                    # Fallback para métodos padrão
                    file_url = attachment.file.url if hasattr(attachment.file, 'url') else attachment.file.name
                
                # Verificar se é imagem
                is_image = False
                if attachment.content_type:
                    is_image = attachment.content_type.startswith('image/')
                elif attachment.original_filename:
                    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg']
                    is_image = any(attachment.original_filename.lower().endswith(ext) for ext in image_extensions)
                
                attachment_info = f"{attachment.original_filename}: {file_url}"
                
                if is_image:
                    images_list.append(attachment_info)
                else:
                    files_list.append(attachment_info)
        
        images_text = '; '.join(images_list) if images_list else 'Nenhuma imagem'
        files_text = '; '.join(files_list) if files_list else 'Nenhum arquivo'
        
        writer.writerow([
            f'#{ticket.id:04d}',
            ticket.title,
            ticket.get_status_display(),
            ticket.get_priority_display() if hasattr(ticket, 'priority') else '',
            ticket.category.name if ticket.category else '',
            ticket.sector.name if ticket.sector else '',
            ticket.created_by.get_full_name() if ticket.created_by else '',
            ticket.created_by.email if ticket.created_by else '',
            ticket.assigned_to.get_full_name() if ticket.assigned_to else 'Não atribuído',
            ticket.created_at.strftime('%d/%m/%Y %H:%M'),
            ticket.updated_at.strftime('%d/%m/%Y %H:%M'),
            ticket.description,  # DESCRIÇÃO COMPLETA SEM CORTE
            images_text,
            files_text
        ])
    
    return response


def export_tickets_xlsx(tickets):
    """Exporta tickets para Excel (XLSX) com descrição completa e anexos"""
    from django.conf import settings
    
    # Criar workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Chamados"
    
    # Estilo do cabeçalho
    header_fill = PatternFill(start_color="1F4788", end_color="1F4788", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=12)
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    
    # Borda para células
    thin_border = Border(
        left=Side(style='thin', color='CCCCCC'),
        right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'),
        bottom=Side(style='thin', color='CCCCCC')
    )
    
    # Cabeçalhos expandidos
    headers = [
        'ID',
        'Título',
        'Status',
        'Prioridade',
        'Categoria',
        'Setor',
        'Solicitante',
        'Email Solicitante',
        'Responsável',
        'Data Criação',
        'Data Atualização',
        'Descrição Completa',
        'Imagens Anexadas',
        'Arquivos Anexados'
    ]
    
    # Aplicar cabeçalhos
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_alignment
        cell.border = thin_border
    
    # Ajustar largura das colunas
    column_widths = {
        1: 8,   # ID
        2: 35,  # Título
        3: 12,  # Status
        4: 12,  # Prioridade
        5: 20,  # Categoria
        6: 18,  # Setor
        7: 22,  # Solicitante
        8: 28,  # Email
        9: 22,  # Responsável
        10: 16, # Data Criação
        11: 16, # Data Atualização
        12: 60, # Descrição Completa
        13: 50, # Imagens
        14: 50  # Arquivos
    }
    
    for col, width in column_widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width
    
    # Congelar primeira linha (cabeçalho)
    ws.freeze_panes = 'A2'
    
    # Estilos para dados
    link_font = Font(color="0563C1", underline="single")
    data_alignment = Alignment(vertical="top", wrap_text=True)
    
    # Dados
    for row_num, ticket in enumerate(tickets.select_related('created_by', 'assigned_to', 'category', 'sector').prefetch_related('attachments'), start=2):
        # Coluna 1: ID
        cell = ws.cell(row=row_num, column=1, value=f'#{ticket.id:04d}')
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
        # Coluna 2: Título
        cell = ws.cell(row=row_num, column=2, value=ticket.title)
        cell.alignment = data_alignment
        cell.border = thin_border
        
        # Coluna 3: Status
        cell = ws.cell(row=row_num, column=3, value=ticket.get_status_display())
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
        # Coluna 4: Prioridade
        priority = ticket.get_priority_display() if hasattr(ticket, 'priority') else ''
        cell = ws.cell(row=row_num, column=4, value=priority)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
        # Coluna 5: Categoria
        cell = ws.cell(row=row_num, column=5, value=ticket.category.name if ticket.category else '')
        cell.alignment = data_alignment
        cell.border = thin_border
        
        # Coluna 6: Setor
        cell = ws.cell(row=row_num, column=6, value=ticket.sector.name if ticket.sector else '')
        cell.alignment = data_alignment
        cell.border = thin_border
        
        # Coluna 7: Solicitante
        cell = ws.cell(row=row_num, column=7, value=ticket.created_by.get_full_name() if ticket.created_by else '')
        cell.alignment = data_alignment
        cell.border = thin_border
        
        # Coluna 8: Email Solicitante
        cell = ws.cell(row=row_num, column=8, value=ticket.created_by.email if ticket.created_by else '')
        cell.alignment = data_alignment
        cell.border = thin_border
        
        # Coluna 9: Responsável
        cell = ws.cell(row=row_num, column=9, value=ticket.assigned_to.get_full_name() if ticket.assigned_to else 'Não atribuído')
        cell.alignment = data_alignment
        cell.border = thin_border
        
        # Coluna 10: Data Criação
        cell = ws.cell(row=row_num, column=10, value=ticket.created_at.strftime('%d/%m/%Y %H:%M'))
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
        # Coluna 11: Data Atualização
        cell = ws.cell(row=row_num, column=11, value=ticket.updated_at.strftime('%d/%m/%Y %H:%M'))
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
        # Coluna 12: Descrição Completa (SEM CORTE)
        cell = ws.cell(row=row_num, column=12, value=ticket.description)
        cell.alignment = data_alignment
        cell.border = thin_border
        
        # Colunas 13 e 14: Anexos (Imagens e Arquivos)
        attachments = ticket.attachments.all()
        
        if attachments.exists():
            # Separar imagens e outros arquivos
            images = []
            files = []
            
            for attachment in attachments:
                # Construir URL completo do anexo
                # Em produção com MinIO, MEDIA_URL já contém o endpoint completo
                # Ex: https://minio.example.com/bucket/ ou http://localhost:9000/bucket/
                if hasattr(settings, 'USE_S3') and settings.USE_S3:
                    # MinIO: MEDIA_URL já inclui endpoint + bucket, precisa adicionar /media/
                    file_url = f"{settings.MEDIA_URL.rstrip('/')}/media/{attachment.file.name}"
                elif hasattr(settings, 'MEDIA_URL') and settings.MEDIA_URL.startswith('http'):
                    # URL absoluta (CDN ou similar)
                    file_url = f"{settings.MEDIA_URL.rstrip('/')}/{attachment.file.name}"
                elif hasattr(settings, 'MEDIA_URL') and hasattr(settings, 'SITE_URL'):
                    # URL relativa + domínio do site
                    file_url = f"{settings.SITE_URL.rstrip('/')}{settings.MEDIA_URL.rstrip('/')}/{attachment.file.name}"
                else:
                    # Fallback para métodos padrão
                    file_url = attachment.file.url if hasattr(attachment.file, 'url') else attachment.file.name
                
                # Verificar se é imagem
                is_image = False
                if attachment.content_type:
                    is_image = attachment.content_type.startswith('image/')
                elif attachment.original_filename:
                    image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg']
                    is_image = any(attachment.original_filename.lower().endswith(ext) for ext in image_extensions)
                
                attachment_info = f"{attachment.original_filename}\n{file_url}"
                
                if is_image:
                    images.append(attachment_info)
                else:
                    files.append(attachment_info)
            
            # Coluna 13: Imagens
            images_text = '\n\n'.join(images) if images else 'Nenhuma imagem'
            cell = ws.cell(row=row_num, column=13, value=images_text)
            cell.alignment = data_alignment
            cell.border = thin_border
            if images:
                cell.font = link_font
            
            # Coluna 14: Arquivos
            files_text = '\n\n'.join(files) if files else 'Nenhum arquivo'
            cell = ws.cell(row=row_num, column=14, value=files_text)
            cell.alignment = data_alignment
            cell.border = thin_border
            if files:
                cell.font = link_font
        else:
            # Sem anexos
            cell = ws.cell(row=row_num, column=13, value='Nenhuma imagem')
            cell.alignment = data_alignment
            cell.border = thin_border
            cell.font = Font(color="999999", italic=True)
            
            cell = ws.cell(row=row_num, column=14, value='Nenhum arquivo')
            cell.alignment = data_alignment
            cell.border = thin_border
            cell.font = Font(color="999999", italic=True)
        
        # Ajustar altura da linha para acomodar conteúdo
        ws.row_dimensions[row_num].height = max(30, len(ticket.description) / 3)
    
    # Criar resposta HTTP
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="chamados_completo_{timezone.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
    
    # Salvar workbook na resposta
    wb.save(response)
    
    return response


@login_required
def tickets_export_view(request):
    """View dedicada para exportação de chamados com todos os filtros aplicados"""
    user = request.user
    
    # Coletar todos os filtros da URL (mesma lógica da tickets_list_view)
    search = request.GET.get('search', '')
    status_filter = request.GET.getlist('status')
    origem_filter = request.GET.get('origem', '')
    categoria_filter = request.GET.get('categoria', '')
    setor_filter = request.GET.get('setor', '')
    prioridade_filter = request.GET.get('prioridade', '')
    carteira_filter = request.GET.get('carteira', '')
    atribuidos_filter = request.GET.get('atribuidos', '')
    
    # Filtros avançados para SUPERADMIN
    created_by_filter = request.GET.get('created_by', '')
    created_by_sector_filter = request.GET.get('created_by_sector', '')
    assigned_to_filter = request.GET.get('assigned_to', '')
    date_from_filter = request.GET.get('date_from', '')
    date_to_filter = request.GET.get('date_to', '')
    user_hierarchy_filter = request.GET.get('user_hierarchy', '')
    has_attachments_filter = request.GET.get('has_attachments', '')
    has_comments_filter = request.GET.get('has_comments', '')
    overdue_filter = request.GET.get('overdue', '')
    
    # Filtro base
    base_filter = models.Q(created_by=user)
    
    # Filtrar tickets baseado na hierarquia do usuário (mesma lógica)
    if user.can_view_all_tickets():
        tickets = Ticket.objects.all()
    elif user.can_view_sector_tickets():
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        
        tickets = Ticket.objects.filter(
            base_filter |
            models.Q(sector__in=user_sectors) |
            models.Q(assigned_to=user) |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
        ).distinct()
    else:
        tickets = Ticket.objects.filter(
            base_filter |
            models.Q(assigned_to=user) |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
        ).exclude(status='FECHADO').distinct()
    
    # Aplicar filtros (copiado da tickets_list_view)
    
    # Filtro por origem
    if origem_filter == 'meus':
        tickets = tickets.filter(created_by=user)
    elif origem_filter == 'setor':
        user_sectors = list(user.sectors.all())
        if user.sector:
            user_sectors.append(user.sector)
        tickets = tickets.filter(sector__in=user_sectors).exclude(created_by=user)
    
    # Filtro por chamados atribuídos
    if atribuidos_filter == 'sim':
        tickets = tickets.filter(
            models.Q(assigned_to=user) |
            models.Q(additional_assignments__user=user, additional_assignments__is_active=True)
        ).distinct()
    
    # Filtro por status
    if status_filter:
        if 'abertos' in status_filter:
            tickets = tickets.filter(status='ABERTO')
        elif 'nao_resolvidos' in status_filter:
            tickets = tickets.exclude(status__in=['RESOLVIDO', 'FECHADO'])
        else:
            valid_statuses = [s for s in status_filter if s in ['ABERTO', 'EM_ANDAMENTO', 'RESOLVIDO', 'FECHADO']]
            if valid_statuses:
                tickets = tickets.filter(status__in=valid_statuses)
    
    # Filtro por categoria
    if categoria_filter:
        tickets = tickets.filter(category_id=categoria_filter)
    
    # Filtro por setor
    if setor_filter:
        if user.hierarchy == 'SUPERADMIN' or user.can_view_all_tickets():
            tickets = tickets.filter(sector_id=setor_filter)
        else:
            user_sectors = list(user.sectors.all())
            if user.sector:
                user_sectors.append(user.sector)
            sector_ids = [s.id for s in user_sectors] if user_sectors else []
            if int(setor_filter) in sector_ids:
                tickets = tickets.filter(sector_id=setor_filter)
    
    # Filtro por prioridade
    if prioridade_filter:
        tickets = tickets.filter(priority=prioridade_filter)
    
    # Filtro por carteira
    if carteira_filter:
        from communications.models import CommunicationGroup
        try:
            carteira_group = CommunicationGroup.objects.get(id=carteira_filter)
            carteira_users = carteira_group.members.filter(is_active=True)
            carteira_sectors = Sector.objects.filter(
                models.Q(users__in=carteira_users) |
                models.Q(primary_users__in=carteira_users)
            ).distinct()
            
            tickets = tickets.filter(
                models.Q(sector__in=carteira_sectors) |
                models.Q(assigned_to__in=carteira_users) |
                models.Q(additional_assignments__user__in=carteira_users, additional_assignments__is_active=True)
            ).distinct()
        except CommunicationGroup.DoesNotExist:
            pass
    
    # Filtro por pesquisa
    if search:
        tickets = tickets.filter(
            models.Q(title__icontains=search) |
            models.Q(description__icontains=search) |
            models.Q(id__icontains=search)
        )
    
    # Aplicar filtros avançados apenas para SUPERADMIN
    if user.hierarchy == 'SUPERADMIN':
        if created_by_filter:
            tickets = tickets.filter(created_by_id=created_by_filter)
        
        if created_by_sector_filter:
            tickets = tickets.filter(
                models.Q(created_by__sector_id=created_by_sector_filter) |
                models.Q(created_by__sectors__id=created_by_sector_filter)
            ).distinct()
        
        if assigned_to_filter:
            if assigned_to_filter == 'unassigned':
                tickets = tickets.filter(assigned_to__isnull=True)
            else:
                tickets = tickets.filter(assigned_to_id=assigned_to_filter)
        
        if date_from_filter:
            from django.utils.dateparse import parse_date
            date_from = parse_date(date_from_filter)
            if date_from:
                tickets = tickets.filter(created_at__date__gte=date_from)
        
        if date_to_filter:
            from django.utils.dateparse import parse_date
            date_to = parse_date(date_to_filter)
            if date_to:
                tickets = tickets.filter(created_at__date__lte=date_to)
        
        if user_hierarchy_filter:
            tickets = tickets.filter(created_by__hierarchy=user_hierarchy_filter)
        
        if has_attachments_filter == 'yes':
            tickets = tickets.filter(attachments__isnull=False).distinct()
        elif has_attachments_filter == 'no':
            tickets = tickets.filter(attachments__isnull=True)
        
        if has_comments_filter == 'yes':
            tickets = tickets.filter(comments__isnull=False).distinct()
        elif has_comments_filter == 'no':
            tickets = tickets.filter(comments__isnull=True)
        
        if overdue_filter:
            from django.utils import timezone
            now = timezone.now()
            if overdue_filter == 'yes':
                tickets = tickets.filter(
                    models.Q(due_date__lt=now) & 
                    ~models.Q(status__in=['RESOLVIDO', 'FECHADO'])
                )
            elif overdue_filter == 'no':
                tickets = tickets.filter(
                    models.Q(due_date__gte=now) | 
                    models.Q(status__in=['RESOLVIDO', 'FECHADO'])
                )
    
    # Ordenar
    tickets = tickets.order_by('-created_at')
    
    # Verificar formato de exportação (padrão: Excel)
    export_format = request.GET.get('export', 'excel')
    
    if export_format == 'csv':
        return export_tickets_csv(tickets)
    else:
        return export_tickets_xlsx(tickets)
