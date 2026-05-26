import json
import os
import re
import subprocess
import tempfile
import threading
from collections import Counter
from datetime import datetime, timedelta, time, date as date_type

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files import File
from django.db import close_old_connections
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import User, Sector
from .models import CalendarEvent, MeetingRequest, EventParticipant, MeetingTranscription

try:
    from core.models import NotificationMixin
except ImportError:
    NotificationMixin = None

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


def _format_event_datetime(value):
    """Formata data/hora para mensagens de notificação."""
    if not value:
        return ''
    try:
        if timezone.is_naive(value):
            return value.strftime('%d/%m/%Y às %H:%M')
        return timezone.localtime(value).strftime('%d/%m/%Y às %H:%M')
    except Exception:
        return str(value)


def _notify_agenda_user(user, title, message, action_url='/agenda/'):
    """Envia notificação interna e push, com fallback silencioso."""
    if not user:
        return

    if NotificationMixin:
        try:
            NotificationMixin.create_notification(
                user=user,
                title=title,
                message=message,
                notification_type='SYSTEM',
                related_url=action_url,
            )
        except Exception:
            pass

    if send_push_notification_to_user:
        try:
            send_push_notification_to_user(
                user,
                title,
                message,
                action_url=action_url,
            )
        except Exception:
            pass


def _notify_agenda_users(users, title, message, action_url='/agenda/'):
    """Dispara notificação para uma lista de usuários."""
    for user in users:
        _notify_agenda_user(user, title, message, action_url=action_url)


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


def _extract_json_payload(text):
    """Extrai um objeto JSON válido mesmo quando a IA retorna texto extra."""
    if not text:
        return {}

    cleaned = text.strip()
    if cleaned.startswith('```'):
        lines = cleaned.split('\n')
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        cleaned = '\n'.join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', cleaned)
        if not match:
            raise
        return json.loads(match.group(0))


