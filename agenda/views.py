import json
from datetime import datetime, timedelta, time, date as date_type

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import User, Sector
from .models import CalendarEvent, MeetingRequest, EventParticipant, MeetingTranscription

try:
    from notifications.push_utils import send_push_notification_to_user
except ImportError:
    send_push_notification_to_user = None


# =========================================================================
# HELPERS
# =========================================================================

HIERARCHY_RANK = {
    'PADRAO': 0,
    'ADMINISTRATIVO': 1,
    'SUPERVISOR': 2,
    'ADMIN': 3,
    'SUPERADMIN': 4,
}


def _can_view_full_calendar(viewer, target):
    """
    Verifica se viewer pode ver a agenda completa de target.
    - SUPERADMIN vê tudo
    - Hierarquia maior no mesmo setor vê subordinados
    """
    if viewer.pk == target.pk:
        return True
    if viewer.hierarchy == 'SUPERADMIN':
        return True
    # Mesmo setor, hierarquia maior
    viewer_rank = HIERARCHY_RANK.get(viewer.hierarchy, 0)
    target_rank = HIERARCHY_RANK.get(target.hierarchy, 0)
    if viewer_rank > target_rank:
        viewer_sectors = set(viewer.sectors.values_list('id', flat=True))
        target_sectors = set(target.sectors.values_list('id', flat=True))
        if viewer_sectors & target_sectors:
            return True
    return False


def _get_busy_slots(user, start_date, end_date):
    """Retorna lista de slots ocupados (sem detalhes) de um usuário"""
    events = CalendarEvent.objects.filter(
        owner=user, start__lt=end_date, end__gt=start_date
    ).values_list('start', 'end')
    return [{'start': s.isoformat(), 'end': e.isoformat()} for s, e in events]


def _get_available_slots(user, date, slot_duration_min=30):
    """Calcula horários disponíveis de um usuário em um dia"""
    day_start = timezone.make_aware(datetime.combine(date, time(8, 0)))
    day_end = timezone.make_aware(datetime.combine(date, time(18, 0)))

    events = CalendarEvent.objects.filter(
        owner=user, start__lt=day_end, end__gt=day_start
    ).order_by('start')

    busy = [(max(e.start, day_start), min(e.end, day_end)) for e in events]

    slots = []
    current = day_start
    for busy_start, busy_end in busy:
        while current + timedelta(minutes=slot_duration_min) <= busy_start:
            slot_end = current + timedelta(minutes=slot_duration_min)
            slots.append({'start': current, 'end': slot_end})
            current = slot_end
        current = max(current, busy_end)

    while current + timedelta(minutes=slot_duration_min) <= day_end:
        slot_end = current + timedelta(minutes=slot_duration_min)
        slots.append({'start': current, 'end': slot_end})
        current = slot_end

    return slots


# =========================================================================
# CALENDÁRIO PRINCIPAL
# =========================================================================

@login_required
def calendar_view(request):
    """Página principal da agenda com FullCalendar"""
    pending_received = MeetingRequest.objects.filter(
        target=request.user, status='pending'
    ).count()

    pending_invitations = EventParticipant.objects.filter(
        user=request.user, status='pending'
    ).count()

    users_list = User.objects.filter(
        is_active=True
    ).exclude(pk=request.user.pk).select_related('sector').order_by('first_name')

    sectors = Sector.objects.all().order_by('name')

    context = {
        'pending_requests': pending_received,
        'pending_invitations': pending_invitations,
        'users_list': users_list,
        'sectors': sectors,
    }
    return render(request, 'agenda/calendar.html', context)


# =========================================================================
# HELPERS DE RECORRÊNCIA
# =========================================================================

def _parse_recurrence_until(value):
    """Parse uma data de recurrence_until a partir de string."""
    if not value:
        return None
    try:
        if isinstance(value, str):
            return date_type.fromisoformat(value)
        return value
    except (ValueError, TypeError):
        return None


def _create_weekly_occurrences(parent_event, invited_users):
    """Cria ocorrências semanais a partir do evento pai."""
    until = parent_event.recurrence_until
    if not until:
        # Padrão: 3 meses
        until = (parent_event.start + timedelta(days=90)).date()

    duration = parent_event.end - parent_event.start
    current_start = parent_event.start + timedelta(weeks=1)
    max_date = datetime.combine(until, time(23, 59, 59))
    if current_start.tzinfo and not max_date.tzinfo:
        from django.utils import timezone as tz
        max_date = tz.make_aware(max_date)

    while current_start <= max_date:
        child = CalendarEvent.objects.create(
            owner=parent_event.owner,
            title=parent_event.title,
            description=parent_event.description,
            event_type=parent_event.event_type,
            color=parent_event.color,
            start=current_start,
            end=current_start + duration,
            all_day=parent_event.all_day,
            location=parent_event.location,
            link=parent_event.link,
            is_private=parent_event.is_private,
            recurrence_rule='weekly',
            recurrence_until=parent_event.recurrence_until,
            recurrence_parent=parent_event,
        )
        # Copiar convites de participantes
        for user in invited_users:
            EventParticipant.objects.create(
                event=child,
                user=user,
                status='pending',
            )
        current_start += timedelta(weeks=1)


