"""API do app mobile — /api/mobile/v1/.

Reaproveita as regras do portal (quem vê qual chamado, quem comenta, quais
comunicados chegam a quem, bloqueios de férias/jornada/curso/trilha) em vez de
reescrevê-las. Cada endpoint diz de onde veio a regra.
"""
import logging

from django.contrib.auth import authenticate
from django.core.paginator import EmptyPage, Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import (api_view, authentication_classes, parser_classes,
                                       permission_classes, throttle_classes)
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle

from core.middleware import log_action

from . import dados
from .autenticacao import TokenMobileAuthentication
from .models import TokenMobile
from .permissoes import SemBloqueio, bloqueio_do_usuario

logger = logging.getLogger(__name__)

TAMANHO_PAGINA = 20
MAX_ANEXO = 50 * 1024 * 1024
MAX_ANEXOS = 5


class TentativasDeLogin(SimpleRateThrottle):
    """No máximo 10 tentativas de login por minuto por IP (o portal web não tem trava nenhuma)."""
    rate = '10/min'
    scope = 'mobile_login'

    def get_cache_key(self, request, view):
        return self.cache_format % {'scope': self.scope, 'ident': self.get_ident(request)}


def _pagina(request, queryset):
    try:
        numero = max(int(request.query_params.get('pagina') or 1), 1)
    except ValueError:
        numero = 1
    paginador = Paginator(queryset, TAMANHO_PAGINA)
    try:
        pagina = paginador.page(numero)
    except EmptyPage:
        return [], {'pagina': numero, 'total': paginador.count, 'tem_mais': False}
    return list(pagina.object_list), {'pagina': numero, 'total': paginador.count,
                                      'tem_mais': pagina.has_next()}