def _ensure_list(value):
    """Garante lista para campos JSON, aceitando alguns formatos comuns de retorno da IA."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ('items', 'data', 'values', 'results'):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
    return []


def _friendly_openai_error(err):
    """Converte erros da OpenAI/IA em mensagens claras para o usuário final."""
    raw = str(err or '').strip()
    low = raw.lower()
    cls = err.__class__.__name__.lower() if err is not None else ''

    if 'insufficient_quota' in low or 'insufficient quota' in low or 'exceeded your current quota' in low:
        return (
            'Cota da API OpenAI esgotada. Recarregue os créditos no painel da OpenAI '
            'e clique em "Reiniciar Processamento" para tentar de novo.'
        )
    if 'ratelimit' in cls or 'rate_limit' in low or 'rate limit' in low or 'too many requests' in low:
        return (
            'Limite de requisições da OpenAI atingido temporariamente. Aguarde alguns minutos '
            'e clique em "Reiniciar Processamento".'
        )
    if 'context_length_exceeded' in low or 'maximum context length' in low or 'context window' in low:
        return (
            'Transcrição muito longa para o modelo. Reduza o texto bruto e clique em '
            '"Reiniciar Processamento".'
        )
    if 'invalid_api_key' in low or 'incorrect api key' in low or 'authentication' in low:
        return 'Chave da API OpenAI inválida. Avise o administrador e tente reprocessar depois.'
    if 'timeout' in cls or 'timed out' in low or 'timeout' in low:
        return 'A OpenAI demorou para responder. Clique em "Reiniciar Processamento" para tentar de novo.'
    if 'apiconnectionerror' in cls or 'connection' in low and 'openai' in low:
        return 'Falha de conexão com a OpenAI. Verifique a internet e clique em "Reiniciar Processamento".'

    return raw or 'Erro desconhecido durante o processamento.'


def _normalize_transcription_analysis(payload, source_text):
    """Normaliza o payload da IA para os campos esperados pelo portal."""
    payload = payload or {}

    formatted = payload.get('formatted') or payload.get('formatted_transcription') or source_text
    summary = payload.get('summary') or ''
    sections = _ensure_list(payload.get('sections'))
    key_decisions = _ensure_list(payload.get('key_decisions'))
    action_items = _ensure_list(payload.get('action_items'))
    participants = _ensure_list(payload.get('participants_identified'))
    tags = _ensure_list(payload.get('tags'))
    suggested_events = _ensure_list(payload.get('suggested_events'))
    risks = _ensure_list(payload.get('risks'))

    sentiment = payload.get('sentiment') or 'neutral'
    if sentiment not in {'positive', 'neutral', 'negative', 'mixed'}:
        sentiment = 'neutral'

    meeting_type = payload.get('meeting_type_detected') or 'general'
    if meeting_type not in {'standup', 'planning', 'review', 'brainstorm', 'oneonone', 'kickoff', 'status', 'decision', 'general'}:
        meeting_type = 'general'

    return {
        'formatted_transcription': formatted,
        'summary': summary,
        'sections': sections,
        'key_decisions': key_decisions,
        'action_items': action_items,
        'participants_identified': participants,
        'sentiment': sentiment,
        'meeting_type_detected': meeting_type,
        'tags': tags,
        'suggested_events': suggested_events,
        'risks': risks,
    }


PARTICIPANT_ROLE_ICON_MAP = {
    'Moderador': 'fa-chess-king',
    'Tomador de decisão': 'fa-gavel',
    'Responsável técnico': 'fa-cogs',
    'Cliente': 'fa-user-tie',
    'Observador': 'fa-eye',
    'Participante': 'fa-user',
}


def _parse_participant_roles(raw_value):
    """Normaliza papéis de participantes enviados pela UI."""
    if not raw_value:
        return []

    data = raw_value
    if isinstance(raw_value, str):
        try:
            data = json.loads(raw_value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    if not isinstance(data, list):
        return []

    normalized = []
    for item in data[:12]:
        if not isinstance(item, dict):
            continue

        name = (item.get('name') or '').strip()
        role = (item.get('role') or '').strip() or 'Participante'

        if not name:
            continue
        if role not in PARTICIPANT_ROLE_ICON_MAP:
            role = 'Participante'

        normalized.append({
            'name': name[:80],
            'role': role,
            'icon': PARTICIPANT_ROLE_ICON_MAP.get(role, 'fa-user'),
        })

    return normalized


def _build_participant_roles_context(participant_roles):
    """Monta contexto textual para orientar a IA sobre papéis dos participantes."""
    if not participant_roles:
        return ''

    lines = []
    for entry in participant_roles:
        if not isinstance(entry, dict):
            continue
        name = (entry.get('name') or '').strip()
        role = (entry.get('role') or '').strip()
        if not name or not role:
            continue
        lines.append(f'- {name}: {role}')

    if not lines:
        return ''

    return (
        "Contexto adicional definido pelo usuário para identificação de falantes:\n"
        + "\n".join(lines)
    )


def _build_transcription_system_prompt(today_str, compact=False):
    """Prompt de análise estruturada da transcrição."""
    formatted_instruction = (
        '9. "formatted": Transcrição reorganizada em parágrafos coesos por tópico, com identificação de '
        'falantes quando possível ("Falante 1:", "Maria:" etc.), pontuação corrigida e sem inventar conteúdo.\n\n'
        if not compact else
        '9. "formatted": Versão formatada e resumida da transcrição (máximo de 3500 caracteres), preservando o sentido.\n\n'
    )

    return (
        "Você é um Chief of Staff sênior, analista executivo e facilitador de reuniões. Sua missão é ler a "
        "transcrição inteira e produzir uma análise PROFUNDA, DENSA, ACIONÁVEL e FIEL ao que foi dito, sem "
        "inventar fatos. Use português do Brasil corporativo, claro e direto. Quando faltar informação sobre "
        "nomes, papéis ou datas, escreva 'A definir' em vez de inventar.\n\n"
        "Diretrizes obrigatórias de qualidade:\n"
        "- Cite NÚMEROS, VALORES, PRAZOS, METAS, NOMES e TERMOS exatos sempre que aparecerem.\n"
        "- Evite frases vagas como 'foi discutido X' sem dizer o quê. Seja específico.\n"
        "- Identifique riscos, bloqueios e oportunidades, mesmo quando implícitos.\n"
        "- Para cada decisão e ação, conecte com o impacto no negócio quando inferível.\n"
        "- Cada item de ação deve ser SMART (específico, mensurável, com responsável e prazo realista).\n"
        "- Quando a transcrição mencionar 'semana que vem', 'até sexta', 'próxima reunião', calcule a data real "
        f"a partir de hoje ({today_str}).\n\n"
        "RETORNE UM JSON com EXATAMENTE estas chaves:\n\n"
        '1. "summary": Resumo executivo DETALHADO em Markdown, com pelo menos 6 parágrafos densos '
        '(mínimo 1800 caracteres, ideal 2500-5000). Cubra OBRIGATORIAMENTE: (a) contexto e objetivo da reunião; '
        '(b) principais tópicos com explicações; (c) decisões e seus porquês; (d) divergências, preocupações ou '
        'riscos; (e) próximos passos com responsáveis e prazos; (f) avaliação geral. Cite números, prazos e '
        'nomes mencionados. NÃO seja superficial: o leitor deve entender a reunião inteira lendo só o resumo.\n\n'
        '2. "sections": Lista de seções/partes da reunião (MÍNIMO 6, idealmente entre 8 e 16, conforme '
        'a riqueza da reunião — quanto mais tópicos distintos, mais seções). NUNCA agrupe vários assuntos '
        'numa mesma seção: prefira QUEBRAR em mais seções menores e específicas. Cada seção deve ter:\n'
        '   - "title": Título descritivo e específico (ex: "Revisão das metas Q3 - Comercial")\n'
        '   - "icon": Ícone FontAwesome (ex: "fa-bullhorn", "fa-chart-line", "fa-handshake")\n'
        '   - "content": Resumo NARRATIVO, DETALHADO e DENSO em Markdown, com 2 a 4 parágrafos coesos '
        '(mínimo 900 caracteres, ideal 1200-2200). DEVE cobrir, sempre que possível: (a) contexto da '
        'discussão e por que o tema entrou na pauta; (b) principais pontos abordados, citando NÚMEROS, '
        'VALORES, METAS, NOMES e DATAS EXATOS mencionados; (c) divergências, dúvidas ou consensos '
        'expressos pelos participantes; (d) encaminhamentos específicos surgidos nesta parte da reunião. '
        'Proibido ser superficial ou genérico — reproduza o conteúdo real desta parte da conversa.\n'
        '   - "highlights": 3 a 8 frases-chave ou citações importantes (literais quando possível, entre '
        'aspas se for citação direta), com pelo menos uma contendo número/valor/prazo quando houver.\n'
        '   - "topics_discussed": Lista de 3 a 8 sub-tópicos discutidos nesta seção (frases curtas, '
        'iniciando com substantivo, ex: "Meta de vendas de outubro", "Renegociação com fornecedor X").\n'
        '   - "decisions_in_section": Lista (pode ser vazia) das decisões pontuais tomadas dentro desta '
        'seção (frases curtas e objetivas).\n'
        '   - "duration_estimate": Estimativa de duração em minutos.\n\n'
        '3. "key_decisions": Lista de decisões objetivas. Cada uma com:\n'
        '   - "decision": Texto curto e direto da decisão\n'
        '   - "context": Por que foi tomada (1-2 frases)\n'
        '   - "impact": "high", "medium" ou "low"\n\n'
        '4. "action_items": Lista de itens de ação SMART. Cada um com:\n'
        '   - "task": Descrição clara, iniciando com verbo no infinitivo\n'
        '   - "responsible": Nome do responsável (ou "A definir")\n'
        '   - "deadline": Prazo em ISO date (YYYY-MM-DD) calculado a partir de hoje, ou null\n'
        '   - "priority": "high", "medium" ou "low"\n'
        '   - "success_criteria": Como saber que está concluído (1 frase curta)\n\n'
        '5. "participants_identified": Lista de nomes de participantes detectados.\n\n'
        '6. "sentiment": "positive", "neutral", "negative" ou "mixed".\n\n'
        '7. "meeting_type_detected": "standup", "planning", "review", "brainstorm", '
        '"oneonone", "kickoff", "status", "decision" ou "general".\n\n'
        '8. "tags": Lista de 4-8 tags relevantes (palavras únicas, em minúsculo).\n\n'
        f'{formatted_instruction}'
        '10. "suggested_events": Lista de follow-ups e novos compromissos sugeridos. Cada um com:\n'
        '    - "title": Título\n'
        '    - "description": Pauta/objetivo\n'
        f'    - "suggested_date": Data sugerida em ISO (hoje é {today_str})\n\n'
        '11. "risks": Lista de até 6 riscos/bloqueios identificados (pode ser lista vazia). Cada um com:\n'
        '    - "risk": Descrição do risco\n'
        '    - "mitigation": Ação sugerida para mitigar (1 frase)\n'
        '    - "severity": "high", "medium" ou "low"\n\n'
        "Responda APENAS com JSON válido, sem markdown, sem ```."
    )


def _generate_transcription_analysis_single(client, meeting_title, source_text, analysis_context=''):
    """Executa análise de IA com retry quando a resposta vier truncada ou inválida."""
    clipped_text = (source_text or '').strip()
    if len(clipped_text) > 120000:
        clipped_text = clipped_text[:120000]

    user_content = f"Transcrição da reunião '{meeting_title}':\n\n{clipped_text}"
    if analysis_context:
        user_content = f"{analysis_context}\n\n{user_content}"

    today_str = timezone.now().strftime('%Y-%m-%d')
    attempts = [
        {
            'system_prompt': _build_transcription_system_prompt(today_str, compact=False),
            'max_tokens': 12000,
        },
        {
            'system_prompt': _build_transcription_system_prompt(today_str, compact=False),
            'max_tokens': 8000,
        },
        {
            'system_prompt': _build_transcription_system_prompt(today_str, compact=True),
            'max_tokens': 4500,
        },
    ]

    last_error = None
    for attempt in attempts:
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": attempt['system_prompt']},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                max_tokens=attempt['max_tokens'],
                response_format={"type": "json_object"},
            )

            choice = resp.choices[0]
            content = (choice.message.content or '').strip()
            parsed = _extract_json_payload(content)

            # Se veio truncada, tenta novamente no modo compacto.
            if getattr(choice, 'finish_reason', None) == 'length' and attempt is attempts[0]:
                raise ValueError('Resposta da IA truncada por limite de tokens.')

            return _normalize_transcription_analysis(parsed, clipped_text)
        except Exception as err:
            last_error = err

    raise last_error or ValueError('Falha ao analisar transcrição com IA.')


def _split_text_for_analysis(source_text, max_chars=100000):
    """Divide texto longo preservando blocos de parágrafos."""
    text = (source_text or '').strip()
    if len(text) <= max_chars:
        return [text]

    chunks = []
    current = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current_len + line_len > max_chars and current:
            chunks.append('\n'.join(current).strip())
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    if current:
        chunks.append('\n'.join(current).strip())

    return [c for c in chunks if c]


def _dedupe_by_text(items, key):
    seen = set()
    deduped = []
    for item in items:
        if isinstance(item, dict):
            text = (item.get(key) or '').strip()
        else:
            text = str(item or '').strip()
        if not text:
            continue
        norm = re.sub(r'\s+', ' ', text.lower())
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(item)
    return deduped


def _merge_transcription_analyses(client, meeting_title, analyses, source_text):
    summaries = [a.get('summary', '').strip() for a in analyses if a.get('summary')]
    formatted_parts = [a.get('formatted_transcription', '').strip() for a in analyses if a.get('formatted_transcription')]
    sections = []
    key_decisions = []
    action_items = []
    suggested_events = []
    risks = []
    participants = set()
    meeting_types = []
    sentiments = []
    tag_counter = Counter()

    for analysis in analyses:
        sections.extend(analysis.get('sections') or [])
        key_decisions.extend(analysis.get('key_decisions') or [])
        action_items.extend(analysis.get('action_items') or [])
        suggested_events.extend(analysis.get('suggested_events') or [])
        risks.extend(analysis.get('risks') or [])
        participants.update([p for p in (analysis.get('participants_identified') or []) if p])
        meeting_types.append(analysis.get('meeting_type_detected') or 'general')
        sentiments.append(analysis.get('sentiment') or 'neutral')
        for tag in (analysis.get('tags') or []):
            if isinstance(tag, str) and tag.strip():
                tag_counter[tag.strip().lower()] += 1

    formatted_transcription = '\n\n'.join([p for p in formatted_parts if p]).strip() or (source_text or '').strip()

    summary = ''
    if summaries:
        joined = '\n\n---\n\n'.join([text for text in summaries if text])
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Você é um analista executivo sênior. Consolide os resumos parciais de uma "
                            "reunião longa em UM ÚNICO resumo executivo final, em português do Brasil, "
                            "escrito em Markdown, sem repetir informações e preservando todos os detalhes "
                            "relevantes (números, prazos, nomes, decisões, riscos e próximos passos). "
                            "O resumo final deve ser DETALHADO e ABRANGENTE, com no mínimo 6 parágrafos "
                            "longos (entre 2500 e 4500 caracteres), cobrindo: contexto e objetivo, "
                            "principais tópicos discutidos, decisões tomadas, divergências e riscos, "
                            "próximos passos com responsáveis e prazos, e uma conclusão geral. Use bullet "
                            "points quando ajudar a leitura. Não invente fatos: use apenas o que está nos "
                            "resumos parciais."
                        )
                    },
                    {"role": "user", "content": f"Resumos parciais da reunião '{meeting_title}':\n\n{joined}"},
                ],
                temperature=0.2,
                max_tokens=4000,
            )
            summary = (resp.choices[0].message.content or '').strip()
        except Exception:
            summary = '\n\n'.join(summaries)

    key_decisions = _dedupe_by_text(key_decisions, 'decision')[:60]
    action_items = _dedupe_by_text(action_items, 'task')[:80]
    suggested_events = _dedupe_by_text(suggested_events, 'title')[:40]
    risks = _dedupe_by_text(risks, 'risk')[:20]

    sections = sections[:60]
    tags = [tag for tag, _ in tag_counter.most_common(8)]

    if 'mixed' in sentiments or ('positive' in sentiments and 'negative' in sentiments):
        sentiment = 'mixed'
    elif sentiments:
        sentiment = Counter(sentiments).most_common(1)[0][0]
    else:
        sentiment = 'neutral'

    meeting_type = Counter(meeting_types).most_common(1)[0][0] if meeting_types else 'general'

    return {
        'formatted_transcription': formatted_transcription,
        'summary': summary,
        'sections': sections,
        'key_decisions': key_decisions,
        'action_items': action_items,
        'participants_identified': sorted(participants),
        'sentiment': sentiment,
        'meeting_type_detected': meeting_type,
        'tags': tags,
        'suggested_events': suggested_events,
        'risks': risks,
    }


def _generate_transcription_analysis(client, meeting_title, source_text, analysis_context=''):
    """Analisa transcrições longas com chunking e consolidação."""
    source_text = (source_text or '').strip()
    if len(source_text) <= 120000:
        return _generate_transcription_analysis_single(client, meeting_title, source_text, analysis_context=analysis_context)

    chunks = _split_text_for_analysis(source_text, max_chars=100000)
    analyses = []
    for chunk in chunks:
        try:
            analyses.append(
                _generate_transcription_analysis_single(client, meeting_title, chunk, analysis_context=analysis_context)
            )
        except Exception:
            analyses.append(
                {
                    'formatted_transcription': chunk,
                    'summary': '',
                    'sections': [],
                    'key_decisions': [],
                    'action_items': [],
                    'participants_identified': [],
                    'sentiment': 'neutral',
                    'meeting_type_detected': 'general',
                    'tags': [],
                    'suggested_events': [],
                }
            )

    return _merge_transcription_analyses(client, meeting_title, analyses, source_text)


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

    formatted_start = _format_event_datetime(event.start)
    _notify_agenda_user(
        request.user,
        'Evento marcado na agenda',
        f'Você marcou "{event.title}" para {formatted_start}.',
        action_url='/agenda/',
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
            _notify_agenda_user(
                user,
                'Convite para evento',
                f'{request.user.full_name} marcou "{event.title}" com você para {formatted_start}.',
                action_url='/agenda/',
            )

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
    old_start = event.start
    old_end = event.end

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
            _notify_agenda_user(
                user,
                'Convite para evento',
                f'{request.user.full_name} convidou você para "{event.title}".',
                action_url='/agenda/',
            )

    if event.start != old_start or event.end != old_end:
        participants_to_notify = event.participants.exclude(pk=request.user.pk)
        _notify_agenda_users(
            participants_to_notify,
            'Evento remarcado',
            f'{request.user.full_name} remarcou "{event.title}" para {_format_event_datetime(event.start)}.',
            action_url='/agenda/',
        )

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
        if invitation.event.owner_id != request.user.pk:
            _notify_agenda_user(
                invitation.event.owner,
                'Convite aceito',
                f'{request.user.full_name} aceitou o convite para "{invitation.event.title}".',
                action_url='/agenda/',
            )
        return JsonResponse({'ok': True, 'message': 'Convite aceito!'})
    elif action == 'reject':
        invitation.reject(notes)
        if invitation.event.owner_id != request.user.pk:
            _notify_agenda_user(
                invitation.event.owner,
                'Convite recusado',
                f'{request.user.full_name} recusou o convite para "{invitation.event.title}".',
                action_url='/agenda/',
            )
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
            meeting_request = MeetingRequest.objects.create(
                requester=request.user,
                target=target,
                title=title,
                description=description,
                meeting_type=meeting_type,
                proposed_start=proposed_start,
                proposed_end=proposed_end,
                location=location,
            )
            _notify_agenda_user(
                target,
                'Nova solicitação de reunião',
                f'{request.user.full_name} solicitou "{meeting_request.title}" para {_format_event_datetime(meeting_request.proposed_start)}.',
                action_url='/agenda/solicitacoes/?tab=received',
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
    _notify_agenda_user(
        mr.requester,
        'Solicitação aceita',
        f'{request.user.full_name} aceitou "{mr.title}". Reunião marcada para {_format_event_datetime(mr.proposed_start)}.',
        action_url='/agenda/solicitacoes/?tab=sent',
    )
    messages.success(request, f'Reunião "{mr.title}" aceita! Evento adicionado à sua agenda.')
    return redirect('agenda:meeting_requests')


@login_required
@require_POST
def meeting_request_reject(request, pk):
    """Recusar solicitação de reunião"""
    mr = get_object_or_404(MeetingRequest, pk=pk, target=request.user, status='pending')
    notes = request.POST.get('response_notes', '')
    mr.reject(notes)
    _notify_agenda_user(
        mr.requester,
        'Solicitação recusada',
        f'{request.user.full_name} recusou "{mr.title}".',
        action_url='/agenda/solicitacoes/?tab=sent',
    )
    messages.success(request, f'Solicitação de reunião "{mr.title}" recusada.')
    return redirect('agenda:meeting_requests')


@login_required
@require_POST
def meeting_request_cancel(request, pk):
    """Cancelar solicitação enviada"""
    mr = get_object_or_404(MeetingRequest, pk=pk, requester=request.user, status='pending')
    mr.cancel()
    _notify_agenda_user(
        mr.target,
        'Solicitação cancelada',
        f'{request.user.full_name} cancelou a solicitação "{mr.title}".',
        action_url='/agenda/solicitacoes/?tab=received',
    )
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
    """Recebe áudio, salva com segurança e agenda processamento assíncrono."""
    from django.conf import settings as django_settings

    audio_file = request.FILES.get('audio')
    title = request.POST.get('title', '').strip() or 'Reunião sem título'
    event_id = request.POST.get('event_id')
    participant_roles = _parse_participant_roles(request.POST.get('participant_roles'))

    try:
        duration_seconds = int(request.POST.get('duration_seconds', 0) or 0)
    except (TypeError, ValueError):
        duration_seconds = 0

    if not audio_file:
        return JsonResponse({'error': 'Nenhum arquivo de áudio enviado.'}, status=400)

    api_key = getattr(django_settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'Chave da API OpenAI não configurada. Configure OPENAI_API_KEY no .env'}, status=500)

    temp_audio_path = _save_uploaded_audio_to_temp(audio_file)

    event = None
    if event_id:
        try:
            event = CalendarEvent.objects.get(pk=event_id, owner=request.user)
        except CalendarEvent.DoesNotExist:
            pass

    # O arquivo é persistido no storage (S3 quando USE_S3=True) antes do processamento.
    transcription = MeetingTranscription.objects.create(
        owner=request.user,
        event=event,
        title=title,
        duration_seconds=duration_seconds,
        participant_roles=participant_roles,
        status='processing',
    )

    _start_transcription_background_job(
        transcription_id=transcription.pk,
        api_key=api_key,
        mode='upload',
        options={
            'temp_audio_path': temp_audio_path,
            'original_audio_name': getattr(audio_file, 'name', '') or 'audio.webm',
        },
    )

    return JsonResponse({
        'id': transcription.pk,
        'status': 'processing',
        'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        'message': 'Áudio recebido e processamento iniciado em segundo plano.',
    }, status=202)


def _save_uploaded_audio_to_temp(uploaded_file):
    """Copia um upload recebido na request para um arquivo temporário local."""
    suffix = '.webm'
    if getattr(uploaded_file, 'name', None):
        _, ext = os.path.splitext(uploaded_file.name)
        if ext:
            suffix = ext

    tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        for chunk in uploaded_file.chunks():
            tmp_file.write(chunk)
    finally:
        tmp_file.close()

    return tmp_file.name


def _get_transcription_upload_temp_base():
    base_dir = os.path.join(tempfile.gettempdir(), 'agenda_transcription_uploads')
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def _sanitize_upload_id(value):
    upload_id = (value or '').strip()
    if not upload_id or not re.match(r'^[a-zA-Z0-9_-]{8,}$', upload_id):
        return ''
    return upload_id


def _get_upload_dir(upload_id):
    return os.path.join(_get_transcription_upload_temp_base(), upload_id)


@login_required
@require_POST
def api_transcription_upload_chunk(request):
    """Recebe um chunk de áudio para uploads grandes."""
    upload_id = _sanitize_upload_id(request.POST.get('upload_id'))
    if not upload_id:
        return JsonResponse({'error': 'upload_id inválido.'}, status=400)

    try:
        chunk_index = int(request.POST.get('chunk_index', -1))
        total_chunks = int(request.POST.get('total_chunks', 0))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Índices de chunk inválidos.'}, status=400)

    if chunk_index < 0:
        return JsonResponse({'error': 'Chunk fora do intervalo.'}, status=400)

    if total_chunks > 0 and chunk_index >= total_chunks:
        return JsonResponse({'error': 'Chunk fora do intervalo.'}, status=400)

    audio_chunk = request.FILES.get('audio')
    if not audio_chunk:
        return JsonResponse({'error': 'Nenhum chunk enviado.'}, status=400)

    upload_dir = _get_upload_dir(upload_id)
    os.makedirs(upload_dir, exist_ok=True)
    part_path = os.path.join(upload_dir, f'part_{chunk_index:06d}')
    tmp_path = part_path + '.tmp'

    # Escrita atômica: grava em .tmp e renomeia ao final para evitar partes corrompidas
    # caso o cliente reenvie o mesmo índice após uma falha de rede.
    try:
        with open(tmp_path, 'wb') as target:
            for data in audio_chunk.chunks():
                target.write(data)
            target.flush()
            try:
                os.fsync(target.fileno())
            except OSError:
                pass
        os.replace(tmp_path, part_path)
    except Exception as exc:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return JsonResponse({'error': f'Falha ao salvar parte: {exc}'}, status=500)

    return JsonResponse({
        'upload_id': upload_id,
        'chunk_index': chunk_index,
        'received': True,
        'size': os.path.getsize(part_path),
    })


@login_required
@require_POST
def api_transcription_upload_finalize(request):
    """Finaliza upload chunked, monta o arquivo e inicia transcrição."""
    from django.conf import settings as django_settings

    upload_id = _sanitize_upload_id(request.POST.get('upload_id'))
    if not upload_id:
        return JsonResponse({'error': 'upload_id inválido.'}, status=400)

    try:
        total_chunks = int(request.POST.get('total_chunks', 0))
    except (TypeError, ValueError):
        total_chunks = 0

    if total_chunks <= 0:
        return JsonResponse({'error': 'total_chunks inválido.'}, status=400)

    title = request.POST.get('title', '').strip() or 'Reunião sem título'
    event_id = request.POST.get('event_id')
    participant_roles = _parse_participant_roles(request.POST.get('participant_roles'))

    try:
        duration_seconds = int(request.POST.get('duration_seconds', 0) or 0)
    except (TypeError, ValueError):
        duration_seconds = 0

    api_key = getattr(django_settings, 'OPENAI_API_KEY', '')
    if not api_key:
        return JsonResponse({'error': 'Chave da API OpenAI não configurada. Configure OPENAI_API_KEY no .env'}, status=500)

    upload_dir = _get_upload_dir(upload_id)
    if not os.path.isdir(upload_dir):
        return JsonResponse({'error': 'Upload não encontrado.'}, status=404)

    parts = sorted(
        [
            name for name in os.listdir(upload_dir)
            if name.startswith('part_')
        ]
    )

    if not parts:
        return JsonResponse({'error': 'Nenhuma parte do áudio foi recebida. Tente gravar novamente.'}, status=400)

    # Tolerância: se faltarem algumas partes (ex.: queda momentânea de rede),
    # processamos o que está disponível em vez de descartar todo o áudio.
    missing_chunks = 0
    if total_chunks > 0 and len(parts) < total_chunks:
        missing_chunks = total_chunks - len(parts)

    original_audio_name = request.POST.get('original_audio_name', '') or 'audio.webm'
    _, ext = os.path.splitext(original_audio_name)
    suffix = ext or '.webm'

    temp_output = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    temp_path = temp_output.name
    try:
        for part in parts:
            part_path = os.path.join(upload_dir, part)
            with open(part_path, 'rb') as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    temp_output.write(chunk)
    finally:
        temp_output.close()

    for part in parts:
        try:
            os.unlink(os.path.join(upload_dir, part))
        except OSError:
            pass
    try:
        os.rmdir(upload_dir)
    except OSError:
        pass

    event = None
    if event_id:
        try:
            event = CalendarEvent.objects.get(pk=event_id, owner=request.user)
        except CalendarEvent.DoesNotExist:
            pass

    transcription = MeetingTranscription.objects.create(
        owner=request.user,
        event=event,
        title=title,
        duration_seconds=duration_seconds,
        participant_roles=participant_roles,
        status='processing',
    )

    _start_transcription_background_job(
        transcription_id=transcription.pk,
        api_key=api_key,
        mode='upload',
        options={
            'temp_audio_path': temp_path,
            'original_audio_name': original_audio_name,
        },
    )

    return JsonResponse({
        'id': transcription.pk,
        'status': 'processing',
        'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        'message': 'Upload concluído e transcrição iniciada em segundo plano.',
        'missing_chunks': missing_chunks,
        'parts_received': len(parts),
    }, status=202)


def _copy_storage_file_to_temp(field_file):
    """Copia um FieldFile para arquivo temporário local sem carregar tudo em memória."""
    suffix = '.webm'
    if getattr(field_file, 'name', None):
        _, ext = os.path.splitext(field_file.name)
        if ext:
            suffix = ext

    tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = tmp_file.name
    try:
        field_file.open('rb')
        while True:
            chunk = field_file.read(1024 * 1024)
            if not chunk:
                break
            tmp_file.write(chunk)
    finally:
        try:
            field_file.close()
        except Exception:
            pass
        tmp_file.close()

    return tmp_path


def _convert_to_mp3(input_path):
    """Converte áudio para mp3 (mono/16kHz) usando ffmpeg e retorna o path gerado."""
    base_name, _ = os.path.splitext(input_path)
    output_path = f'{base_name}_normalized.mp3'

    try:
        subprocess.run(
            [
                'ffmpeg', '-y', '-i', input_path, '-vn', '-acodec', 'libmp3lame',
                '-ab', '64k', '-ar', '16000', '-ac', '1', output_path,
            ],
            capture_output=True,
            timeout=1800,  # Aumentado para 30min (era 900)
            check=True,
        )
        return output_path
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        if os.path.exists(output_path):
            os.unlink(output_path)
        return None


def _extract_whisper_text_and_duration(whisper_response):
    """Extrai texto e duração de respostas do Whisper."""
    duration = None
    if hasattr(whisper_response, 'text'):
        raw_text = whisper_response.text or ''
        if hasattr(whisper_response, 'duration') and whisper_response.duration:
            try:
                duration = int(whisper_response.duration)
            except (TypeError, ValueError):
                duration = None
    else:
        raw_text = str(whisper_response)
    return raw_text.strip(), duration


def _transcribe_audio_from_storage(client, field_file):
    """Transcreve um áudio armazenado no storage (S3/local) com uso seguro de memória."""
    temp_source_path = _copy_storage_file_to_temp(field_file)
    original_name = os.path.basename(getattr(field_file, 'name', '') or 'audio.webm')

    try:
        return _transcribe_audio_path(client, temp_source_path, original_name)
    finally:
        if os.path.exists(temp_source_path):
            os.unlink(temp_source_path)


def _probe_audio_duration_seconds(file_path):
    """Obtém duração do áudio via ffprobe (em segundos)."""
    try:
        probe_result = subprocess.run(
            [
                'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1', file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float((probe_result.stdout or '').strip())
    except Exception:
        return None


def _transcribe_audio_path(client, source_path, original_filename):
    """Converte para mp3 quando possível, divide em partes se necessário e transcreve com retry."""
    whisper_max_size = 24 * 1024 * 1024  # 24MB
    long_audio_seconds = 1500  # acima de ~25min, segmentar para mais robustez

    duration_seconds = _probe_audio_duration_seconds(source_path)
    if duration_seconds and duration_seconds >= long_audio_seconds:
        try:
            raw_text = _split_and_transcribe(client, source_path)
            return raw_text, int(duration_seconds)
        except Exception:
            # Se falhar a segmentacao, tenta o fluxo tradicional
            pass

    # Primeiro, tenta sem conversão se for pequeno o suficiente
    try:
        source_size = os.path.getsize(source_path)
        if source_size <= whisper_max_size:
            return _try_transcribe_file(client, source_path, is_converted=False, max_retries=2)
    except Exception:
        pass

    # Para arquivos grandes, tenta converter para MP3 (menor, mais robusto)
    mp3_path = _convert_to_mp3(source_path)
    if mp3_path:
        try:
            mp3_size = os.path.getsize(mp3_path)
            if mp3_size <= whisper_max_size:
                # Tentativa com retry para upload único
                return _try_transcribe_file(client, mp3_path, is_converted=True, max_retries=3)

            # Se ainda for grande, divide em segmentos
            raw_text = _split_and_transcribe(client, mp3_path)
            return raw_text, duration_seconds
        finally:
            if os.path.exists(mp3_path):
                os.unlink(mp3_path)

    # Fallback: divide o original se for muito grande
    try:
        if os.path.getsize(source_path) > whisper_max_size:
            # Tenta dividir o arquivo original sem converter
            raw_text = _split_and_transcribe_raw(client, source_path)
            return raw_text, duration_seconds
    except Exception:
        pass

    # Último recurso: tenta enviar o original direto com retry
    return _try_transcribe_file(client, source_path, is_converted=False, max_retries=3)


def _try_transcribe_file(client, file_path, is_converted=False, max_retries=2, response_format='verbose_json'):
    """Tenta transcrever um arquivo com retry automático em caso de erro."""
    for attempt in range(max_retries):
        try:
            with open(file_path, 'rb') as audio_stream:
                payload = {
                    'model': 'whisper-1',
                    'file': audio_stream,
                    'language': 'pt',
                    'response_format': response_format,
                }
                if response_format == 'verbose_json':
                    payload['timestamp_granularities'] = ['segment']

                # Retries internos do Whisper com backoff
                whisper_response = client.audio.transcriptions.create(**payload)
            return _extract_whisper_text_and_duration(whisper_response)
        except Exception as e:
            if attempt < max_retries - 1:
                import time
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                time.sleep(wait_time)
                continue
            # Último attempt falhou, re-raise
            raise


def _split_and_transcribe(client, mp3_path):
    """Divide áudio grande em segmentos de tempo e concatena as transcrições com retry."""
    total_duration = _probe_audio_duration_seconds(mp3_path) or 3600

    if total_duration >= 6 * 3600:
        segment_seconds = 900
    else:
        segment_seconds = 720

    tmp_dir = tempfile.mkdtemp()
    all_text_parts = []

    try:
        start = 0.0
        segment_index = 0
        while start < total_duration:
            seg_duration = min(segment_seconds, max(1, total_duration - start))
            seg_path = os.path.join(tmp_dir, f'seg_{segment_index:04d}.mp3')
            timeout = max(300, int(seg_duration * 3))

            try:
                subprocess.run(
                    [
                        'ffmpeg', '-y', '-ss', str(start), '-t', str(seg_duration), '-i', mp3_path,
                        '-vn', '-acodec', 'libmp3lame', '-ab', '64k', '-ar', '16000', '-ac', '1', seg_path,
                    ],
                    capture_output=True,
                    timeout=timeout,
                    check=True,
                )

                text, _ = _try_transcribe_file(
                    client,
                    seg_path,
                    is_converted=True,
                    max_retries=3,
                    response_format='text',
                )
                if text:
                    all_text_parts.append(text.strip())
            except Exception as err:
                all_text_parts.append(f'[Segmento não transcrito: {str(err)[:80]}]')
            finally:
                if os.path.exists(seg_path):
                    os.unlink(seg_path)

            start += seg_duration
            segment_index += 1

        return '\n\n'.join([p for p in all_text_parts if p]).strip()
    finally:
        for name in os.listdir(tmp_dir):
            file_path = os.path.join(tmp_dir, name)
            if os.path.isfile(file_path):
                os.unlink(file_path)
        os.rmdir(tmp_dir)


def _split_and_transcribe_raw(client, source_path):
    """Divide arquivo de áudio SEM converter para MP3, segmentando por tamanho (backup)."""
    whisper_max_size = 24 * 1024 * 1024  # 24MB
    file_size = os.path.getsize(source_path)
    
    # Calcula quantos chunks são necessários
    num_chunks = (file_size // whisper_max_size) + 1
    if num_chunks <= 1:
        return _try_transcribe_file(client, source_path, is_converted=False, max_retries=2)[0]
    
    # Divide o arquivo em chunks
    tmp_dir = tempfile.mkdtemp()
    chunk_size = file_size // num_chunks + 1
    all_text_parts = []
    
    try:
        chunk_index = 0
        with open(source_path, 'rb') as f:
            while True:
                chunk_data = f.read(chunk_size)
                if not chunk_data:
                    break
                
                chunk_path = os.path.join(tmp_dir, f'chunk_{chunk_index:03d}.webm')
                with open(chunk_path, 'wb') as chunk_file:
                    chunk_file.write(chunk_data)
                
                # Transcreve cada chunk
                for attempt in range(2):
                    try:
                        with open(chunk_path, 'rb') as audio_stream:
                            whisper_response = client.audio.transcriptions.create(
                                model='whisper-1',
                                file=audio_stream,
                                language='pt',
                                response_format='text',
                            )
                        text = whisper_response if isinstance(whisper_response, str) else str(whisper_response)
                        if text and text.strip():
                            all_text_parts.append(text.strip())
                        break
                    except Exception:
                        if attempt < 1:
                            import time
                            time.sleep(2)
                        else:
                            all_text_parts.append(f'[Chunk {chunk_index} não processado]')
                
                chunk_index += 1
        
        return '\n\n'.join(all_text_parts).strip()
    finally:
        for name in os.listdir(tmp_dir):
            file_path = os.path.join(tmp_dir, name)
            if os.path.isfile(file_path):
                os.unlink(file_path)
        os.rmdir(tmp_dir)


def _ensure_transcription_calendar_event(transcription, user):
    """Cria evento de rastreabilidade da transcrição caso ainda não exista."""
    if transcription.calendar_event_created_id:
        return

    now = timezone.now()
    duration = max(transcription.duration_seconds or 60, 60)
    summary_preview = (transcription.summary or '').strip()

    cal_event = CalendarEvent.objects.create(
        owner=user,
        title=f'📝 Transcrição: {transcription.title}',
        description=(
            'Transcrição de reunião processada com IA.\n\n'
            f'📋 Resumo: {summary_preview[:200]}...\n'
            f'✅ Itens de ação: {len(transcription.action_items or [])}\n'
            f'🎯 Decisões: {len(transcription.key_decisions or [])}\n\n'
            f'Ver transcrição completa: /agenda/transcricoes/{transcription.pk}/'
        ),
        event_type='task',
        color='#ea580c',
        start=now - timedelta(seconds=duration),
        end=now,
        all_day=False,
    )

    transcription.calendar_event_created = cal_event
    transcription.save(update_fields=['calendar_event_created'])


def _process_transcription_upload_job(transcription_id, client, source_path=None, original_audio_name=None):
    """Pipeline principal de transcrição inicial."""
    transcription = MeetingTranscription.objects.select_related('owner').get(pk=transcription_id)
    temp_path = source_path

    if temp_path:
        original_name = original_audio_name or os.path.basename(temp_path) or 'audio.webm'
        with open(temp_path, 'rb') as audio_stream:
            transcription.audio_file.save(original_name, File(audio_stream), save=False)
        transcription.save(update_fields=['audio_file'])

    try:
        if temp_path:
            raw_text, duration = _transcribe_audio_path(
                client,
                temp_path,
                original_audio_name or os.path.basename(temp_path) or 'audio.webm',
            )
        else:
            if not transcription.audio_file:
                raise ValueError('Arquivo de áudio não encontrado para processamento.')

            raw_text, duration = _transcribe_audio_from_storage(client, transcription.audio_file)

        if not raw_text:
            raise ValueError('Falha ao transcrever áudio enviado.')

        if duration:
            transcription.duration_seconds = duration

        transcription.raw_transcription = raw_text

        analysis_context = _build_participant_roles_context(transcription.participant_roles)
        analysis = _generate_transcription_analysis(
            client,
            transcription.title,
            raw_text,
            analysis_context=analysis_context,
        )

        transcription.formatted_transcription = analysis['formatted_transcription']
        transcription.summary = analysis['summary']
        transcription.sections = analysis['sections']
        transcription.key_decisions = analysis['key_decisions']
        transcription.action_items = analysis['action_items']
        transcription.participants_identified = analysis['participants_identified']
        transcription.sentiment = analysis['sentiment']
        transcription.meeting_type_detected = analysis['meeting_type_detected']
        transcription.tags = analysis['tags']
        transcription.suggested_events = analysis['suggested_events']
        transcription.risks = analysis.get('risks', [])
        transcription.status = 'completed'
        transcription.error_message = ''
        transcription.save()

        _ensure_transcription_calendar_event(transcription, transcription.owner)
        transcription.tasks_created.clear()
        _create_tasks_from_transcription(transcription, transcription.owner)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _process_transcription_reprocess_job(transcription_id, client, options=None):
    """Pipeline de reprocessamento, priorizando texto salvo e fallback para áudio."""
    options = options or {}
    transcription = MeetingTranscription.objects.select_related('owner').get(pk=transcription_id)

    force_raw = bool(options.get('force_raw'))
    provided_raw_text = (options.get('raw_text') or '').strip()

    source_text = (transcription.raw_transcription or '').strip()
    formatted_text = (transcription.formatted_transcription or '').strip()

    if provided_raw_text:
        source_text = provided_raw_text
        transcription.raw_transcription = provided_raw_text
        transcription.save(update_fields=['raw_transcription'])

    if not source_text and formatted_text:
        source_text = formatted_text
        transcription.raw_transcription = formatted_text
        transcription.save(update_fields=['raw_transcription'])

    if force_raw and not source_text:
        raise ValueError('Não há texto bruto para retranscrever.')

    if not source_text and transcription.audio_file:
        source_text, duration = _transcribe_audio_from_storage(client, transcription.audio_file)
        if duration:
            transcription.duration_seconds = duration
        if source_text:
            transcription.raw_transcription = source_text
            transcription.save(update_fields=['raw_transcription', 'duration_seconds'])

    if not source_text:
        raise ValueError('Sem transcrição bruta/completa ou áudio para processar.')

    analysis_context = _build_participant_roles_context(transcription.participant_roles)
    analysis = _generate_transcription_analysis(
        client,
        transcription.title,
        source_text,
        analysis_context=analysis_context,
    )

    transcription.formatted_transcription = analysis['formatted_transcription']
    if not transcription.raw_transcription:
        transcription.raw_transcription = source_text
    transcription.summary = analysis['summary']
    transcription.sections = analysis['sections']
    transcription.key_decisions = analysis['key_decisions']
    transcription.action_items = analysis['action_items']
    transcription.participants_identified = analysis['participants_identified']
    transcription.sentiment = analysis['sentiment']
    transcription.meeting_type_detected = analysis['meeting_type_detected']
    transcription.tags = analysis['tags']
    transcription.suggested_events = analysis['suggested_events']
    transcription.risks = analysis.get('risks', [])
    transcription.status = 'completed'
    transcription.error_message = ''
    transcription.save()

    _ensure_transcription_calendar_event(transcription, transcription.owner)
    transcription.tasks_created.clear()
    _create_tasks_from_transcription(transcription, transcription.owner)


def _run_transcription_background_job(transcription_id, api_key, mode, options=None):
    """Worker thread para processar transcrição sem depender da conexão HTTP."""
    import openai

    close_old_connections()
    options = options or {}
    source_text = ''

    try:
        transcription = MeetingTranscription.objects.get(pk=transcription_id)
    except MeetingTranscription.DoesNotExist:
        close_old_connections()
        return

    try:
        if mode == 'upload' and not api_key:
            raise ValueError('OPENAI_API_KEY não configurada para transcrição.')

        if mode == 'reprocess' and not api_key:
            source_text = (transcription.raw_transcription or '').strip() or (transcription.formatted_transcription or '').strip()
            if not source_text:
                raise ValueError('Chave da API OpenAI não configurada.')

            transcription.formatted_transcription = transcription.formatted_transcription or source_text
            transcription.summary = (
                transcription.summary
                or 'Reprocessado usando a transcrição já salva. Configure OPENAI_API_KEY para análise avançada.'
            )
            transcription.status = 'completed'
            transcription.error_message = ''
            transcription.save(update_fields=['formatted_transcription', 'summary', 'status', 'error_message'])
            return

        client = openai.OpenAI(api_key=api_key)

        if mode == 'upload':
            _process_transcription_upload_job(
                transcription_id,
                client,
                source_path=options.get('temp_audio_path'),
                original_audio_name=options.get('original_audio_name'),
            )
        else:
            _process_transcription_reprocess_job(transcription_id, client, options=options)

    except Exception as err:
        transcription = MeetingTranscription.objects.filter(pk=transcription_id).first()
        if transcription:
            source_text = (transcription.raw_transcription or '').strip() or (transcription.formatted_transcription or '').strip()
            error_msg = _friendly_openai_error(err)[:500]

            # Sempre marcar como erro para liberar o "Reiniciar Processamento" e
            # exibir a mensagem amigável na tela do usuário. A transcrição bruta
            # continua disponível para nova tentativa.
            transcription.status = 'error'
            transcription.error_message = error_msg
            if source_text and not transcription.formatted_transcription:
                transcription.formatted_transcription = source_text
                transcription.save(update_fields=['status', 'error_message', 'formatted_transcription'])
            else:
                transcription.save(update_fields=['status', 'error_message'])
    finally:
        close_old_connections()


def _start_transcription_background_job(transcription_id, api_key, mode='upload', options=None):
    """Dispara uma thread para processamento de transcrição em segundo plano."""
    worker = threading.Thread(
        target=_run_transcription_background_job,
        kwargs={
            'transcription_id': transcription_id,
            'api_key': api_key,
            'mode': mode,
            'options': options or {},
        },
        daemon=True,
        name=f'transcription-{mode}-{transcription_id}',
    )
    worker.start()


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
def api_transcription_status(request, pk):
    """Retorna status resumido da transcrição para polling da interface."""
    transcription = get_object_or_404(MeetingTranscription, pk=pk, owner=request.user)

    # Detecta "Processando" travado: sem updates há mais de 3 minutos.
    is_stale = False
    stale_seconds = 0
    if transcription.status == 'processing' and transcription.updated_at:
        stale_seconds = int((timezone.now() - transcription.updated_at).total_seconds())
        is_stale = stale_seconds >= 180

    return JsonResponse({
        'id': transcription.pk,
        'status': transcription.status,
        'error_message': transcription.error_message,
        'updated_at': transcription.updated_at.isoformat(),
        'is_stale': is_stale,
        'stale_seconds': stale_seconds,
        'redirect': f'/agenda/transcricoes/{transcription.pk}/',
    })


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

    _notify_agenda_user(
        request.user,
        'Evento marcado na agenda',
        f'O evento "{event.title}" foi marcado a partir da transcrição "{transcription.title}".',
        action_url='/agenda/',
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
    """Reinicia processamento da transcrição em segundo plano."""
    from django.conf import settings as django_settings

    transcription = get_object_or_404(MeetingTranscription, pk=pk, owner=request.user)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}') if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}

    provided_raw_text = (payload.get('raw_text') or '').strip()

    transcription.status = 'processing'
    transcription.error_message = ''
    update_fields = ['status', 'error_message']

    if provided_raw_text:
        transcription.raw_transcription = provided_raw_text
        update_fields.append('raw_transcription')

    transcription.save(update_fields=update_fields)

    api_key = getattr(django_settings, 'OPENAI_API_KEY', '')
    _start_transcription_background_job(
        transcription_id=transcription.pk,
        api_key=api_key,
        mode='reprocess',
        options=payload,
    )

    return JsonResponse({
        'ok': True,
        'status': 'processing',
        'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        'message': 'Reprocessamento iniciado em segundo plano.',
    }, status=202)