# =========================================================================
# API DE EVENTOS (JSON para FullCalendar)
# =========================================================================

@login_required
def api_events(request):
    """Retorna eventos em JSON para o FullCalendar"""
    start_str = request.GET.get('start')
    end_str = request.GET.get('end')
    target_user_id = request.GET.get('user_id')

    if not start_str or not end_str:
        return JsonResponse([], safe=False)

    try:
        start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        end = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return JsonResponse([], safe=False)

    # Ver eventos de outro usuário?
    if target_user_id:
        try:
            target = User.objects.get(pk=target_user_id, is_active=True)
        except User.DoesNotExist:
            return JsonResponse([], safe=False)

        if _can_view_full_calendar(request.user, target):
            events = CalendarEvent.objects.filter(
                owner=target, start__lt=end, end__gt=start
            )
        else:
            # Apenas mostra slots ocupados (sem detalhes)
            busy = _get_busy_slots(target, start, end)
            return JsonResponse(busy, safe=False)
    else:
        # Meus eventos + eventos onde sou participante
        from django.db.models import Q
        events = CalendarEvent.objects.filter(
            Q(owner=request.user) | Q(participants=request.user),
            start__lt=end,
            end__gt=start,
        ).distinct()

    data = []
    for ev in events:
        data.append({
            'id': ev.pk,
            'title': ev.title,
            'start': ev.start.isoformat(),
            'end': ev.end.isoformat(),
            'allDay': ev.all_day,
            'color': ev.color,
            'extendedProps': {
                'description': ev.description,
                'location': ev.location,
                'link': ev.link,
                'event_type': ev.event_type,
                'type_display': ev.get_event_type_display(),
                'is_owner': ev.owner_id == request.user.pk,
                'owner_name': ev.owner.full_name,
            }
        })
    return JsonResponse(data, safe=False)


@login_required
def api_event_detail(request, pk):
    """Detalhes de um evento"""
    event = get_object_or_404(CalendarEvent, pk=pk)
    if event.owner != request.user and not _can_view_full_calendar(request.user, event.owner):
        # Check if user is a participant
        if not EventParticipant.objects.filter(event=event, user=request.user).exists():
            return JsonResponse({'error': 'Sem permissão'}, status=403)

    participants = []
    for ep in event.event_participants.select_related('user'):
        participants.append({
            'id': ep.user.id,
            'first_name': ep.user.first_name,
            'last_name': ep.user.last_name,
            'email': ep.user.email,
            'status': ep.status,
            'status_display': ep.get_status_display(),
        })
    
    return JsonResponse({
        'id': event.pk,
        'title': event.title,
        'description': event.description,
        'event_type': event.event_type,
        'type_display': event.get_event_type_display(),
        'color': event.color,
        'start': event.start.isoformat(),
        'end': event.end.isoformat(),
        'all_day': event.all_day,
        'location': event.location,
        'link': event.link,
        'is_private': event.is_private,
        'is_owner': event.owner_id == request.user.pk,
        'owner_name': event.owner.full_name,
        'participants': participants,
        'recurrence': event.recurrence_rule,
        'recurrence_until': event.recurrence_until.isoformat() if event.recurrence_until else None,
        'recurrence_parent_id': event.recurrence_parent_id,
    })


@login_required
@require_POST
def api_event_create(request):
    """Criar evento via AJAX"""
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    title = data.get('title', '').strip()
    if not title:
        return JsonResponse({'error': 'Título é obrigatório'}, status=400)

    try:
        start = datetime.fromisoformat(data['start'])
        end = datetime.fromisoformat(data['end'])
    except (KeyError, ValueError):
        return JsonResponse({'error': 'Datas inválidas'}, status=400)

    if end <= start:
        return JsonResponse({'error': 'A data de fim deve ser após a de início'}, status=400)

    event = CalendarEvent.objects.create(
        owner=request.user,
        title=title,
        description=data.get('description', ''),
        event_type=data.get('event_type', 'event'),
        color=data.get('color', '#4f46e5'),
        start=start,
        end=end,
        all_day=data.get('all_day', False),
        location=data.get('location', ''),
        link=data.get('link', ''),
        is_private=data.get('is_private', False),
        recurrence_rule=data.get('recurrence', 'none'),
        recurrence_until=_parse_recurrence_until(data.get('recurrence_until')),
    )

    # Participantes - criar convites pendentes
    participant_ids = data.get('participants', [])
    invited_users = []
    if participant_ids:
        invited_users = list(User.objects.filter(pk__in=participant_ids, is_active=True).exclude(pk=request.user.pk))
        for user in invited_users:
            EventParticipant.objects.create(
                event=event,
                user=user,
                status='pending',
            )
            # Enviar notificação push
            if send_push_notification_to_user:
                try:
                    send_push_notification_to_user(
                        user,
                        'Convite para evento',
                        f'{request.user.full_name} convidou você para: {event.title}',
                        action_url='/agenda/',
                    )
                except Exception:
                    pass

    # Gerar ocorrências recorrentes (semanal)
    if event.recurrence_rule == 'weekly':
        _create_weekly_occurrences(event, invited_users)

    return JsonResponse({
        'id': event.pk,
        'title': event.title,
        'start': event.start.isoformat(),
        'end': event.end.isoformat(),
        'color': event.color,
    }, status=201)


