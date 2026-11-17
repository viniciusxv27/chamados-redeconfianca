"""Módulo Agenda no app: minha agenda (dia/semana), convites e transcrições de reunião.

As regras são as do portal (agenda/views.py), chamadas daqui — nada reescrito:

- Entrada: todas as telas da agenda só têm ``@login_required`` (calendar_view, agenda/views.py:702;
  transcription_list, :1380) e o item do menu aparece para todo logado (templates/base.html:838, sem
  ``{% if %}``). A trava "logado, ativo e sem bloqueio do portal" vem do ``permissao('agenda')``.
- Minha agenda: a mesma consulta de ``api_events`` sem ``user_id`` (agenda/views.py:851): eventos que
  são meus ou em que sou participante.
- Detalhe do evento: a regra de ``api_event_detail`` (agenda/views.py:886) — dono, quem vê a agenda
  completa do dono (``_can_view_full_calendar``) ou convidado — mais quem já está em ``participants``
  (essa pessoa já vê o evento na lista da web). Fora disso → 404.
- Criar, editar, excluir, responder convite e responder solicitação de reunião: a própria view do
  portal é chamada (``_chamar_view_json`` / ``_chamar_view_form``): validação, avisos (sino + push),
  sala de vídeo da Chamada, tarefa do tipo Tarefa e a repetição semanal são os da web.
- Transcrições: ``_visible_transcriptions_for_user`` (agenda/views.py:151); atribuir responsável e
  importar para o Impulso chamam ``api_transcription_assign_task`` / ``api_transcription_task_impulso``.

Fica no portal: gravar/enviar áudio (o app não tem pacote de áudio), reprocessar (chama a IA paga),
compartilhar transcrição, ver a agenda de outra pessoa, disponibilidade e pedir reunião.
"""
import json
from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import Http404, HttpRequest, QueryDict
from django.shortcuts import get_object_or_404
from django.urls import path
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, parser_classes, permission_classes
from rest_framework.parsers import FormParser, JSONParser
from rest_framework.response import Response

from ..autenticacao import TokenMobileAuthentication
from . import agenda_dados as fmt
from . import permissao

User = get_user_model()


def pode_entrar(user):
    # agenda/views.py:702 (calendar_view) e :1380 (transcription_list): só @login_required;
    # o menu (templates/base.html:838) aparece para todo logado.
    return bool(user and user.is_authenticated and user.is_active)


MODULO = {'rotulo': 'Agenda', 'descricao': 'Compromissos, convites e atas de reunião',
          'icone': 'calendar-outline', 'cor': '#0EA5E9', 'pode_entrar': pode_entrar}

# A lista de eventos vai em pedaços: uma semana por vez no app; no máximo ~2 meses por pedido.
MAX_DIAS_POR_PEDIDO = 62
TAMANHO_PAGINA = 20


def _api(metodos=('GET',)):
    def decorar(funcao):
        funcao = parser_classes([JSONParser, FormParser])(funcao)
        funcao = permission_classes([permissao('agenda')])(funcao)
        funcao = authentication_classes([TokenMobileAuthentication])(funcao)
        return api_view(list(metodos))(funcao)
    return decorar


