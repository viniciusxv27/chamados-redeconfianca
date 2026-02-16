import json
from datetime import datetime, timedelta, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import User, Sector
from .models import CalendarEvent, MeetingRequest, EventParticipant

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
    )

    # Participantes - criar convites pendentes
    participant_ids = data.get('participants', [])
    if participant_ids:
        participants = User.objects.filter(pk__in=participant_ids, is_active=True).exclude(pk=request.user.pk)
        for user in participants:
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