@login_required
@require_POST
def api_event_update(request, pk):
    """Atualizar evento (mover/redimensionar/editar)"""
    event = get_object_or_404(CalendarEvent, pk=pk, owner=request.user)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    if 'title' in data:
        event.title = data['title']
    if 'description' in data:
        event.description = data['description']
    if 'start' in data:
        event.start = datetime.fromisoformat(data['start'])
    if 'end' in data:
        event.end = datetime.fromisoformat(data['end'])
    if 'all_day' in data:
        event.all_day = data['all_day']
    if 'color' in data:
        event.color = data['color']
    if 'event_type' in data:
        event.event_type = data['event_type']
    if 'location' in data:
        event.location = data['location']
    if 'link' in data:
        event.link = data['link']
    if 'is_private' in data:
        event.is_private = data['is_private']

    event.save()

    if 'participants' in data:
        new_participant_ids = set(data['participants'])
        # Exclude owner from participants
        new_participant_ids.discard(request.user.pk)
        
        # Get existing participant user IDs
        existing_participants = {ep.user_id: ep for ep in event.event_participants.all()}
        existing_ids = set(existing_participants.keys())
        
        # Remove participants no longer in list
        to_remove = existing_ids - new_participant_ids
        event.event_participants.filter(user_id__in=to_remove).delete()
        
        # Add new participants
        to_add = new_participant_ids - existing_ids
        new_users = User.objects.filter(pk__in=to_add, is_active=True)
        for user in new_users:
            EventParticipant.objects.create(
                event=event,
                user=user,
                status='pending',
            )
            # Enviar notificação push
            if send_push_notification_to_user:
                try:
                    send_push_notification_to_user(
                        user,
                        'Convite para evento',
                        f'{request.user.full_name} convidou você para: {event.title}',
                        action_url='/agenda/',
                    )
                except Exception:
                    pass

    return JsonResponse({'ok': True})


@login_required
@require_POST
def api_event_delete(request, pk):
    """Excluir evento"""
    event = get_object_or_404(CalendarEvent, pk=pk, owner=request.user)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = {}

    delete_all = data.get('delete_all_recurrences', False)

    if delete_all and event.recurrence_rule != 'none':
        # Excluir todos da série
        if event.recurrence_parent_id:
            parent = event.recurrence_parent
            parent.recurrence_children.all().delete()
            parent.delete()
        else:
            event.recurrence_children.all().delete()
            event.delete()
    else:
        event.delete()

    return JsonResponse({'ok': True})


@login_required
def api_event_invitations(request):
    """Lista convites pendentes para eventos"""
    invitations = EventParticipant.objects.filter(
        user=request.user, status='pending'
    ).select_related('event', 'event__owner').order_by('-invited_at')
    
    data = []
    for inv in invitations:
        data.append({
            'id': inv.pk,
            'event_id': inv.event.pk,
            'event_title': inv.event.title,
            'event_type': inv.event.event_type,
            'event_type_display': inv.event.get_event_type_display(),
            'start': inv.event.start.isoformat(),
            'end': inv.event.end.isoformat(),
            'location': inv.event.location,
            'link': inv.event.link,
            'owner_name': inv.event.owner.full_name,
            'invited_at': inv.invited_at.isoformat(),
        })
    return JsonResponse(data, safe=False)


@login_required
@require_POST
def api_event_invitation_respond(request, pk):
    """Aceitar ou recusar convite para evento"""
    invitation = get_object_or_404(EventParticipant, pk=pk, user=request.user, status='pending')
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        data = {}
    
    action = data.get('action', request.POST.get('action', ''))
    notes = data.get('notes', request.POST.get('notes', ''))
    
    if action == 'accept':
        invitation.accept(notes)
        return JsonResponse({'ok': True, 'message': 'Convite aceito!'})
    elif action == 'reject':
        invitation.reject(notes)
        return JsonResponse({'ok': True, 'message': 'Convite recusado.'})
    else:
        return JsonResponse({'error': 'Ação inválida'}, status=400)


# =========================================================================
# DISPONIBILIDADE
# =========================================================================

@login_required
def user_availability(request, user_id):
    """Ver horários disponíveis de outro usuário"""
    target = get_object_or_404(User, pk=user_id, is_active=True)

    date_str = request.GET.get('date')
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = timezone.localdate()
    else:
        selected_date = timezone.localdate()

    can_view_full = _can_view_full_calendar(request.user, target)
    available_slots = _get_available_slots(target, selected_date)

    # Gerar semana de datas para navegação
    week_start = selected_date - timedelta(days=selected_date.weekday())
    week_dates = [week_start + timedelta(days=i) for i in range(7)]

    context = {
        'target_user': target,
        'selected_date': selected_date,
        'available_slots': available_slots,
        'can_view_full': can_view_full,
        'week_dates': week_dates,
    }
    return render(request, 'agenda/availability.html', context)