def _erro(mensagem, codigo=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({'erro': mensagem, **extra}, status=codigo)


def _web():
    from agenda import views as web
    return web


# ── Ponte com as views do portal ─────────────────────────────────────────────

def _chamar_view_json(request, view, *args, corpo=None):
    """Roda uma view JSON da agenda (as que o calendário chama por fetch) com a pessoa do token.

    A view lê ``request.body``; a requisição do DRF já pode ter consumido o corpo, então ela recebe
    uma requisição nova, só com o JSON montado aqui. Devolve (status, conteúdo).
    """
    dj = request._request
    nova = HttpRequest()
    nova.method = 'POST'
    nova.path = dj.path
    nova.path_info = dj.path_info
    nova.META = {**dj.META, 'REQUEST_METHOD': 'POST', 'CONTENT_TYPE': 'application/json'}
    nova._body = json.dumps(corpo or {}).encode('utf-8')
    nova.user = request.user
    if hasattr(dj, 'session'):
        nova.session = dj.session
    resposta = view(nova, *args)
    try:
        conteudo = json.loads(resposta.content or b'{}')
    except ValueError:
        conteudo = {}
    return resposta.status_code, conteudo if isinstance(conteudo, dict) else {}


def _chamar_view_form(request, view, *args, post=None):
    """Roda uma view de formulário (POST + mensagem + redirect) e devolve [(nível, texto)].

    As mensagens que a view deixaria na tela são lidas (e consumidas) aqui: nada fica na sessão.
    """
    dj = request._request
    dj.user = request.user
    dj.method = 'POST'
    corpo = QueryDict(mutable=True)
    for chave, valor in (post or {}).items():
        corpo[chave] = '' if valor is None else str(valor)
    dj.POST = corpo
    view(dj, *args)
    return [(m.level_tag, str(m)) for m in messages.get_messages(dj)]


def _texto(valor, limite=None):
    texto = valor.strip() if isinstance(valor, str) else ''
    return texto[:limite] if limite else texto


def _sim(valor):
    if isinstance(valor, bool):
        return valor
    return str(valor or '').strip().lower() in ('1', 'true', 'sim', 'on')


def _ler_data_hora(valor):
    """ISO 8601 (com ou sem fuso) → datetime com fuso. Sem fuso vale a hora de Brasília."""
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        dt = datetime.fromisoformat(valor.strip().replace('Z', '+00:00'))
    except ValueError:
        return None
    return timezone.make_aware(dt) if timezone.is_naive(dt) else dt


def _ler_data(valor):
    if not isinstance(valor, str) or not valor.strip():
        return None
    try:
        return date.fromisoformat(valor.strip()[:10])
    except ValueError:
        return None


# ── Minha agenda ─────────────────────────────────────────────────────────────

def _meus_eventos(user, inicio, fim):
    # A consulta de agenda/views.py api_events (linha 851): meus eventos + onde sou participante.
    from agenda.models import CalendarEvent
    return (CalendarEvent.objects
            .filter(Q(owner=user) | Q(participants=user), start__lt=fim, end__gt=inicio)
            .distinct().select_related('owner').order_by('start', 'end', 'pk'))


def _pendencias(user):
    from agenda.models import EventParticipant, MeetingRequest
    # Os contadores do topo de agenda/views.py calendar_view (linha 705).
    return {
        'convites_pendentes': EventParticipant.objects.filter(user=user, status='pending').count(),
        'solicitacoes_pendentes': MeetingRequest.objects.filter(target=user, status='pending').count(),
    }


@_api()
def eventos(request):
    """Eventos de ``inicio`` a ``fim`` (datas, inclusive). Sem datas: a semana de hoje (segunda a domingo)."""
    hoje = timezone.localdate()
    inicio = _ler_data(request.query_params.get('inicio')) or (hoje - timedelta(days=hoje.weekday()))
    fim = _ler_data(request.query_params.get('fim')) or (inicio + timedelta(days=6))
    if fim < inicio:
        return _erro('O fim do período vem antes do início.')
    if (fim - inicio).days >= MAX_DIAS_POR_PEDIDO:
        return _erro(f'Peça no máximo {MAX_DIAS_POR_PEDIDO} dias por vez.')
    de = timezone.make_aware(datetime.combine(inicio, time.min))
    ate = timezone.make_aware(datetime.combine(fim + timedelta(days=1), time.min))
    user = request.user
    return Response({
        'inicio': inicio.isoformat(),
        'fim': fim.isoformat(),
        'hoje': hoje.isoformat(),
        'eventos': [fmt.evento(request, ev, user) for ev in _meus_eventos(user, de, ate)],
        **_pendencias(user),
        'url_web': '/agenda/',
    })


def _evento_visivel(user, pk):
    """O evento, se esta pessoa pode abri-lo no portal; senão 404 (não contamos que o id existe)."""
    from agenda.models import CalendarEvent, EventParticipant
    web = _web()

    ev = CalendarEvent.objects.select_related('owner').filter(pk=pk).first()
    if ev is None:
        raise Http404
    # agenda/views.py:886-889 (api_event_detail): dono, agenda completa do dono ou convidado.
    pode = (ev.owner_id == user.pk or web._can_view_full_calendar(user, ev.owner)
            or EventParticipant.objects.filter(event=ev, user=user).exists()
            # Quem está em `participants` (solicitação de reunião aceita) já vê o evento na lista da web.
            or ev.participants.filter(pk=user.pk).exists())
    if not pode:
        raise Http404
    return ev


def _detalhe_evento(request, ev):
    web = _web()
    user = request.user

    participantes = list(ev.event_participants.select_related('user', 'user__sector').order_by('user__first_name'))
    meu = next((p for p in participantes if p.user_id == user.pk), None)
    transcricoes = (web._visible_transcriptions_for_user(user).filter(event=ev)
                    .order_by('-created_at').values('id', 'title', 'status')[:5])
    sou_dono = ev.owner_id == user.pk
    return {
        **fmt.evento(request, ev, user),
        'descricao': ev.description or None,
        'dono_pessoa': fmt.pessoa_curta(ev.owner),
        'participantes': [fmt.participante(p) for p in participantes],
        'recorrencia_ate': ev.recurrence_until.isoformat() if ev.recurrence_until else None,
        'recorrencia_pai': ev.recurrence_parent_id,
        'meu_convite': ({'id': meu.pk, 'status': meu.status, 'status_nome': meu.get_status_display()}
                        if meu else None),
        'pode_responder': bool(meu and meu.status == 'pending'),   # api_event_invitation_respond: status='pending'
        'pode_editar': sou_dono,          # api_event_update/api_event_delete: owner=request.user
        'tarefa_id': ev.tarefa_id,
        'transcricoes': [{'id': t['id'], 'titulo': t['title'],
                          'status_nome': fmt.STATUS_TRANSCRICAO.get(t['status'], t['status'])} for t in transcricoes],
        'url_web': '/agenda/',
    }


@_api()
def evento(request, pk):
    return Response(_detalhe_evento(request, _evento_visivel(request.user, pk)))


def _corpo_do_evento(request, criando):
    """O que o app manda → o JSON que o calendário do portal manda (templates/agenda/calendar.html saveEvent)."""
    from agenda.models import CalendarEvent

    d = request.data if isinstance(request.data, dict) else {}
    titulo = _texto(d.get('titulo'))
    if not titulo:
        return None, 'Título é obrigatório'
    if len(titulo) > 255:
        return None, 'O título passa de 255 letras.'
    inicio, fim = _ler_data_hora(d.get('inicio')), _ler_data_hora(d.get('fim'))
    if not inicio or not fim:
        return None, 'Datas inválidas'
    # A mesma checagem de api_event_create (agenda/views.py:942); na edição evita gravar fim antes do início.
    if fim <= inicio:
        return None, 'A data de fim deve ser após a de início'
    local, link = _texto(d.get('local')), _texto(d.get('link'))
    if len(local) > 255 or len(link) > 500:
        return None, 'Local ou link longo demais.'
    tipos = dict(CalendarEvent.TYPE_CHOICES)
    cores = dict(CalendarEvent.COLOR_CHOICES)
    tipo = d.get('tipo') if d.get('tipo') in tipos else 'event'
    cor = d.get('cor') if d.get('cor') in cores else '#4f46e5'
    participantes = d.get('participantes') or []
    if not isinstance(participantes, list):
        return None, 'Participantes inválidos.'
    try:
        participantes = sorted({int(x) for x in participantes})
    except (TypeError, ValueError):
        return None, 'Participantes inválidos.'

    corpo = {
        'title': titulo,
        'description': _texto(d.get('descricao')),
        'start': inicio.isoformat(),
        'end': fim.isoformat(),
        'event_type': tipo,
        'color': cor,
        'all_day': _sim(d.get('dia_inteiro')),
        'location': local,
        'link': link,
        'is_private': _sim(d.get('privado')),
        'participants': participantes,
    }
    if not criando and 'link' not in d:
        # Edição sem link no pedido: o portal mantém o que está (a sala da Chamada é dele).
        corpo.pop('link')
    if criando:
        # Repetição só ao criar, como no portal (calendar.html: "Disable recurrence edit on existing events").
        semanal = _sim(d.get('repetir_semanal'))
        corpo['recurrence'] = 'weekly' if semanal else 'none'
        if semanal and d.get('repetir_ate'):
            ate = _ler_data(d.get('repetir_ate'))
            if ate is None:
                return None, 'Data final da repetição inválida.'
            if ate < inicio.date():
                return None, 'A repetição precisa terminar depois do primeiro dia.'
            corpo['recurrence_until'] = ate.isoformat()
    return corpo, None


@_api(('POST',))
def evento_criar(request):
    """Cria pelo ``api_event_create`` do portal (avisos, convites, sala da Chamada, tarefa, repetição)."""
    from agenda.models import CalendarEvent
    web = _web()

    corpo, erro = _corpo_do_evento(request, criando=True)
    if erro:
        return _erro(erro)
    codigo, conteudo = _chamar_view_json(request, web.api_event_create, corpo=corpo)
    if codigo != 201 or not conteudo.get('id'):
        return _erro(conteudo.get('error') or 'Não foi possível marcar o evento.', codigo if codigo >= 400 else 400)
    ev = CalendarEvent.objects.select_related('owner').get(pk=conteudo['id'])
    return Response({'evento': _detalhe_evento(request, ev), 'sala': conteudo.get('sala'),
                     'mensagem': 'Chamada marcada e sala criada.' if conteudo.get('sala') else 'Evento marcado.'},
                    status=status.HTTP_201_CREATED)


@_api(('POST',))
def evento_editar(request, pk):
    """Edita pelo ``api_event_update`` do portal (só o dono: agenda/views.py:1015)."""
    from agenda.models import CalendarEvent
    web = _web()

    ev = get_object_or_404(CalendarEvent, pk=pk, owner=request.user)
    corpo, erro = _corpo_do_evento(request, criando=False)
    if erro:
        return _erro(erro)
    codigo, conteudo = _chamar_view_json(request, web.api_event_update, ev.pk, corpo=corpo)
    if codigo != 200 or not conteudo.get('ok'):
        return _erro(conteudo.get('error') or 'Não foi possível salvar o evento.', codigo if codigo >= 400 else 400)
    ev.refresh_from_db()
    return Response({'evento': _detalhe_evento(request, ev), 'sala': conteudo.get('sala'), 'mensagem': 'Evento atualizado.'})


@_api(('POST',))
def evento_excluir(request, pk):
    """Exclui pelo ``api_event_delete`` do portal (só o dono). ``serie``: a série semanal inteira."""
    from agenda.models import CalendarEvent
    web = _web()

    ev = get_object_or_404(CalendarEvent, pk=pk, owner=request.user)
    serie = _sim((request.data or {}).get('serie')) if isinstance(request.data, dict) else False
    codigo, conteudo = _chamar_view_json(request, web.api_event_delete, ev.pk,
                                         corpo={'delete_all_recurrences': serie})
    if codigo != 200 or not conteudo.get('ok'):
        return _erro(conteudo.get('error') or 'Não foi possível excluir.', codigo if codigo >= 400 else 400)
    return Response({'ok': True, 'mensagem': 'Série excluída.' if serie and ev.recurrence_rule != 'none' else 'Evento excluído.'})


@_api()
def conflitos(request):
    """Aviso (não bloqueia — o portal não bloqueia): o que já está na minha agenda nesse horário."""
    inicio = _ler_data_hora(request.query_params.get('inicio'))
    fim = _ler_data_hora(request.query_params.get('fim'))
    if not inicio or not fim or fim <= inicio:
        return Response({'conflitos': []})
    qs = _meus_eventos(request.user, inicio, fim)
    ignorar = request.query_params.get('ignorar')
    if ignorar and str(ignorar).isdigit():
        qs = qs.exclude(pk=int(ignorar))
    return Response({'conflitos': [fmt.evento(request, ev, request.user) for ev in qs[:5]]})


@_api()
def opcoes(request):
    """Tipos, cores, pessoas e grupos para o formulário do evento e para escolher responsável.

    Pessoas: todo usuário ativo — o ``users_list`` do calendário (agenda/views.py:713, sem quem cria)
    e o ``pessoas_tarefa`` da transcrição (:2612, com quem cria). O app tira ``eu`` no convite.
    """
    from agenda.models import CalendarEvent
    web = _web()

    user = request.user
    pessoas = (User.objects.filter(is_active=True).select_related('sector')
               .order_by('first_name', 'last_name', 'username'))
    return Response({
        'eu': user.pk,
        'tipos': [{'valor': v, 'rotulo': r} for v, r in CalendarEvent.TYPE_CHOICES],
        'cores': [{'valor': v, 'rotulo': r} for v, r in CalendarEvent.COLOR_CHOICES],
        'pessoas': [fmt.pessoa_curta(u) for u in pessoas],
        'grupos': web.grupos_para_convite(user),
    })


# ── Convites e solicitações de reunião ───────────────────────────────────────

@_api()
def convites(request):
    """Convites de evento pendentes (api_event_invitations, agenda/views.py:1118) e solicitações de
    reunião recebidas pendentes (meeting_requests_list, :1284)."""
    from agenda.models import EventParticipant, MeetingRequest

    user = request.user
    convs = (EventParticipant.objects.filter(user=user, status='pending')
             .select_related('event', 'event__owner').order_by('event__start'))
    sols = (MeetingRequest.objects.filter(target=user, status='pending')
            .select_related('requester', 'requester__sector').order_by('proposed_start'))
    return Response({
        'convites': [fmt.convite(request, c, user) for c in convs],
        'solicitacoes': [fmt.solicitacao(s) for s in sols],
    })


def _resposta(request):
    d = request.data if isinstance(request.data, dict) else {}
    resposta = d.get('resposta')
    if resposta not in ('aceitar', 'recusar'):
        return None, ''
    return resposta, _texto(d.get('observacao'), 2000)


@_api(('POST',))
def convite_responder(request, pk):
    """Aceitar/recusar convite pelo ``api_event_invitation_respond`` do portal (avisa o dono)."""
    from agenda.models import EventParticipant
    web = _web()

    convite = get_object_or_404(EventParticipant, pk=pk, user=request.user, status='pending')
    resposta, observacao = _resposta(request)
    if resposta is None:
        return _erro('Responda com aceitar ou recusar.')
    codigo, conteudo = _chamar_view_json(request, web.api_event_invitation_respond, convite.pk, corpo={
        'action': 'accept' if resposta == 'aceitar' else 'reject', 'notes': observacao})
    if codigo != 200 or not conteudo.get('ok'):
        return _erro(conteudo.get('error') or 'Não foi possível responder.', codigo if codigo >= 400 else 400)
    convite.refresh_from_db()
    return Response({'ok': True, 'mensagem': conteudo.get('message') or 'Resposta enviada.',
                     'status': convite.status, 'evento_id': convite.event_id})


@_api(('POST',))
def solicitacao_responder(request, pk):
    """Aceitar/recusar solicitação de reunião pelas views do portal (agenda/views.py:1308 e :1325).
    Aceitar cria o evento na agenda dos dois (MeetingRequest.accept)."""
    from agenda.models import MeetingRequest
    web = _web()

    sol = get_object_or_404(MeetingRequest, pk=pk, target=request.user, status='pending')
    resposta, observacao = _resposta(request)
    if resposta is None:
        return _erro('Responda com aceitar ou recusar.')
    view = web.meeting_request_accept if resposta == 'aceitar' else web.meeting_request_reject
    avisos = _chamar_view_form(request, view, sol.pk, post={'response_notes': observacao})
    sol.refresh_from_db()
    if sol.status == 'pending':
        return _erro(next((t for n, t in avisos if n == 'error'), 'Não foi possível responder.'))
    return Response({'ok': True, 'status': sol.status,
                     'mensagem': next((t for n, t in avisos if n == 'success'), 'Resposta enviada.'),
                     'evento_id': sol.created_event_id})


# ── Transcrições de reunião ──────────────────────────────────────────────────

def _transcricoes(user):
    return _web()._visible_transcriptions_for_user(user)


@_api()
def transcricoes(request):
    """A lista de /agenda/transcricoes/ (agenda/views.py:1380): mesmas visíveis, processando primeiro."""
    from django.core.paginator import EmptyPage, Paginator
    from django.db.models import Case, IntegerField, Value, When

    qs = (_transcricoes(request.user)
          .select_related('event', 'owner')
          .annotate(status_priority=Case(When(status='processing', then=Value(0)), default=Value(1),
                                         output_field=IntegerField()),
                    total_tarefas=Count('tasks_created', distinct=True))
          .order_by('status_priority', '-created_at'))
    try:
        numero = max(int(request.query_params.get('pagina') or 1), 1)
    except ValueError:
        numero = 1
    paginador = Paginator(qs, TAMANHO_PAGINA)
    try:
        pagina = paginador.page(numero)
        itens, tem_mais = list(pagina.object_list), pagina.has_next()
    except EmptyPage:
        itens, tem_mais = [], False
    return Response({
        'resultados': [fmt.transcricao_resumo(t, request.user) for t in itens],
        'pagina': numero, 'total': paginador.count, 'tem_mais': tem_mais,
        'url_web': '/agenda/transcricoes/',
    })


def _transcricao_visivel(user, pk):
    # agenda/views.py:2603 (transcription_detail): get_object_or_404 nas visíveis → 404 fora delas.
    return get_object_or_404(_transcricoes(user).select_related('event', 'owner'), pk=pk)


def _detalhe_transcricao(request, t):
    web = _web()
    user = request.user
    tasks = list(t.tasks_created.select_related('assigned_to', 'assigned_to__sector').order_by('pk'))
    impulso = web._contexto_impulso(user, tasks)      # marca meta_impulso* nas tarefas, como no portal
    disponivel = bool(impulso.get('impulso_disponivel'))
    return {
        **fmt.transcricao_resumo(t, user),
        'resumo': (t.summary or '').strip() or None,
        'tarefas': len(tasks),
        'sentimento': t.sentiment if t.status == 'completed' else None,
        'sentimento_nome': t.get_sentiment_display() if t.status == 'completed' else None,
        'tipo_detectado': t.get_meeting_type_detected_display() if t.status == 'completed' else None,
        'tags': [x for x in (t.tags or []) if isinstance(x, str)],
        'participantes_identificados': [x for x in (t.participants_identified or []) if isinstance(x, str)],
        'secoes': fmt.secoes(t),
        'decisoes': fmt.decisoes(t),
        'riscos': fmt.riscos(t),
        'itens_acao': fmt.itens_de_acao(t),
        'lista_tarefas': [fmt.tarefa(task, disponivel) for task in tasks],
        # transcription_detail: 'pode_atribuir_tarefas' = True para quem enxerga a transcrição.
        'pode_atribuir': True,
        'impulso': ({
            'sou_gestor': bool(impulso.get('impulso_sou_gestor')),
            'gestores': [{'id': g.pk, 'nome': fmt.nome(g), 'sou_eu': g.pk == user.pk}
                         for g in impulso.get('impulso_gestores') or []],
            'hoje': impulso['impulso_hoje'].isoformat(),
        } if disponivel else None),
        'url_web': f'/agenda/transcricoes/{t.pk}/',
    }


@_api()
def transcricao(request, pk):
    return Response(_detalhe_transcricao(request, _transcricao_visivel(request.user, pk)))


@_api(('POST',))
def tarefa_responsavel(request, pk, task_id):
    """Escolher o responsável da tarefa pelo ``api_transcription_assign_task`` do portal."""
    from core.models import TaskActivity
    web = _web()

    t = _transcricao_visivel(request.user, pk)
    get_object_or_404(TaskActivity, pk=task_id, source_transcription=t)
    d = request.data if isinstance(request.data, dict) else {}
    codigo, conteudo = _chamar_view_json(request, web.api_transcription_assign_task, t.pk, task_id,
                                         corpo={'user_id': d.get('user_id')})
    if codigo != 200 or not conteudo.get('ok'):
        return _erro(conteudo.get('error') or 'Não foi possível atribuir.', codigo if codigo >= 400 else 400)
    return Response({'mensagem': conteudo.get('message') or 'Responsável atualizado.',
                     'transcricao': _detalhe_transcricao(request, t)})


@_api(('POST',))
def tarefa_impulso(request, pk, task_id):
    """O "Importar para o meu Impulso" pelo ``api_transcription_task_impulso`` do portal (regras em
    impulso/importacao.py: gestor permitido, prazo, aprovação, tarefa já importada)."""
    from core.models import TaskActivity
    web = _web()

    t = _transcricao_visivel(request.user, pk)
    get_object_or_404(TaskActivity, pk=task_id, source_transcription=t)
    d = request.data if isinstance(request.data, dict) else {}
    corpo = {
        'titulo': _texto(d.get('titulo')),
        'descricao': _texto(d.get('descricao')),
        'prazo': _texto(d.get('prazo')),
        'gestor': d.get('gestor'),
        'precisa_aprovacao': 'nao' if d.get('precisa_aprovacao') in (False, 'nao', 'não', '0', 0) else 'sim',
    }
    codigo, conteudo = _chamar_view_json(request, web.api_transcription_task_impulso, t.pk, task_id, corpo=corpo)
    if codigo != 200 or not conteudo.get('ok'):
        return _erro(conteudo.get('error') or 'Não foi possível importar.', codigo if codigo >= 400 else 400,
                     meta_id=conteudo.get('meta_id'))
    return Response({'mensagem': conteudo.get('message'), 'meta_id': conteudo.get('meta_id'),
                     'aprovada': bool(conteudo.get('aprovada')),
                     'transcricao': _detalhe_transcricao(request, t)})


urlpatterns = [
    path('eventos/', eventos, name='agenda_eventos'),
    path('eventos/novo/', evento_criar, name='agenda_evento_criar'),
    path('eventos/conflitos/', conflitos, name='agenda_conflitos'),
    path('eventos/<int:pk>/', evento, name='agenda_evento'),
    path('eventos/<int:pk>/editar/', evento_editar, name='agenda_evento_editar'),
    path('eventos/<int:pk>/excluir/', evento_excluir, name='agenda_evento_excluir'),
    path('opcoes/', opcoes, name='agenda_opcoes'),
    path('convites/', convites, name='agenda_convites'),
    path('convites/<int:pk>/responder/', convite_responder, name='agenda_convite_responder'),
    path('solicitacoes/<int:pk>/responder/', solicitacao_responder, name='agenda_solicitacao_responder'),
    path('transcricoes/', transcricoes, name='agenda_transcricoes'),
    path('transcricoes/<int:pk>/', transcricao, name='agenda_transcricao'),
    path('transcricoes/<int:pk>/tarefas/<int:task_id>/responsavel/', tarefa_responsavel,
         name='agenda_tarefa_responsavel'),
    path('transcricoes/<int:pk>/tarefas/<int:task_id>/impulso/', tarefa_impulso, name='agenda_tarefa_impulso'),
]