def _erro(mensagem, codigo=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({'erro': mensagem, **extra}, status=codigo)


# ── Sessão ────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([TentativasDeLogin])
def login(request):
    """E-mail + senha, como no portal (users.views.login_view). Devolve o token do aparelho."""
    email = (request.data.get('email') or '').strip()
    senha = request.data.get('senha') or ''
    if not email or not senha:
        return _erro('Informe e-mail e senha.')
    user = authenticate(request._request, username=email, password=senha)
    if user is None or not user.is_active:
        return _erro('E-mail ou senha inválidos.', status.HTTP_401_UNAUTHORIZED)
    _, token = TokenMobile.emitir(
        user, dispositivo=request.data.get('dispositivo') or '',
        plataforma=request.data.get('plataforma') or '', versao_app=request.data.get('versao_app') or '')
    log_action(user, 'USER_LOGIN', 'Login no app mobile', request)
    return Response({'token': token, 'usuario': dados.pessoa(request, user, completo=True),
                     'bloqueio': bloqueio_do_usuario(user, usar_cache=False)})


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([IsAuthenticated])
def logout(request):
    request.auth.revogar()
    return Response({'ok': True})


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([IsAuthenticated])
def eu(request):
    """Perfil + bloqueio atual. Responde mesmo bloqueado: é por aqui que o app sabe o motivo."""
    user = request.user
    return Response({'usuario': dados.pessoa(request, user, completo=True),
                     'bloqueio': bloqueio_do_usuario(user, usar_cache=request.query_params.get('atualizar') != '1'),
                     'notificacoes_nao_lidas': _nao_lidas(user)})


# ── Início ────────────────────────────────────────────────────────────────────

def chamados_visiveis(user):
    """A base da lista de chamados da web (tickets.views.tickets_list_view), sem os filtros da tela."""
    from tickets.models import Ticket
    from tickets.permissions import chamados_restritos, filtro_do_padrao_restrito, setores_do_usuario

    if user.can_view_all_tickets():
        return Ticket.objects.all()
    atendo = Q(assigned_to=user) | Q(additional_assignments__user=user, additional_assignments__is_active=True)
    if chamados_restritos(user):
        return Ticket.objects.filter(filtro_do_padrao_restrito(user)).distinct()
    if user.can_view_sector_tickets():
        return Ticket.objects.filter(Q(created_by=user) | Q(sector_id__in=setores_do_usuario(user)) | atendo).distinct()
    return Ticket.objects.filter(Q(created_by=user) | atendo).distinct()


def comunicados_visiveis(user):
    """Os da home do portal (communications.views.home_feed): para todos ou para a pessoa."""
    from communications.models import Communication
    return (Communication.objects.filter(Q(send_to_all=True) | Q(recipients=user)).distinct()
            .select_related('sender', 'sender__sector').prefetch_related('images', 'liked_by'))


def _marcas_de_comunicados(user, comunicados):
    from communications.models import CommunicationRead
    ids = [c.pk for c in comunicados]
    lidos = dict(CommunicationRead.objects.filter(user=user, communication_id__in=ids)
                 .values_list('communication_id', 'status'))
    curtidos = set(user.liked_communications.filter(id__in=ids).values_list('id', flat=True))
    return lidos, curtidos


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def inicio(request):
    from communications.popup_checkers import comunicados_pendentes

    user = request.user
    meus = chamados_visiveis(user)
    contagem = meus.aggregate(
        abertos_por_mim=Count('id', filter=Q(created_by=user, status__in=dados.STATUS_EM_ABERTO), distinct=True),
        comigo=Count('id', filter=(Q(assigned_to=user) | Q(additional_assignments__user=user,
                                                           additional_assignments__is_active=True))
                     & Q(status__in=dados.STATUS_EM_ABERTO), distinct=True),
        aguardando_minha_aprovacao=Count('id', filter=Q(created_by=user, status='RESOLVIDO'), distinct=True),
        atrasados=Count('id', filter=Q(status__in=dados.STATUS_EM_ABERTO, due_date__lt=timezone.now()), distinct=True),
    )
    recentes = list(meus.filter(Q(created_by=user) | Q(assigned_to=user))
                    .select_related('sector', 'category', 'created_by', 'assigned_to',
                                    'created_by__sector', 'assigned_to__sector')
                    .order_by('-updated_at')[:5])
    base = comunicados_visiveis(user).order_by('-created_at')
    fixados = list(base.filter(is_pinned=True)[:3])
    novos = list(base.filter(is_pinned=False)[:6])
    # Elogios recentes: os mesmos 2 da home do portal (communications.views.home_feed).
    try:
        from compliments.models import Compliment
        elogios = list(Compliment.objects.filter(is_active=True)
                       .select_related('from_user', 'from_user__sector', 'to_user', 'to_user__sector', 'to_sector')
                       .order_by('-created_at')[:2])
    except Exception:  # noqa: BLE001 — sem o app de elogios a home segue
        elogios = []
    lidos, curtidos = _marcas_de_comunicados(user, fixados + novos)
    pendentes = list(comunicados_pendentes(user).values('id', 'title')[:5])
    return Response({
        'usuario': dados.pessoa(request, user, completo=True),
        'chamados': contagem,
        'chamados_recentes': [dados.chamado_resumo(request, t) for t in recentes],
        'comunicados_fixados': [dados.comunicado_resumo(request, c, lidos, curtidos) for c in fixados],
        'comunicados': [dados.comunicado_resumo(request, c, lidos, curtidos) for c in novos],
        'elogios': [dados.elogio(request, e) for e in elogios],
        # Saldo C$ no cartão do perfil: a home do portal esconde de PADRÃO, ADMINISTRATIVO e SUPERVISOR.
        'saldo_cs': (str(user.balance_cs) if user.hierarchy not in ('PADRAO', 'ADMINISTRATIVO', 'SUPERVISOR')
                     and getattr(user, 'balance_cs', None) is not None else None),
        'pode_criar_comunicado': bool(user.can_manage_users()),
        'comunicados_obrigatorios': [{'id': c['id'], 'titulo': c['title']} for c in pendentes],
        'notificacoes_nao_lidas': _nao_lidas(user),
    })


# ── Chamados ──────────────────────────────────────────────────────────────────

def _pode_comentar(user, ticket):
    """A mesma pergunta de tickets.views.add_comment_view."""
    from tickets.permissions import setores_pelo_setor
    return bool(
        user.can_view_all_tickets()
        or (user.can_view_sector_tickets() and ticket.sector_id == user.sector_id
            and ticket.sector_id in setores_pelo_setor(user))
        or ticket.created_by_id == user.id
        or user in ticket.get_all_assigned_users())


@api_view(['GET', 'POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
@parser_classes([JSONParser, MultiPartParser, FormParser])
def chamados(request):
    if request.method == 'POST':
        return _abrir_chamado(request)
    user = request.user
    qs = (chamados_visiveis(user)
          .select_related('sector', 'category', 'created_by', 'assigned_to',
                          'created_by__sector', 'assigned_to__sector'))
    escopo = request.query_params.get('escopo') or 'todos'
    if escopo == 'meus':
        qs = qs.filter(created_by=user)
    elif escopo == 'comigo':
        qs = qs.filter(Q(assigned_to=user) | Q(additional_assignments__user=user,
                                                additional_assignments__is_active=True))
    situacao = request.query_params.get('situacao') or 'abertos'
    if situacao == 'abertos':
        qs = qs.filter(status__in=dados.STATUS_EM_ABERTO)
    elif situacao == 'concluidos':
        qs = qs.filter(status__in=dados.STATUS_CONCLUIDOS)
    busca = (request.query_params.get('busca') or '').strip()
    if busca:
        filtro = Q(title__icontains=busca) | Q(description__icontains=busca)
        if busca.lstrip('#').isdigit():
            filtro |= Q(pk=int(busca.lstrip('#')))
        qs = qs.filter(filtro)
    itens, pagina = _pagina(request, qs.order_by('-updated_at', '-id'))
    return Response({'resultados': [dados.chamado_resumo(request, t) for t in itens], **pagina})


def _abrir_chamado(request):
    """As regras de tickets.views.ticket_create_fixed_view (sem o 'para uma pessoa' por enquanto)."""
    from tickets.models import Category, Ticket, TicketAttachment, TicketLog
    from users.models import Sector

    user = request.user
    titulo = (request.data.get('titulo') or '').strip()
    descricao = (request.data.get('descricao') or '').strip()
    prioridade = request.data.get('prioridade') or 'MEDIA'
    local = (request.data.get('local') or '').strip() or None
    erros = {}
    if not titulo:
        erros['titulo'] = 'Dê um título ao chamado.'
    elif len(titulo) > 200:
        erros['titulo'] = 'Use no máximo 200 caracteres.'
    if not descricao:
        erros['descricao'] = 'Descreva o que precisa.'
    setor = Sector.objects.filter(pk=request.data.get('setor') or 0).first()
    if setor is None:
        erros['setor'] = 'Escolha o setor.'
    categoria = (Category.objects.filter(pk=request.data.get('categoria') or 0, sector=setor, is_active=True).first()
                 if setor else None)
    if setor and categoria is None:
        erros['categoria'] = 'Escolha a categoria.'
    if setor and setor.name.strip().lower() in ('manutenção', 'manutencao') and not local:
        erros['local'] = 'Informe a loja/local para a Manutenção.'
    if prioridade not in dict(Ticket.PRIORITY_CHOICES):
        erros['prioridade'] = 'Prioridade inválida.'
    arquivos = request.FILES.getlist('anexos')
    if len(arquivos) > MAX_ANEXOS:
        erros['anexos'] = f'Envie no máximo {MAX_ANEXOS} arquivos.'
    elif any(a.size > MAX_ANEXO for a in arquivos):
        erros['anexos'] = 'Cada arquivo pode ter até 50 MB.'
    if erros:
        return _erro('Confira os campos destacados.', campos=erros)

    with transaction.atomic():
        ticket = Ticket.objects.create(
            title=titulo, description=descricao, sector=setor, category=categoria, created_by=user,
            requires_approval=bool(categoria.requires_approval),
            solution_time_hours=categoria.default_solution_time_hours or 24,
            priority=prioridade, store_location=local)
        for arquivo in arquivos:
            TicketAttachment.objects.create(
                ticket=ticket, file=arquivo, original_filename=arquivo.name, file_size=arquivo.size,
                content_type=getattr(arquivo, 'content_type', '') or '', uploaded_by=user)
        TicketLog.objects.create(ticket=ticket, user=user, new_status='ABERTO', observation='Chamado criado')
    log_action(user, 'TICKET_CREATE', f'Chamado criado pelo app: #{ticket.id} - {ticket.title}', request)
    ticket = Ticket.objects.select_related('sector', 'category', 'created_by', 'assigned_to').get(pk=ticket.pk)
    return Response(dados.chamado_resumo(request, ticket), status=status.HTTP_201_CREATED)


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def chamado(request, pk):
    from tickets.models import Ticket
    from tickets.permissions import pode_ver_chamado

    ticket = get_object_or_404(
        Ticket.objects.select_related('sector', 'category', 'created_by', 'assigned_to',
                                      'created_by__sector', 'assigned_to__sector'), pk=pk)
    if not pode_ver_chamado(request.user, ticket):
        return _erro('Você não tem acesso a este chamado.', status.HTTP_404_NOT_FOUND)
    comentarios = (ticket.comments.filter(excluido_em__isnull=True)
                   .select_related('user', 'user__sector').order_by('created_at'))
    anexos = ticket.attachments.select_related('uploaded_by').order_by('uploaded_at')
    return Response({
        **dados.chamado_resumo(request, ticket),
        'descricao': ticket.description,
        'solucao': ticket.solution or None,
        'local': ticket.store_location or None,
        'auxiliares': [dados.pessoa(request, u) for u in ticket.get_all_assigned_users()
                       if u and u.pk != ticket.assigned_to_id],
        'tags': [{'nome': t.nome, 'cor': getattr(t, 'cor', None)} for t in ticket.tags.all()],
        'comentarios': [{
            'id': c.pk, 'texto': c.comment, 'tipo': c.comment_type, 'tipo_nome': c.get_comment_type_display(),
            'autor': dados.pessoa(request, c.user), 'criado_em': dados.iso(c.created_at),
            'sou_eu': c.user_id == request.user.id,
        } for c in comentarios],
        'anexos': [{
            'id': a.pk, 'nome': a.original_filename, 'tamanho': a.file_size,
            'tipo': a.content_type, 'url': dados.url_absoluta(request, a.file),
            'enviado_em': dados.iso(a.uploaded_at),
        } for a in anexos],
        'pode_comentar': _pode_comentar(request.user, ticket),
        'url_web': f'/tickets/{ticket.pk}/',
    })


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def chamado_comentar(request, pk):
    from tickets.models import Ticket, TicketComment

    ticket = get_object_or_404(Ticket, pk=pk)
    if not _pode_comentar(request.user, ticket):
        return _erro('Você não pode comentar neste chamado.', status.HTTP_403_FORBIDDEN)
    texto = (request.data.get('texto') or '').strip()
    if not texto:
        return _erro('Escreva o comentário.')
    comentario = TicketComment.objects.create(ticket=ticket, user=request.user, comment=texto,
                                              comment_type='COMMENT')
    log_action(request.user, 'TICKET_COMMENT', f'Comentário adicionado ao chamado #{ticket.id} (app)', request)
    return Response({'id': comentario.pk, 'texto': comentario.comment, 'tipo': 'COMMENT',
                     'tipo_nome': comentario.get_comment_type_display(),
                     'autor': dados.pessoa(request, request.user), 'criado_em': dados.iso(comentario.created_at),
                     'sou_eu': True}, status=status.HTTP_201_CREATED)


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def setores(request):
    """Setores que recebem chamado (os que têm categoria ativa)."""
    from users.models import Sector
    lista = (Sector.objects.filter(category__is_active=True).distinct().order_by('name')
             .values('id', 'name'))
    return Response([{'id': s['id'], 'nome': s['name']} for s in lista])


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def categorias(request):
    """O mesmo de /tickets/api/categories-by-sector/."""
    from tickets.models import Category
    lista = Category.objects.filter(sector_id=request.query_params.get('setor') or 0, is_active=True).order_by('name')
    return Response([{'id': c.pk, 'nome': c.name, 'descricao_padrao': c.default_description or '',
                      'horas_solucao': c.default_solution_time_hours} for c in lista])


# ── Comunicados ───────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def comunicados(request):
    qs = comunicados_visiveis(request.user)
    if request.query_params.get('fixados') == 'nao':   # o "Feed de Comunicados" da home (sem os fixados)
        qs = qs.filter(is_pinned=False)
    itens, pagina = _pagina(request, qs.order_by('-is_pinned', '-created_at'))
    lidos, curtidos = _marcas_de_comunicados(request.user, itens)
    return Response({'resultados': [dados.comunicado_resumo(request, c, lidos, curtidos) for c in itens], **pagina})


def _comunicado_visivel(user, pk):
    return get_object_or_404(comunicados_visiveis(user), pk=pk)


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def comunicado(request, pk):
    from communications.models import CommunicationRead

    c = _comunicado_visivel(request.user, pk)
    # Abrir conta como visualizado, como no portal (communication_detail_view).
    CommunicationRead.objects.get_or_create(communication=c, user=request.user)
    c.viewed_by.add(request.user)
    lidos, curtidos = _marcas_de_comunicados(request.user, [c])
    imagens = [dados.url_absoluta(request, c.image)] if c.image else []
    imagens += [dados.url_absoluta(request, i.image) for i in c.images.all().order_by('order') if i.e_imagem]
    return Response({
        **dados.comunicado_resumo(request, c, lidos, curtidos),
        'texto': dados.texto_do_html(c.message),
        'imagens': [i for i in imagens if i],
    })


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def comunicado_situacao(request, pk):
    """'Estou ciente' / 'Estou com dúvida' (communications.views.update_communication_status)."""
    from communications.models import CommunicationRead

    c = _comunicado_visivel(request.user, pk)
    situacao = request.data.get('situacao')
    if situacao not in ('ESTOU_CIENTE', 'ESTOU_COM_DUVIDA'):
        return _erro('Situação inválida.')
    leitura, _ = CommunicationRead.objects.get_or_create(communication=c, user=request.user)
    leitura.status = situacao
    leitura.save()
    return Response({'situacao': situacao})


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def comunicado_curtir(request, pk):
    c = _comunicado_visivel(request.user, pk)
    if c.liked_by.filter(pk=request.user.pk).exists():
        c.liked_by.remove(request.user)
        curti = False
    else:
        c.liked_by.add(request.user)
        curti = True
    return Response({'curti': curti, 'curtidas': c.liked_by.count()})


# ── Notificações ──────────────────────────────────────────────────────────────
# Há dois sistemas no portal: o do sino (notifications.UserNotification) e o
# antigo core.Notification (cursos, documentos, caixa). O app junta os dois; o id
# leva o prefixo da origem ("p-" sino, "c-" antigo).

def _nao_lidas(user):
    from core.models import Notification
    from notifications.models import UserNotification
    return (UserNotification.objects.filter(user=user, is_read=False).count()
            + Notification.objects.filter(user=user, is_read=False).count())


@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def notificacoes(request):
    from core.models import Notification
    from notifications.models import UserNotification

    user = request.user
    itens = []
    for un in (UserNotification.objects.filter(user=user).select_related('notification')
               .order_by('-created_at')[:50]):
        n = un.notification
        itens.append({'id': f'p-{un.pk}', 'titulo': n.title, 'mensagem': dados.texto_do_html(n.message, 400),
                      'tipo': n.notification_type, 'lida': un.is_read, 'criada_em': dados.iso(un.created_at),
                      'link': n.action_url or None})
    for n in Notification.objects.filter(user=user).order_by('-created_at')[:50]:
        itens.append({'id': f'c-{n.pk}', 'titulo': n.title, 'mensagem': dados.texto_do_html(n.message, 400),
                      'tipo': n.notification_type, 'lida': n.is_read, 'criada_em': dados.iso(n.created_at),
                      'link': n.related_url or None})
    itens.sort(key=lambda i: i['criada_em'] or '', reverse=True)
    return Response({'resultados': itens[:50], 'nao_lidas': _nao_lidas(user)})


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def notificacao_lida(request, ident):
    from core.models import Notification
    from notifications.models import UserNotification

    origem, _, numero = ident.partition('-')
    if not numero.isdigit() or origem not in ('p', 'c'):
        return _erro('Notificação inválida.', status.HTTP_404_NOT_FOUND)
    modelo = UserNotification if origem == 'p' else Notification
    item = get_object_or_404(modelo, pk=int(numero), user=request.user)
    if not item.is_read:
        item.mark_as_read()
    return Response({'ok': True, 'nao_lidas': _nao_lidas(request.user)})


@api_view(['POST'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def notificacoes_lidas(request):
    from core.models import Notification
    from notifications.models import UserNotification

    agora = timezone.now()
    UserNotification.objects.filter(user=request.user, is_read=False).update(is_read=True, read_at=agora)
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True, read_at=agora)
    return Response({'ok': True, 'nao_lidas': 0})


# ── Módulos ───────────────────────────────────────────────────────────────────

@api_view(['GET'])
@authentication_classes([TokenMobileAuthentication])
@permission_classes([SemBloqueio])
def modulos(request):
    """Os módulos que a pessoa pode abrir — cada um pela regra do próprio app (api_mobile/modulos)."""
    from .modulos import modulos_do_usuario
    return Response(modulos_do_usuario(request.user))