# =========================================================================
# SOLICITAÇÕES DE REUNIÃO
# =========================================================================

@login_required
def request_meeting(request, user_id):
    """Solicitar reunião/chamada/horário com outro usuário"""
    target = get_object_or_404(User, pk=user_id, is_active=True)

    if target == request.user:
        messages.error(request, 'Você não pode solicitar reunião consigo mesmo.')
        return redirect('agenda:calendar')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        meeting_type = request.POST.get('meeting_type', 'meeting')
        start_str = request.POST.get('proposed_start', '')
        end_str = request.POST.get('proposed_end', '')
        location = request.POST.get('location', '').strip()

        errors = []
        if not title:
            errors.append('Título é obrigatório.')
        try:
            proposed_start = datetime.fromisoformat(start_str)
            proposed_end = datetime.fromisoformat(end_str)
            if proposed_end <= proposed_start:
                errors.append('O horário de fim deve ser após o de início.')
        except (ValueError, TypeError):
            errors.append('Datas/horários inválidos.')
            proposed_start = proposed_end = None

        if errors:
            for e in errors:
                messages.error(request, e)
        else:
            MeetingRequest.objects.create(
                requester=request.user,
                target=target,
                title=title,
                description=description,
                meeting_type=meeting_type,
                proposed_start=proposed_start,
                proposed_end=proposed_end,
                location=location,
            )
            messages.success(request, f'Solicitação enviada para {target.full_name}!')
            return redirect('agenda:meeting_requests')

    # Pegar data/hora do slot selecionado (se vier da tela de disponibilidade)
    prefill_start = request.GET.get('start', '')
    prefill_end = request.GET.get('end', '')

    context = {
        'target_user': target,
        'prefill_start': prefill_start,
        'prefill_end': prefill_end,
    }
    return render(request, 'agenda/request_meeting.html', context)


@login_required
def meeting_requests_list(request):
    """Lista de solicitações enviadas e recebidas"""
    tab = request.GET.get('tab', 'received')

    received = MeetingRequest.objects.filter(
        target=request.user
    ).select_related('requester').order_by('-created_at')

    sent = MeetingRequest.objects.filter(
        requester=request.user
    ).select_related('target').order_by('-created_at')

    context = {
        'received_requests': received,
        'sent_requests': sent,
        'tab': tab,
        'pending_count': received.filter(status='pending').count(),
    }
    return render(request, 'agenda/meeting_requests.html', context)


@login_required
@require_POST
def meeting_request_accept(request, pk):
    """Aceitar solicitação de reunião"""
    mr = get_object_or_404(MeetingRequest, pk=pk, target=request.user, status='pending')
    notes = request.POST.get('response_notes', '')
    mr.accept(notes)
    messages.success(request, f'Reunião "{mr.title}" aceita! Evento adicionado à sua agenda.')
    return redirect('agenda:meeting_requests')


@login_required
@require_POST
def meeting_request_reject(request, pk):
    """Recusar solicitação de reunião"""
    mr = get_object_or_404(MeetingRequest, pk=pk, target=request.user, status='pending')
    notes = request.POST.get('response_notes', '')
    mr.reject(notes)
    messages.success(request, f'Solicitação de reunião "{mr.title}" recusada.')
    return redirect('agenda:meeting_requests')


@login_required
@require_POST
def meeting_request_cancel(request, pk):
    """Cancelar solicitação enviada"""
    mr = get_object_or_404(MeetingRequest, pk=pk, requester=request.user, status='pending')
    mr.cancel()
    messages.success(request, 'Solicitação cancelada.')
    return redirect('agenda:meeting_requests')


# =========================================================================
# VER AGENDA DE OUTRO USUÁRIO (SUPERADMIN / HIERARQUIA)
# =========================================================================

@login_required
def view_user_calendar(request, user_id):
    """Ver agenda completa de outro usuário (com permissão)"""
    target = get_object_or_404(User, pk=user_id, is_active=True)

    if not _can_view_full_calendar(request.user, target):
        messages.error(request, 'Você não tem permissão para ver a agenda deste usuário.')
        return redirect('agenda:calendar')

    context = {
        'target_user': target,
        'viewing_other': True,
    }
    return render(request, 'agenda/calendar.html', context)


# =========================================================================
# TRANSCRIÇÃO DE REUNIÕES (IA)
# =========================================================================

@login_required
def transcription_list(request):
    """Lista de transcrições do usuário"""
    transcriptions = MeetingTranscription.objects.filter(
        owner=request.user
    ).select_related('event').order_by('-created_at')

    context = {
        'transcriptions': transcriptions,
    }
    return render(request, 'agenda/transcription_list.html', context)


@login_required
def transcription_new(request):
    """Página para iniciar nova transcrição (gravar áudio ou upload)"""
    event_id = request.GET.get('event_id')
    event = None
    if event_id:
        try:
            event = CalendarEvent.objects.get(pk=event_id, owner=request.user)
        except CalendarEvent.DoesNotExist:
            pass

    context = {
        'event': event,
    }
    return render(request, 'agenda/transcription_new.html', context)


@login_required
@require_POST
def api_transcription_upload(request):
    """Recebe áudio e transcreve usando OpenAI Whisper + GPT-4o com análise avançada"""
    import openai
    import json as json_module
    import tempfile
    import os
    from django.conf import settings as django_settings

    audio_file = request.FILES.get('audio')
    title = request.POST.get('title', '').strip() or 'Reunião sem título'
    event_id = request.POST.get('event_id')
    duration_seconds = int(request.POST.get('duration_seconds', 0) or 0)

    if not audio_file:
        return JsonResponse({'error': 'Nenhum arquivo de áudio enviado.'}, status=400)

    api_key = getattr(django_settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'Chave da API OpenAI não configurada. Configure OPENAI_API_KEY no .env'}, status=500)

    event = None
    if event_id:
        try:
            event = CalendarEvent.objects.get(pk=event_id)
        except CalendarEvent.DoesNotExist:
            pass

    # Criar registro de transcrição
    transcription = MeetingTranscription.objects.create(
        owner=request.user,
        event=event,
        title=title,
        audio_file=audio_file,
        duration_seconds=duration_seconds,
        status='processing',
    )

    try:
        client = openai.OpenAI(api_key=api_key)

        # 1. Transcrever áudio com Whisper — com split automático para arquivos grandes
        audio_file.seek(0)
        audio_data = audio_file.read()
        audio_size = len(audio_data)

        WHISPER_MAX_SIZE = 24 * 1024 * 1024  # 24MB (margem do limite de 25MB)

        if audio_size <= WHISPER_MAX_SIZE:
            # Arquivo pequeno: enviar direto
            file_tuple = (audio_file.name, audio_data, audio_file.content_type or 'audio/webm')
            whisper_response = client.audio.transcriptions.create(
                model="whisper-1",
                file=file_tuple,
                language="pt",
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
            if hasattr(whisper_response, 'text'):
                raw_text = whisper_response.text
                if hasattr(whisper_response, 'duration') and whisper_response.duration:
                    transcription.duration_seconds = int(whisper_response.duration)
            else:
                raw_text = str(whisper_response)
        else:
            # Arquivo grande: dividir em partes e transcrever cada uma
            raw_text = _transcribe_large_audio(client, audio_data, audio_file.name,
                                                audio_file.content_type or 'audio/webm')

        transcription.raw_transcription = raw_text

        # 2. Análise avançada com GPT-4o
        today_str = timezone.now().strftime('%Y-%m-%d')
        system_prompt = (
            "Você é um assistente corporativo de alto nível especializado em análise de reuniões. "
            "Analise a transcrição da reunião e retorne um JSON estruturado com a seguinte análise completa:\n\n"
            "RETORNE UM JSON com estas chaves:\n\n"
            '1. "summary": Resumo executivo conciso (2-3 parágrafos) com os pontos mais importantes.\n\n'
            '2. "sections": Lista de seções/partes da reunião. Cada seção é um objeto com:\n'
            '   - "title": Título descritivo do tópico (ex: "Abertura e Contexto", "Discussão sobre Vendas")\n'
            '   - "icon": Ícone FontAwesome sugerido (ex: "fa-bullhorn", "fa-chart-line", "fa-users")\n'
            '   - "content": Resumo detalhado do que foi discutido nessa parte\n'
            '   - "highlights": Lista de frases-chave ou citações importantes dessa seção\n'
            '   - "duration_estimate": Estimativa de duração em minutos dessa seção\n\n'
            '3. "key_decisions": Lista de decisões tomadas na reunião. Cada uma com:\n'
            '   - "decision": Texto da decisão\n'
            '   - "context": Breve contexto de por que foi decidido\n'
            '   - "impact": "high", "medium" ou "low"\n\n'
            '4. "action_items": Lista de itens de ação. Cada um com:\n'
            '   - "task": Descrição da tarefa\n'
            '   - "responsible": Nome do responsável (se mencionado, senão "A definir")\n'
            '   - "deadline": Prazo mencionado ou sugerido (ISO date ou null)\n'
            '   - "priority": "high", "medium" ou "low"\n\n'
            '5. "participants_identified": Lista de nomes de pessoas mencionadas/participantes detectados na conversa.\n\n'
            '6. "sentiment": Sentimento geral da reunião: "positive", "neutral", "negative" ou "mixed".\n\n'
            '7. "meeting_type_detected": Tipo detectado: "standup", "planning", "review", "brainstorm", '
            '"oneonone", "kickoff", "status", "decision" ou "general".\n\n'
            '8. "tags": Lista de 3-8 palavras-chave/tags relevantes da reunião.\n\n'
            '9. "formatted": Transcrição completa formatada com parágrafos, pontuação, e identificação de '
            'falantes quando possível. Use marcadores como "**Participante:**" quando detectar troca de falante.\n\n'
            '10. "suggested_events": Lista de compromissos futuros mencionados. Cada um com:\n'
            '    - "title": Título do evento sugerido\n'
            '    - "description": Descrição\n'
            f'    - "suggested_date": Data sugerida em ISO (hoje é {today_str})\n\n'
            "IMPORTANTE: Divida a reunião em pelo menos 3 seções se possível (abertura, desenvolvimento, encerramento). "
            "Se a reunião tiver diversos assuntos, crie uma seção para cada. "
            "Responda APENAS com JSON válido, sem markdown, sem ```."
        )

        gpt_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Transcrição da reunião '{title}':\n\n{raw_text}"}
            ],
            temperature=0.2,
            max_tokens=4096,
        )

        gpt_text = gpt_response.choices[0].message.content.strip()
        # Limpar markdown code blocks se houver
        if gpt_text.startswith('```'):
            lines = gpt_text.split('\n')
            if lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            gpt_text = '\n'.join(lines)

        parsed = json_module.loads(gpt_text)

        transcription.formatted_transcription = parsed.get('formatted', raw_text)
        transcription.summary = parsed.get('summary', '')
        transcription.sections = parsed.get('sections', [])
        transcription.key_decisions = parsed.get('key_decisions', [])
        transcription.action_items = parsed.get('action_items', [])
        transcription.participants_identified = parsed.get('participants_identified', [])
        transcription.sentiment = parsed.get('sentiment', 'neutral')
        transcription.meeting_type_detected = parsed.get('meeting_type_detected', 'general')
        transcription.tags = parsed.get('tags', [])
        transcription.suggested_events = parsed.get('suggested_events', [])
        transcription.status = 'completed'
        transcription.save()

        # 3. Criar evento na agenda do usuário marcando que fez uma transcrição
        now = timezone.now()
        cal_event = CalendarEvent.objects.create(
            owner=request.user,
            title=f"📝 Transcrição: {title}",
            description=(
                f"Transcrição de reunião processada com IA.\n\n"
                f"📋 Resumo: {transcription.summary[:200]}...\n"
                f"✅ Itens de ação: {len(transcription.action_items)}\n"
                f"🎯 Decisões: {len(transcription.key_decisions)}\n\n"
                f"Ver transcrição completa: /agenda/transcricoes/{transcription.pk}/"
            ),
            event_type='task',
            color='#9333ea',
            start=now - timedelta(seconds=max(duration_seconds, 60)),
            end=now,
            all_day=False,
        )
        transcription.calendar_event_created = cal_event
        transcription.save(update_fields=['calendar_event_created'])

        # 4. Criar tarefas automaticamente a partir dos action_items
        _create_tasks_from_transcription(transcription, request.user)

        return JsonResponse({
            'id': transcription.pk,
            'status': 'completed',
            'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        })

    except json_module.JSONDecodeError:
        transcription.formatted_transcription = transcription.raw_transcription
        transcription.summary = 'Erro ao processar análise avançada. Transcrição bruta disponível.'
        transcription.status = 'completed'
        transcription.save()
        return JsonResponse({
            'id': transcription.pk,
            'status': 'completed',
            'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        })
    except Exception as e:
        transcription.status = 'error'
        transcription.error_message = str(e)
        transcription.save()
        return JsonResponse({'error': f'Erro ao processar: {str(e)}'}, status=500)


def _transcribe_large_audio(client, audio_data, filename, content_type):
    """Divide áudio grande em chunks de ~24MB e transcreve cada parte."""
    import math

    chunk_size = 24 * 1024 * 1024  # 24MB
    total_size = len(audio_data)
    num_chunks = math.ceil(total_size / chunk_size)

    all_text_parts = []
    for i in range(num_chunks):
        start = i * chunk_size
        end = min((i + 1) * chunk_size, total_size)
        chunk = audio_data[start:end]

        ext = filename.rsplit('.', 1)[-1] if '.' in filename else 'webm'
        chunk_name = f"part_{i + 1}.{ext}"
        file_tuple = (chunk_name, chunk, content_type)

        whisper_response = client.audio.transcriptions.create(
            model="whisper-1",
            file=file_tuple,
            language="pt",
            response_format="text",
        )
        text = whisper_response if isinstance(whisper_response, str) else str(whisper_response)
        all_text_parts.append(text.strip())

    return ' '.join(all_text_parts)


def _create_tasks_from_transcription(transcription, user):
    """Cria TaskActivity para cada action_item da transcrição."""
    from core.models import TaskActivity

    PRIORITY_MAP = {
        'high': 'HIGH',
        'medium': 'MEDIUM',
        'low': 'LOW',
    }

    action_items = transcription.action_items or []
    for item in action_items:
        task_text = item.get('task', '') if isinstance(item, dict) else str(item)
        if not task_text:
            continue

        priority = 'MEDIUM'
        deadline = None
        if isinstance(item, dict):
            priority = PRIORITY_MAP.get(item.get('priority', ''), 'MEDIUM')
            deadline_str = item.get('deadline')
            if deadline_str:
                try:
                    deadline = datetime.fromisoformat(deadline_str)
                except (ValueError, TypeError):
                    deadline = None

        task = TaskActivity.objects.create(
            title=task_text[:200],
            description=(
                f"Tarefa gerada automaticamente da transcrição: {transcription.title}\n\n"
                f"Responsável mencionado: {item.get('responsible', 'A definir') if isinstance(item, dict) else 'A definir'}"
            ),
            assigned_to=user,
            created_by=user,
            priority=priority,
            due_date=deadline,
            status='PENDING',
        )
        transcription.tasks_created.add(task)


@login_required
def transcription_detail(request, pk):
    """Visualizar uma transcrição"""
    transcription = get_object_or_404(MeetingTranscription, pk=pk, owner=request.user)
    tasks = transcription.tasks_created.select_related('assigned_to').all()
    users = User.objects.filter(is_active=True).order_by('first_name', 'username')

    context = {
        'transcription': transcription,
        'tasks': tasks,
        'users': users,
    }
    return render(request, 'agenda/transcription_detail.html', context)


@login_required
@require_POST
def api_transcription_schedule(request, pk):
    """Criar evento na agenda a partir de item sugerido da transcrição"""
    transcription = get_object_or_404(MeetingTranscription, pk=pk, owner=request.user)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    title = data.get('title', '').strip()
    description = data.get('description', '').strip()
    start_str = data.get('start', '')
    end_str = data.get('end', '')
    duration_min = int(data.get('duration_minutes', 60) or 60)

    if not title or not start_str:
        return JsonResponse({'error': 'Título e data de início são obrigatórios.'}, status=400)

    try:
        start = datetime.fromisoformat(start_str)
        if end_str:
            end = datetime.fromisoformat(end_str)
        else:
            end = start + timedelta(minutes=duration_min)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Datas inválidas.'}, status=400)

    event = CalendarEvent.objects.create(
        owner=request.user,
        title=title,
        description=f"{description}\n\n📝 Agendado a partir da transcrição: {transcription.title}",
        event_type='meeting',
        start=start,
        end=end,
        color='#16a34a',
    )

    return JsonResponse({
        'ok': True,
        'event_id': event.pk,
        'message': f'Evento "{title}" agendado com sucesso!'
    })


@login_required
@require_POST
def api_transcription_assign_task(request, pk, task_id):
    """Atribuir uma tarefa da transcrição a um usuário"""
    from core.models import TaskActivity

    transcription = get_object_or_404(MeetingTranscription, pk=pk, owner=request.user)
    task = get_object_or_404(TaskActivity, pk=task_id, source_transcription=transcription)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    user_id = data.get('user_id')
    if not user_id:
        return JsonResponse({'error': 'user_id é obrigatório.'}, status=400)

    try:
        target_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'Usuário não encontrado.'}, status=404)

    task.assigned_to = target_user
    task.save(update_fields=['assigned_to'])

    return JsonResponse({
        'ok': True,
        'task_id': task.pk,
        'assigned_to': target_user.get_full_name() or target_user.username,
        'message': f'Tarefa atribuída a {target_user.get_full_name() or target_user.username}!'
    })


@login_required
@require_POST
def api_transcription_reprocess(request, pk):
    """Reiniciar o processamento de uma transcrição (processing travada ou com erro).
    
    Se já existe raw_transcription, pula o Whisper e refaz apenas a análise GPT-4o.
    Se não existe, refaz tudo desde o Whisper.
    """
    import openai
    import json as json_module
    from django.conf import settings as django_settings

    transcription = get_object_or_404(MeetingTranscription, pk=pk, owner=request.user)

    if transcription.status == 'completed':
        # Permitir reprocessar mesmo completas (o usuário quer "priorizar" = refazer)
        pass

    api_key = getattr(django_settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'Chave da API OpenAI não configurada.'}, status=500)

    transcription.status = 'processing'
    transcription.error_message = ''
    transcription.save(update_fields=['status', 'error_message'])

    try:
        client = openai.OpenAI(api_key=api_key)

        # Se já tem raw_transcription, pula Whisper
        raw_text = transcription.raw_transcription or ''

        if not raw_text and transcription.audio_file:
            # Precisa transcrever com Whisper
            transcription.audio_file.open('rb')
            audio_data = transcription.audio_file.read()
            transcription.audio_file.close()
            audio_size = len(audio_data)

            fname = transcription.audio_file.name or 'audio.webm'
            content_type = 'audio/webm'

            WHISPER_MAX_SIZE = 24 * 1024 * 1024

            if audio_size <= WHISPER_MAX_SIZE:
                file_tuple = (fname, audio_data, content_type)
                whisper_response = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=file_tuple,
                    language="pt",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )
                if hasattr(whisper_response, 'text'):
                    raw_text = whisper_response.text
                    if hasattr(whisper_response, 'duration') and whisper_response.duration:
                        transcription.duration_seconds = int(whisper_response.duration)
                else:
                    raw_text = str(whisper_response)
            else:
                raw_text = _transcribe_large_audio(client, audio_data, fname, content_type)

            transcription.raw_transcription = raw_text
            transcription.save(update_fields=['raw_transcription', 'duration_seconds'])

        if not raw_text:
            transcription.status = 'error'
            transcription.error_message = 'Sem áudio ou transcrição bruta para processar.'
            transcription.save(update_fields=['status', 'error_message'])
            return JsonResponse({'error': 'Sem áudio ou transcrição bruta para processar.'}, status=400)

        # Análise GPT-4o
        today_str = timezone.now().strftime('%Y-%m-%d')
        system_prompt = (
            "Você é um assistente corporativo de alto nível especializado em análise de reuniões. "
            "Analise a transcrição da reunião e retorne um JSON estruturado com a seguinte análise completa:\n\n"
            "RETORNE UM JSON com estas chaves:\n\n"
            '1. "summary": Resumo executivo conciso (2-3 parágrafos) com os pontos mais importantes.\n\n'
            '2. "sections": Lista de seções/partes da reunião. Cada seção é um objeto com:\n'
            '   - "title": Título descritivo do tópico\n'
            '   - "icon": Ícone FontAwesome sugerido (ex: "fa-bullhorn", "fa-chart-line")\n'
            '   - "content": Resumo detalhado do que foi discutido nessa parte\n'
            '   - "highlights": Lista de frases-chave ou citações importantes\n'
            '   - "duration_estimate": Estimativa de duração em minutos\n\n'
            '3. "key_decisions": Lista de decisões. Cada uma com:\n'
            '   - "decision": Texto da decisão\n'
            '   - "context": Breve contexto\n'
            '   - "impact": "high", "medium" ou "low"\n\n'
            '4. "action_items": Lista de itens de ação. Cada um com:\n'
            '   - "task": Descrição da tarefa\n'
            '   - "responsible": Nome do responsável (ou "A definir")\n'
            '   - "deadline": Prazo ISO date ou null\n'
            '   - "priority": "high", "medium" ou "low"\n\n'
            '5. "participants_identified": Lista de nomes de participantes.\n\n'
            '6. "sentiment": "positive", "neutral", "negative" ou "mixed".\n\n'
            '7. "meeting_type_detected": "standup", "planning", "review", "brainstorm", '
            '"oneonone", "kickoff", "status", "decision" ou "general".\n\n'
            '8. "tags": Lista de 3-8 tags relevantes.\n\n'
            '9. "formatted": Transcrição completa formatada com parágrafos e identificação de falantes.\n\n'
            '10. "suggested_events": Lista de compromissos futuros. Cada um com:\n'
            '    - "title": Título\n'
            '    - "description": Descrição\n'
            f'    - "suggested_date": Data sugerida em ISO (hoje é {today_str})\n\n'
            "IMPORTANTE: Divida a reunião em pelo menos 3 seções se possível. "
            "Responda APENAS com JSON válido, sem markdown, sem ```.\n"
        )

        gpt_response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Transcrição da reunião '{transcription.title}':\n\n{raw_text}"}
            ],
            temperature=0.2,
            max_tokens=4096,
        )

        gpt_text = gpt_response.choices[0].message.content.strip()
        if gpt_text.startswith('```'):
            lines = gpt_text.split('\n')
            if lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            gpt_text = '\n'.join(lines)

        parsed = json_module.loads(gpt_text)

        transcription.formatted_transcription = parsed.get('formatted', raw_text)
        transcription.summary = parsed.get('summary', '')
        transcription.sections = parsed.get('sections', [])
        transcription.key_decisions = parsed.get('key_decisions', [])
        transcription.action_items = parsed.get('action_items', [])
        transcription.participants_identified = parsed.get('participants_identified', [])
        transcription.sentiment = parsed.get('sentiment', 'neutral')
        transcription.meeting_type_detected = parsed.get('meeting_type_detected', 'general')
        transcription.tags = parsed.get('tags', [])
        transcription.suggested_events = parsed.get('suggested_events', [])
        transcription.status = 'completed'
        transcription.save()

        # Criar evento na agenda se ainda não existe
        if not transcription.calendar_event_created:
            now = timezone.now()
            cal_event = CalendarEvent.objects.create(
                owner=request.user,
                title=f"📝 Transcrição: {transcription.title}",
                description=(
                    f"Transcrição de reunião processada com IA.\n\n"
                    f"📋 Resumo: {transcription.summary[:200]}...\n"
                    f"✅ Itens de ação: {len(transcription.action_items)}\n"
                    f"🎯 Decisões: {len(transcription.key_decisions)}\n\n"
                    f"Ver transcrição completa: /agenda/transcricoes/{transcription.pk}/"
                ),
                event_type='task',
                color='#9333ea',
                start=now - timedelta(seconds=max(transcription.duration_seconds or 60, 60)),
                end=now,
                all_day=False,
            )
            transcription.calendar_event_created = cal_event
            transcription.save(update_fields=['calendar_event_created'])

        # Criar tarefas se ainda não existem
        if not transcription.tasks_created.exists():
            _create_tasks_from_transcription(transcription, request.user)

        return JsonResponse({
            'ok': True,
            'status': 'completed',
            'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        })

    except json_module.JSONDecodeError:
        transcription.formatted_transcription = transcription.raw_transcription
        transcription.summary = 'Erro ao processar análise avançada. Transcrição bruta disponível.'
        transcription.status = 'completed'
        transcription.save()
        return JsonResponse({
            'ok': True,
            'status': 'completed',
            'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        })
    except Exception as e:
        transcription.status = 'error'
        transcription.error_message = str(e)
        transcription.save(update_fields=['status', 'error_message'])
        return JsonResponse({'error': f'Erro ao reprocessar: {str(e)}'}, status=500)
