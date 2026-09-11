import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from datetime import datetime, timedelta, time, date as date_type

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Case, F, IntegerField, Q, Value, When
from django.db.models.functions import Greatest
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import User, Sector
from core.storage import get_media_storage
from .models import CalendarEvent, MeetingRequest, EventParticipant, MeetingTranscription

try:
    from core.models import NotificationMixin
except ImportError:
    NotificationMixin = None

try:
    from notifications.push_utils import send_push_notification_to_user
except ImportError:
    send_push_notification_to_user = None


logger = logging.getLogger('agenda.transcricao')


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


def login_required_json(view_func):
    """Como @login_required, mas para as APIs que o gravador chama por fetch.

    O @login_required redireciona para a tela de login; o fetch seguia o
    redirecionamento, recebia a página de login com status 200 e contava o
    pedaço de áudio como salvo — perda silenciosa justamente na gravação longa
    em que a sessão expirou. Aqui a resposta é um 401 em JSON.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({
                'error': ('Sua sessão no portal expirou. Entre de novo (pode ser em outra aba) — '
                          'a gravação continua guardada neste computador.'),
                'sessao_expirada': True,
            }, status=401)
        return view_func(request, *args, **kwargs)
    return _wrapped


def _is_superadmin(user):
    return getattr(user, 'hierarchy', None) == 'SUPERADMIN' or getattr(user, 'is_superuser', False)


def _visible_transcriptions_for_user(user):
    queryset = MeetingTranscription.objects.all()
    if _is_superadmin(user):
        return queryset
    return queryset.filter(Q(owner=user) | Q(shared_with=user)).distinct()


def _can_reprocess_transcription(user, transcription):
    return transcription.owner_id == user.id or _is_superadmin(user)


def _manageable_transcriptions_for_user(user):
    if _is_superadmin(user):
        return MeetingTranscription.objects.all()
    return MeetingTranscription.objects.filter(owner=user)


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

def grupos_para_convite(usuario):
    """Grupos de comunicação (/users/manage/groups/) prontos para virar convite.

    Cada grupo já vem com os ids dos membros que de fato serão convidados: só
    gente ativa e sem quem está criando o evento — quem cria não se convida, e
    contar essa pessoa faria o "N pessoas" da tela mentir.

    Grupo que sobra vazio depois desse corte não aparece: um botão que não
    convida ninguém só gera dúvida.
    """
    from communications.models import CommunicationGroup

    grupos = (CommunicationGroup.objects
              .filter(is_active=True)
              .prefetch_related('members')
              .order_by('name'))

    saida = []
    for grupo in grupos:
        ids = [m.pk for m in grupo.members.all()
               if m.is_active and m.pk != usuario.pk]
        if ids:
            saida.append({'id': grupo.pk, 'nome': grupo.name, 'membros': ids})
    return saida


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
        'grupos_convite': grupos_para_convite(request.user),
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


def _tarefa_do_evento(event, invited_users):
    """Cria (ou atualiza) a tarefa de verdade de um evento do tipo Tarefa."""
    try:
        from agenda.tarefas import precisa_de_tarefa, tarefa_para_evento

        if not precisa_de_tarefa(event):
            return None
        return tarefa_para_evento(event, invited_users, autor=event.owner)
    except Exception:                                   # módulo indisponível
        return None


def _sala_do_evento(event, invited_users):
    """Cria (ou atualiza) a sala de vídeo de um evento do tipo Chamada."""
    try:
        from reunioes.servicos import precisa_de_sala, sala_para_evento

        if not precisa_de_sala(event):
            return None
        return sala_para_evento(event, invited_users, autor=event.owner)
    except Exception:                                   # módulo indisponível
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
        # Sala própria por ocorrência: uma sala só para a série inteira faria a
        # reunião da semana que vem cair na mesma conversa da anterior.
        _sala_do_evento(child, invited_users)
        # Tarefa própria por ocorrência, pela mesma razão: "toda segunda" são
        # várias tarefas, uma para cada segunda, cada uma com seu status.
        _tarefa_do_evento(child, invited_users)
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

    # Chamada ganha sala de vídeo do portal na hora, com o link já pronto.
    sala = _sala_do_evento(event, invited_users)
    # Tarefa vira tarefa de verdade: aparece em /users/tasks/ com status, chat,
    # anexos e subtarefas, em vez de ser só um compromisso parecido com uma.
    tarefa = _tarefa_do_evento(event, invited_users)

    # Gerar ocorrências recorrentes (semanal)
    if event.recurrence_rule == 'weekly':
        _create_weekly_occurrences(event, invited_users)

    return JsonResponse({
        'id': event.pk,
        'title': event.title,
        'start': event.start.isoformat(),
        'end': event.end.isoformat(),
        'color': event.color,
        'link': event.link,
        'sala': sala,
        'tarefa_id': tarefa.id if tarefa else None,
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

    sala = _sala_do_evento(event, list(event.participants.all()))
    _tarefa_do_evento(event, list(event.participants.all()))

    return JsonResponse({'ok': True, 'link': event.link, 'sala': sala})


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
    """Lista transcrições visíveis para o usuário."""
    from . import processamento

    processamento.garantir_varredura()
    transcriptions = (
        _visible_transcriptions_for_user(request.user)
        .select_related('event', 'owner')
        .annotate(
            status_priority=Case(
                When(status='processing', then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .distinct()
        .order_by('status_priority', '-created_at')
    )

    context = {
        'transcriptions': transcriptions,
        'is_superadmin': _is_superadmin(request.user),
    }
    return render(request, 'agenda/transcription_list.html', context)


@login_required
def transcription_new(request):
    """Página para iniciar nova transcrição (gravar áudio ou upload)"""
    context = {
        'event': _evento_para_transcricao(request.user, request.GET.get('event_id')),
    }
    return render(request, 'agenda/transcription_new.html', context)


@login_required_json
@require_POST
def api_transcription_upload(request):
    """Recebe um áudio inteiro (arquivos pequenos), guarda no storage e agenda o processamento.

    O áudio vai para o storage ANTES da resposta. Antes ficava num temporário
    do worker e só subia dentro do job: se o job morresse no caminho, o arquivo
    ia junto e a transcrição virava erro sem volta.
    """
    from django.conf import settings as django_settings
    from . import processamento

    audio_file = request.FILES.get('audio')
    title = request.POST.get('title', '').strip() or 'Reunião sem título'
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

    transcription = MeetingTranscription(
        owner=request.user,
        event=_evento_para_transcricao(request.user, request.POST.get('event_id')),
        title=title[:255],
        duration_seconds=duration_seconds,
        participant_roles=participant_roles,
        status='processing',
        origem='arquivo',
    )
    nome = os.path.basename(getattr(audio_file, 'name', '') or '') or 'audio.webm'
    try:
        transcription.audio_file.save(nome, audio_file, save=False)
    except Exception as exc:                                        # noqa: BLE001
        logger.error('Áudio enviado não foi guardado no storage: %s', exc)
        return JsonResponse({'error': 'Não foi possível guardar o áudio agora. Tente de novo em instantes.'},
                            status=503)
    transcription.save()

    processamento.iniciar_job(transcription.pk, modo='upload')

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


# Prefixo (dentro do storage de mídia) onde ficam os pedaços brutos de cada
# gravação. Com USE_S3=True isso vira o MinIO; caso contrário, o MEDIA_ROOT local.
TRANSCRIPTION_PARTS_PREFIX = 'transcriptions/parts'


def _sanitize_upload_id(value):
    upload_id = (value or '').strip()
    if not upload_id or not re.match(r'^[a-zA-Z0-9_-]{8,80}$', upload_id):
        return ''
    return upload_id


def _get_transcription_parts_storage():
    """Storage dos pedaços — o mesmo do audio_file (MinIO quando USE_S3).

    Com sobrescrita ligada: a mesma parte reenviada substitui a anterior, em vez
    de ganhar um nome com sufixo que a montagem ignoraria.
    """
    storage = get_media_storage() or default_storage
    if hasattr(storage, 'file_overwrite'):
        storage.file_overwrite = True
    return storage


def _transcription_parts_dir(upload_id):
    return f'{TRANSCRIPTION_PARTS_PREFIX}/{upload_id}'


def _transcription_part_name(upload_id, chunk_index, suffix='.webm'):
    suffix = suffix or '.webm'
    return f'{_transcription_parts_dir(upload_id)}/part_{int(chunk_index):06d}{suffix}'


_NOME_DE_PARTE = re.compile(r'^part_(\d{6})(\.[A-Za-z0-9]{1,8})?$')


def _list_transcription_parts(storage, upload_id, levantar=False):
    """Partes já persistidas no storage — UMA por índice, em ordem.

    Um reenvio concorrente da mesma parte podia virar `part_000003_AbCd.webm`
    ao lado da original, e a montagem colava o trecho duas vezes: só nomes
    exatos contam. Com `levantar=True` (no job), falha do storage sobe como erro
    e vira nova tentativa — antes virava "nenhuma parte" e erro definitivo.
    """
    directory = _transcription_parts_dir(upload_id)
    try:
        _dirs, files = storage.listdir(directory)
    except (FileNotFoundError, NotADirectoryError):
        return []
    except Exception:
        if levantar:
            raise
        return []
    por_indice = {}
    for name in files:
        achado = _NOME_DE_PARTE.match(name)
        if achado:
            por_indice.setdefault(int(achado.group(1)), name)
    return [f'{directory}/{por_indice[i]}' for i in sorted(por_indice)]


def _apagar_partes(upload_id):
    """Apaga do storage todas as partes de uma sessão de gravação."""
    upload_id = _sanitize_upload_id(upload_id)
    if not upload_id:
        return 0
    storage = _get_transcription_parts_storage()
    directory = _transcription_parts_dir(upload_id)
    try:
        _dirs, files = storage.listdir(directory)
    except Exception:
        return 0
    apagadas = 0
    for name in files:
        try:
            storage.delete(f'{directory}/{name}')
            apagadas += 1
        except Exception:
            pass
    return apagadas


def _assemble_transcription_parts_to_temp(upload_id, original_audio_name):
    """Baixa as partes do storage e concatena num arquivo temporário local.

    Retorna (temp_path, partes) — `partes` é o maior índice + 1, a mesma conta
    de `partes_recebidas` — ou (None, 0) se não houver partes. Feito por
    streaming (1MB por vez) para suportar gravações de muitas horas sem carregar
    tudo na memória. Falha do storage sobe como erro (vira nova tentativa).
    """
    storage = _get_transcription_parts_storage()
    parts = _list_transcription_parts(storage, upload_id, levantar=True)
    if not parts:
        return None, 0

    # A extensão importa: o Whisper descobre o formato pelo nome do arquivo, e o
    # gravador do Safari grava mp4, não webm.
    _, ext = os.path.splitext(original_audio_name or '')
    if not _SUFIXO_VALIDO.match((ext or '').lower()):
        _, ext = os.path.splitext(parts[0])
    suffix = ext.lower() if _SUFIXO_VALIDO.match((ext or '').lower()) else '.webm'

    tmp_file = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp_path = tmp_file.name
    try:
        for part_name in parts:
            source = storage.open(part_name, 'rb')
            try:
                while True:
                    data = source.read(1024 * 1024)
                    if not data:
                        break
                    tmp_file.write(data)
            finally:
                try:
                    source.close()
                except Exception:
                    pass
    except Exception:
        tmp_file.close()
        os.unlink(tmp_path)
        raise
    tmp_file.close()

    maior = int(_NOME_DE_PARTE.match(parts[-1].rsplit('/', 1)[-1]).group(1))
    return tmp_path, maior + 1


ORIGENS_VALIDAS = {'gravador', 'reuniao', 'arquivo'}
MAIOR_INDICE_DE_PARTE = 200000
_SUFIXO_VALIDO = re.compile(r'^\.[a-z0-9]{1,8}$')


def _evento_para_transcricao(user, event_id):
    """Evento da agenda ao qual a transcrição pode ser ligada, ou None.

    Antes só valia evento cujo dono fosse quem grava. Na sala de reunião quem
    grava a ata raramente é o dono do evento — a ata nascia solta. Vale o dono,
    quem foi convidado para o evento e quem está na reunião ligada a ele.
    """
    try:
        event_id = int(event_id)
    except (TypeError, ValueError):
        return None
    evento = CalendarEvent.objects.filter(pk=event_id).first()
    if evento is None:
        return None
    if evento.owner_id == user.id or _is_superadmin(user):
        return evento
    if EventParticipant.objects.filter(event=evento, user=user).exists():
        return evento
    try:
        from reunioes.models import Reuniao

        reuniao = Reuniao.objects.filter(evento=evento).first()
        if reuniao is not None and reuniao.pode_ver(user):
            return evento
    except Exception:                                               # noqa: BLE001
        pass
    return None


def _sessao_de_gravacao(request, upload_id, criar=True, so_dono=True):
    """(transcrição, resposta de erro) da sessão de gravação com esse upload_id.

    A sessão nasce no primeiro contato (iniciar ou primeira parte) com o dono
    gravado. Antes qualquer pessoa logada podia escrever partes em qualquer
    upload_id — e finalizar a gravação de outra pessoa.
    """
    transcricao = MeetingTranscription.objects.filter(upload_id=upload_id).first()
    if transcricao is not None:
        if transcricao.owner_id != request.user.id and (so_dono or not _is_superadmin(request.user)):
            return None, JsonResponse({'error': 'Esta gravação é de outra pessoa.'}, status=403)
        return transcricao, None
    if not criar:
        return None, None

    origem = (request.POST.get('origem') or '').strip()
    try:
        with transaction.atomic():
            transcricao = MeetingTranscription.objects.create(
                owner=request.user,
                upload_id=upload_id,
                status='recording',
                origem=origem if origem in ORIGENS_VALIDAS else 'gravador',
                title=(request.POST.get('title') or '').strip()[:255] or 'Gravação em andamento',
                participant_roles=_parse_participant_roles(request.POST.get('participant_roles')),
                event=_evento_para_transcricao(request.user, request.POST.get('event_id')),
            )
    except IntegrityError:
        # O "iniciar" e a primeira parte chegaram juntos: o outro criou primeiro.
        transcricao = MeetingTranscription.objects.filter(upload_id=upload_id).first()
        if transcricao is None or transcricao.owner_id != request.user.id:
            return None, JsonResponse({'error': 'Esta gravação é de outra pessoa.'}, status=403)
    return transcricao, None


@login_required_json
@require_POST
def api_gravacao_iniciar(request):
    """Abre a sessão de gravação no servidor antes do primeiro pedaço de áudio.

    Com a sessão aberta o portal sabe da gravação desde o primeiro segundo: se o
    navegador fechar, ela aparece nas pendências e o varredor a fecha com o que
    tiver chegado.
    """
    upload_id = _sanitize_upload_id(request.POST.get('upload_id'))
    if not upload_id:
        return JsonResponse({'error': 'upload_id inválido.'}, status=400)
    transcricao, erro = _sessao_de_gravacao(request, upload_id, criar=True)
    if erro:
        return erro
    return JsonResponse({
        'id': transcricao.pk,
        'upload_id': transcricao.upload_id,
        'status': transcricao.status,
        'partes_recebidas': transcricao.partes_recebidas,
    })


@login_required_json
@require_POST
def api_transcription_upload_chunk(request):
    """Recebe uma parte da gravação e a guarda no storage na hora."""
    upload_id = _sanitize_upload_id(request.POST.get('upload_id'))
    if not upload_id:
        return JsonResponse({'error': 'upload_id inválido.'}, status=400)

    try:
        chunk_index = int(request.POST.get('chunk_index', -1))
        total_chunks = int(request.POST.get('total_chunks', 0))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Índices de chunk inválidos.'}, status=400)

    if chunk_index < 0 or chunk_index > MAIOR_INDICE_DE_PARTE:
        return JsonResponse({'error': 'Chunk fora do intervalo.'}, status=400)

    if total_chunks > 0 and chunk_index >= total_chunks:
        return JsonResponse({'error': 'Chunk fora do intervalo.'}, status=400)

    audio_chunk = request.FILES.get('audio')
    if not audio_chunk:
        return JsonResponse({'error': 'Nenhum chunk enviado.'}, status=400)

    transcricao, erro = _sessao_de_gravacao(request, upload_id, criar=True)
    if erro:
        return erro

    _, ext = os.path.splitext(getattr(audio_chunk, 'name', '') or '')
    suffix = ext.lower() if _SUFIXO_VALIDO.match((ext or '').lower()) else '.webm'

    # Persiste a parte direto no storage de mídia (MinIO quando USE_S3), para que
    # gravações longas (horas) não fiquem só em disco efêmero e não se percam se
    # o processo/servidor reiniciar no meio.
    storage = _get_transcription_parts_storage()
    name = _transcription_part_name(upload_id, chunk_index, suffix)
    try:
        # Mesmo índice reenviado depois de uma falha de rede substitui a parte.
        if not getattr(storage, 'file_overwrite', False) and storage.exists(name):
            storage.delete(name)
        saved_name = storage.save(name, audio_chunk)
        try:
            size = storage.size(saved_name)
        except Exception:
            size = getattr(audio_chunk, 'size', 0)
    except Exception as exc:
        logger.warning('Parte %s de %s não foi guardada: %s', chunk_index, upload_id, exc)
        return JsonResponse({'error': 'Falha ao salvar a parte no armazenamento. Nova tentativa automática.'},
                            status=503)

    agora = timezone.now()
    campos = {
        'partes_recebidas': Greatest(F('partes_recebidas'), chunk_index + 1),
        'ultima_parte_em': agora,
        'updated_at': agora,
    }
    if transcricao.finalizada_automaticamente and transcricao.status != 'recording':
        # O portal tinha fechado a gravação porque o navegador sumiu, e ele
        # voltou com mais áudio: reabre. Um job em andamento percebe que perdeu
        # a transcrição e para; o processamento recomeça com o áudio completo.
        campos.update(status='recording', etapa='', processando_por='', batimento_em=None,
                      proxima_tentativa_em=None, finalizada_automaticamente=False)
    MeetingTranscription.objects.filter(pk=transcricao.pk).update(**campos)

    return JsonResponse({
        'upload_id': upload_id,
        'chunk_index': chunk_index,
        'received': True,
        'stored': saved_name,
        'size': size,
        'transcription_id': transcricao.pk,
    })


@login_required_json
@require_POST
def api_transcription_upload_finalize(request):
    """Encerra a gravação: confere as partes e coloca a transcrição para processar.

    Idempotente — clique repetido ou retomada depois de o navegador fechar não
    duplica transcrição nem processamento. Se chegaram partes depois de uma
    transcrição concluída, processa de novo com o áudio completo. A montagem do
    áudio NÃO é feita aqui (fica no job), para a resposta ser rápida mesmo com
    uma gravação de 24 h.
    """
    from django.conf import settings as django_settings
    from . import processamento

    upload_id = _sanitize_upload_id(request.POST.get('upload_id'))
    if not upload_id:
        return JsonResponse({'error': 'upload_id inválido.'}, status=400)

    try:
        total_chunks = int(request.POST.get('total_chunks', 0) or 0)
    except (TypeError, ValueError):
        total_chunks = 0

    title = (request.POST.get('title') or '').strip()
    participant_roles = _parse_participant_roles(request.POST.get('participant_roles'))
    try:
        duration_seconds = int(request.POST.get('duration_seconds', 0) or 0)
    except (TypeError, ValueError):
        duration_seconds = 0

    if not getattr(django_settings, 'OPENAI_API_KEY', ''):
        return JsonResponse({'error': 'Chave da API OpenAI não configurada. Configure OPENAI_API_KEY no .env'}, status=500)

    transcricao, erro = _sessao_de_gravacao(request, upload_id, criar=False, so_dono=False)
    if erro:
        return erro

    storage = _get_transcription_parts_storage()
    try:
        recebidas = len(_list_transcription_parts(storage, upload_id, levantar=True))
    except Exception as exc:                                        # noqa: BLE001
        # Storage fora do ar agora: se o banco registrou partes, segue — o job
        # junta o áudio depois, com novas tentativas.
        logger.warning('Partes de %s não listadas ao finalizar: %s', upload_id, exc)
        recebidas = transcricao.partes_recebidas if transcricao else 0

    if not recebidas:
        return JsonResponse({'error': 'Nenhuma parte do áudio foi encontrada no armazenamento. Tente gravar novamente.'}, status=400)

    if transcricao is None:
        transcricao = MeetingTranscription.objects.create(
            owner=request.user,
            upload_id=upload_id,
            origem='gravador',
            status='processing',
            etapa='montagem',
            title=title[:255] or 'Reunião sem título',
            duration_seconds=duration_seconds,
            participant_roles=participant_roles,
            event=_evento_para_transcricao(request.user, request.POST.get('event_id')),
            partes_recebidas=recebidas,
            ultima_parte_em=timezone.now(),
        )
    else:
        processadas = int((transcricao.progresso or {}).get('partes_processadas') or 0)
        maior = max(transcricao.partes_recebidas, recebidas)
        if transcricao.status == 'completed' and maior <= processadas:
            return JsonResponse({
                'id': transcricao.pk,
                'status': 'completed',
                'redirect': f'/agenda/transcricoes/{transcricao.pk}/',
                'message': 'Esta gravação já foi processada.',
                'missing_chunks': 0,
                'parts_received': recebidas,
            })

        campos = []
        if title:
            transcricao.title = title[:255]
            campos.append('title')
        if duration_seconds > (transcricao.duration_seconds or 0):
            transcricao.duration_seconds = duration_seconds
            campos.append('duration_seconds')
        if participant_roles:
            transcricao.participant_roles = participant_roles
            campos.append('participant_roles')
        evento = _evento_para_transcricao(request.user, request.POST.get('event_id'))
        if evento is not None and transcricao.event_id is None:
            transcricao.event = evento
            campos.append('event')
        if maior > transcricao.partes_recebidas:
            transcricao.partes_recebidas = maior
            campos.append('partes_recebidas')

        agora = timezone.now()
        processando_agora = (transcricao.status == 'processing' and transcricao.batimento_em is not None
                             and transcricao.batimento_em > agora - timedelta(seconds=processamento.PARADO_APOS_SEGUNDOS))
        if not processando_agora:
            transcricao.status = 'processing'
            transcricao.etapa = 'montagem'
            transcricao.error_message = ''
            transcricao.tentativas = 0
            transcricao.proxima_tentativa_em = None
            campos += ['status', 'etapa', 'error_message', 'tentativas', 'proxima_tentativa_em']
        if campos:
            transcricao.save(update_fields=list(dict.fromkeys(campos + ['updated_at'])))

    missing_chunks = max(0, total_chunks - recebidas) if total_chunks > 0 else 0
    iniciado = processamento.iniciar_job(transcricao.pk, modo='upload')

    return JsonResponse({
        'id': transcricao.pk,
        'status': 'processing',
        'redirect': f'/agenda/transcricoes/{transcricao.pk}/',
        'message': 'Upload concluído e transcrição iniciada em segundo plano.',
        'missing_chunks': missing_chunks,
        'parts_received': recebidas,
        'iniciado': iniciado,
    }, status=202)


@login_required_json
def api_transcricoes_pendentes(request):
    """Gravações e processamentos em aberto de quem está logado (o banner de pendências)."""
    from . import processamento

    processamento.garantir_varredura()
    pendentes = []
    abertas = (MeetingTranscription.objects
               .filter(owner=request.user, status__in=('recording', 'processing'))
               .order_by('-created_at')[:20])
    for t in abertas:
        pendentes.append({
            'id': t.pk,
            'upload_id': t.upload_id,
            'title': t.title,
            'status': t.status,
            'origem': t.origem,
            'event_id': t.event_id,
            'partes_recebidas': t.partes_recebidas,
            'ultima_parte_em': t.ultima_parte_em.isoformat() if t.ultima_parte_em else None,
            'criado_em': t.created_at.isoformat(),
            'etapa': t.etapa,
            'etapa_rotulo': ETAPA_ROTULOS.get(t.etapa, ''),
            'tentativas': t.tentativas,
            'proxima_tentativa_em': t.proxima_tentativa_em.isoformat() if t.proxima_tentativa_em else None,
            'finalizada_automaticamente': t.finalizada_automaticamente,
            'progresso_pct': _progresso_pct(t),
            'redirect': f'/agenda/transcricoes/{t.pk}/',
        })
    return JsonResponse({'pendentes': pendentes})


@login_required_json
@require_POST
def api_transcription_discard(request, pk):
    """Descarta uma gravação que não vai ser processada: apaga as partes e o registro."""
    transcricao = get_object_or_404(_manageable_transcriptions_for_user(request.user), pk=pk)
    if transcricao.status not in ('recording', 'error') or (transcricao.raw_transcription or '').strip():
        return JsonResponse({'error': 'Só dá para descartar gravação em andamento ou que falhou antes de ter texto.'},
                            status=409)
    apagadas = _apagar_partes(transcricao.upload_id) if transcricao.upload_id else 0
    if transcricao.audio_file:
        try:
            transcricao.audio_file.delete(save=False)
        except Exception:                                           # noqa: BLE001
            pass
    transcricao.delete()
    return JsonResponse({'ok': True, 'partes_apagadas': apagadas})


@login_required
def transcription_recorder(request):
    """Janela própria do gravador: segue gravando mesmo com a aba do portal fechada."""
    fonte = request.GET.get('fonte') or 'mic'
    if fonte not in ('mic', 'system', 'both'):
        fonte = 'mic'
    return render(request, 'agenda/gravador.html', {
        'event': _evento_para_transcricao(request.user, request.GET.get('event_id')),
        'upload_id': _sanitize_upload_id(request.GET.get('upload_id')),
        'titulo': (request.GET.get('titulo') or '').strip()[:255],
        'fonte': fonte,
        'origem': 'gravador',
    })


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


def _convert_to_mp3(input_path, timeout=None):
    """Converte áudio para mp3 (mono/16kHz) usando ffmpeg e retorna o path gerado."""
    base_name, _ = os.path.splitext(input_path)
    output_path = f'{base_name}_normalized.mp3'

    if timeout is None:
        # Timeout adaptativo ao tamanho: gravações longas (várias horas) levam
        # mais tempo para transcodificar. ~4s por MB, mínimo 30min, teto 6h.
        try:
            size_mb = os.path.getsize(input_path) / (1024 * 1024)
        except OSError:
            size_mb = 0
        timeout = int(max(1800, min(6 * 3600, size_mb * 4)))

    try:
        subprocess.run(
            [
                'ffmpeg', '-y', '-i', input_path, '-vn', '-acodec', 'libmp3lame',
                '-ab', '64k', '-ar', '16000', '-ac', '1', output_path,
            ],
            capture_output=True,
            timeout=timeout,
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


def _transcribe_audio_from_storage(client, field_file, duration_hint=0):
    """Transcreve um áudio armazenado no storage (S3/local) com uso seguro de memória."""
    temp_source_path = _copy_storage_file_to_temp(field_file)
    original_name = os.path.basename(getattr(field_file, 'name', '') or 'audio.webm')

    try:
        return _transcribe_audio_path(
            client, temp_source_path, original_name, duration_hint=duration_hint
        )
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


def _transcribe_audio_path(client, source_path, original_filename, duration_hint=0,
                           transcricao=None, salvar_progresso=None, permitir_lacunas=True):
    """Converte para mp3 quando possível, divide em partes se necessário e transcreve com retry.

    Com `transcricao`/`salvar_progresso`, áudio longo é transcrito em trechos e
    cada trecho pronto fica salvo — a retomada não refaz. Com
    `permitir_lacunas=False`, trecho que falhar levanta erro em vez de virar um
    marcador no texto: o job tenta de novo só os que faltaram.
    """
    from .processamento import PerdeuAReivindicacao

    whisper_max_size = 24 * 1024 * 1024  # 24MB
    long_audio_seconds = 1500  # acima de ~25min, segmentar para mais robustez
    nao_cair_no_plano_b = (SegmentosFaltando, PerdeuAReivindicacao)
    em_trechos = {'transcricao': transcricao, 'salvar_progresso': salvar_progresso,
                  'permitir_lacunas': permitir_lacunas}

    duration_hint = int(duration_hint or 0)

    # A duração vinda do ffprobe é a fonte primária; o hint (cronômetro do
    # cliente) entra como fallback. Isso é essencial em gravações de 24h cujo
    # webm concatenado às vezes não expõe a duração — sem o hint, a segmentação
    # assumiria só 1h e cortaria o restante do áudio. Numa retomada vale a
    # duração da primeira vez, para cortar o áudio nos mesmos pontos.
    progresso = getattr(transcricao, 'progresso', None) or {}
    probed_duration = float(progresso.get('duracao_total') or 0) or _probe_audio_duration_seconds(source_path)
    duration_seconds = probed_duration or (duration_hint or None)

    if duration_seconds and duration_seconds >= long_audio_seconds:
        try:
            raw_text = _split_and_transcribe(
                client, source_path, total_duration_hint=duration_seconds, **em_trechos
            )
            return raw_text, int(duration_seconds)
        except nao_cair_no_plano_b:
            raise
        except Exception:
            # Se falhar a segmentacao (ex.: sem ffmpeg), tenta o fluxo tradicional
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
            raw_text = _split_and_transcribe(
                client, mp3_path, total_duration_hint=duration_seconds or duration_hint, **em_trechos
            )
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


# Trechos transcritos ao mesmo tempo: bem abaixo do limite de requisições da
# OpenAI, e uma gravação longa sai em um terço do tempo.
TRANSCRICAO_TRECHOS_EM_PARALELO = 3


class SegmentosFaltando(Exception):
    """Alguns trechos do áudio não foram transcritos (a OpenAI falhou neles)."""


def _hhmm(segundos):
    segundos = int(segundos or 0)
    return f'{segundos // 3600:02d}:{(segundos % 3600) // 60:02d}'


def _transcrever_trecho(client, audio_path, indice, inicio, duracao, pasta):
    """Recorta um trecho com o ffmpeg e manda para o Whisper (roda numa thread)."""
    seg_path = os.path.join(pasta, f'seg_{indice:04d}.mp3')
    try:
        subprocess.run(
            [
                'ffmpeg', '-y', '-ss', str(inicio), '-t', str(duracao), '-i', audio_path,
                '-vn', '-acodec', 'libmp3lame', '-ab', '64k', '-ar', '16000', '-ac', '1', seg_path,
            ],
            capture_output=True,
            timeout=max(300, int(duracao * 3)),
            check=True,
        )
        text, _ = _try_transcribe_file(client, seg_path, is_converted=True, max_retries=3,
                                       response_format='text')
        return (text or '').strip()
    finally:
        if os.path.exists(seg_path):
            os.unlink(seg_path)


def _split_and_transcribe(client, mp3_path, total_duration_hint=0,
                          transcricao=None, salvar_progresso=None, permitir_lacunas=True):
    """Divide áudio grande em trechos de tempo, transcreve 3 por vez e junta na ordem.

    Com `salvar_progresso`, cada trecho pronto é salvo na hora em
    `transcricao.progresso`: se o processo cair no meio de uma gravação de 8 h,
    a retomada só transcreve o que faltou. A divisão também fica salva, para a
    retomada cortar o áudio nos mesmos pontos.
    """
    progresso = dict(getattr(transcricao, 'progresso', None) or {})
    # Fallback para o hint (cronômetro do cliente) quando o ffprobe não consegue
    # ler a duração — sem isso, gravações longas seriam truncadas em 1h.
    total_duration = (float(progresso.get('duracao_total') or 0)
                      or _probe_audio_duration_seconds(mp3_path) or int(total_duration_hint or 0) or 3600)
    segment_seconds = int(progresso.get('segundos_por_segmento')
                          or (900 if total_duration >= 6 * 3600 else 720))
    total_segmentos = max(1, int(math.ceil(total_duration / segment_seconds)))
    feitos = {str(chave): valor for chave, valor in (progresso.get('segmentos') or {}).items()}

    progresso.update(duracao_total=total_duration, segundos_por_segmento=segment_seconds,
                     total_segmentos=total_segmentos, segmentos=feitos)
    if salvar_progresso:
        salvar_progresso(dict(progresso))

    falhas = {}
    pendentes = [i for i in range(total_segmentos) if str(i) not in feitos]
    tmp_dir = tempfile.mkdtemp()
    pool = ThreadPoolExecutor(max_workers=TRANSCRICAO_TRECHOS_EM_PARALELO)
    try:
        futuros = {}
        for indice in pendentes:
            inicio = indice * segment_seconds
            duracao = min(segment_seconds, max(1, total_duration - inicio))
            futuros[pool.submit(_transcrever_trecho, client, mp3_path, indice, inicio, duracao, tmp_dir)] = indice
        for futuro in as_completed(futuros):
            indice = futuros[futuro]
            try:
                feitos[str(indice)] = futuro.result()
            except Exception as err:
                falhas[indice] = str(err)[:120]
                continue
            if salvar_progresso:
                progresso['segmentos'] = feitos
                progresso['segmentos_falhados'] = sorted(falhas)
                salvar_progresso(dict(progresso))
    except BaseException:
        # Outro processo assumiu a transcrição (ou houve interrupção): não espera
        # os trechos que ainda nem começaram.
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        pool.shutdown(wait=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if falhas and not permitir_lacunas:
        if salvar_progresso:
            progresso['segmentos'] = feitos
            progresso['segmentos_falhados'] = sorted(falhas)
            salvar_progresso(dict(progresso))
        raise SegmentosFaltando(
            f'{len(falhas)} de {total_segmentos} trechos do áudio ainda não foram transcritos '
            f'({next(iter(falhas.values()))}).')

    partes = []
    for indice in range(total_segmentos):
        texto = feitos.get(str(indice))
        if texto is None:
            inicio = indice * segment_seconds
            fim = min(total_duration, inicio + segment_seconds)
            partes.append(f'[Trecho {_hhmm(inicio)}–{_hhmm(fim)} não transcrito]')
        elif texto:
            partes.append(texto)
    return '\n\n'.join(partes).strip()


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


# =========================================================================
# PIPELINE DE TRANSCRIÇÃO (retomável — quem roda é agenda/processamento.py)
# =========================================================================

ETAPA_ROTULOS = dict(MeetingTranscription.ETAPAS)
# A partir desta tentativa, trecho que a OpenAI não transcreveu vira um aviso no
# texto: melhor a ata com uma lacuna marcada do que ata nenhuma.
TENTATIVAS_ANTES_DE_ACEITAR_LACUNAS = 4


def processar_transcricao(transcricao_id, client, dono='', modo='upload', opcoes=None):
    """O pipeline inteiro, retomável — cada etapa salva o que produziu.

    montagem    → junta as partes do storage num áudio só e o guarda;
    transcrição → Whisper em trechos, cada trecho salvo ao terminar;
    análise     → resumo, seções, decisões e ações (o texto bruto já está salvo);
    tarefas     → evento na agenda e tarefas.

    Quem chama é o job de agenda/processamento.py, que reivindica a
    transcrição, mantém o batimento e agenda nova tentativa quando algo falha.
    Retomar é chamar de novo: o que já está no banco não é refeito.
    """
    from . import processamento as proc

    opcoes = opcoes or {}
    t = MeetingTranscription.objects.select_related('owner').get(pk=transcricao_id)

    def salvar(*campos):
        proc.conferir_dono(transcricao_id, dono)
        t.save(update_fields=list(dict.fromkeys(list(campos) + ['updated_at'])))

    def salvar_progresso(progresso):
        t.progresso = progresso
        salvar('progresso')

    raw_fornecido = opcoes.get('raw_text') if isinstance(opcoes.get('raw_text'), str) else ''
    raw_fornecido = (raw_fornecido or '').strip()
    if raw_fornecido:
        t.raw_transcription = raw_fornecido
        salvar('raw_transcription')
    forcar_texto = bool(opcoes.get('force_raw'))
    if forcar_texto and not ((t.raw_transcription or '').strip() or (t.formatted_transcription or '').strip()):
        raise proc.ErroPermanente('Não há texto bruto para retranscrever.')

    permitir_lacunas = (t.tentativas or 0) >= TENTATIVAS_ANTES_DE_ACEITAR_LACUNAS
    texto = ''
    for _volta in range(4):
        processadas = int((t.progresso or {}).get('partes_processadas') or 0)
        partes_novas = bool(t.upload_id) and not raw_fornecido and t.partes_recebidas > processadas
        texto = '' if partes_novas else (t.raw_transcription or '').strip()
        if not texto and not partes_novas and (forcar_texto or not t.audio_file):
            # Transcrição antiga que só guardou a versão formatada.
            texto = (t.formatted_transcription or '').strip()
            if texto:
                t.raw_transcription = texto
                salvar('raw_transcription')
        if not texto:
            texto = _montar_e_transcrever(t, client, salvar, salvar_progresso, partes_novas, permitir_lacunas)
        # Partes que chegaram enquanto transcrevia (o navegador voltou e mandou o
        # que estava guardado nele): refaz com o áudio completo.
        t.refresh_from_db(fields=['partes_recebidas'])
        if (bool(t.upload_id) and not raw_fornecido
                and t.partes_recebidas > int((t.progresso or {}).get('partes_processadas') or 0)):
            continue
        break

    _analisar_e_concluir(t, client, texto, salvar)


def _montar_e_transcrever(t, client, salvar, salvar_progresso, partes_novas, permitir_lacunas):
    """Junta as partes (se chegaram novas) e transcreve o áudio. Devolve o texto bruto."""
    from . import processamento as proc

    temp_path = None
    try:
        if partes_novas:
            t.etapa = 'montagem'
            salvar('etapa')
            temp_path, partes = _assemble_transcription_parts_to_temp(t.upload_id, '')
            if not temp_path:
                raise proc.ErroPermanente('Nenhuma parte do áudio foi encontrada no armazenamento.')
            nome = ('reuniao' if t.origem == 'reuniao' else 'gravacao') + os.path.splitext(temp_path)[1]
            anterior = t.audio_file.name if t.audio_file else ''
            with open(temp_path, 'rb') as audio_stream:
                t.audio_file.save(nome, File(audio_stream), save=False)
            # Áudio novo: os trechos transcritos do áudio anterior não valem mais.
            progresso = {chave: valor for chave, valor in (t.progresso or {}).items()
                         if chave not in ('segmentos', 'segmentos_falhados', 'total_segmentos',
                                          'segundos_por_segmento', 'duracao_total')}
            progresso['partes_processadas'] = partes
            t.progresso = progresso
            t.raw_transcription = ''
            salvar('audio_file', 'progresso', 'raw_transcription')
            if anterior and anterior != t.audio_file.name:
                try:
                    t.audio_file.storage.delete(anterior)
                except Exception:                                   # noqa: BLE001
                    pass
        elif not t.audio_file:
            raise proc.ErroPermanente('Não há texto nem áudio para processar esta transcrição.')

        t.etapa = 'transcricao'
        salvar('etapa')
        if not temp_path:
            temp_path = _copy_storage_file_to_temp(t.audio_file)
        texto, duracao = _transcribe_audio_path(
            client, temp_path, os.path.basename(t.audio_file.name or '') or 'audio.webm',
            duration_hint=int(t.duration_seconds or 0), transcricao=t,
            salvar_progresso=salvar_progresso, permitir_lacunas=permitir_lacunas)
        texto = (texto or '').strip()
        if not texto:
            if (t.tentativas or 0) >= 2:
                raise proc.ErroPermanente('O áudio não tem fala que a transcrição consiga reconhecer.')
            raise RuntimeError('A transcrição do áudio voltou vazia.')
        # O texto bruto é salvo ANTES da análise: se a análise falhar, a próxima
        # tentativa não paga o Whisper de novo.
        t.raw_transcription = texto
        if duracao:
            t.duration_seconds = int(duracao)
        salvar('raw_transcription', 'duration_seconds')
        return texto
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _analisar_e_concluir(t, client, texto, salvar):
    t.etapa = 'analise'
    salvar('etapa')
    analysis = _generate_transcription_analysis(
        client, t.title, texto, analysis_context=_build_participant_roles_context(t.participant_roles))

    t.formatted_transcription = analysis['formatted_transcription']
    t.summary = analysis['summary']
    t.sections = analysis['sections']
    t.key_decisions = analysis['key_decisions']
    t.action_items = analysis['action_items']
    t.participants_identified = analysis['participants_identified']
    t.sentiment = analysis['sentiment']
    t.meeting_type_detected = analysis['meeting_type_detected']
    t.tags = analysis['tags']
    t.suggested_events = analysis['suggested_events']
    t.risks = analysis.get('risks', [])
    t.etapa = 'tarefas'
    salvar('formatted_transcription', 'summary', 'sections', 'key_decisions', 'action_items',
           'participants_identified', 'sentiment', 'meeting_type_detected', 'tags',
           'suggested_events', 'risks', 'etapa')

    _ensure_transcription_calendar_event(t, t.owner)
    # Tarefas numa transação e marcadas com a lista que as gerou: uma retomada
    # depois de cair no meio não cria a mesma tarefa duas vezes.
    marca = hashlib.sha1(json.dumps(t.action_items or [], sort_keys=True, ensure_ascii=False,
                                    default=str).encode()).hexdigest()
    if (t.progresso or {}).get('tarefas_de') != marca:
        with transaction.atomic():
            t.tasks_created.clear()
            _create_tasks_from_transcription(t, t.owner)
            t.progresso = {**(t.progresso or {}), 'tarefas_de': marca}
            salvar('progresso')

    t.status = 'completed'
    t.etapa = ''
    t.error_message = ''
    t.processando_por = ''
    t.batimento_em = None
    t.proxima_tentativa_em = None
    salvar('status', 'etapa', 'error_message', 'processando_por', 'batimento_em', 'proxima_tentativa_em')


def concluir_sem_ia(transcricao_id, dono=''):
    """Sem chave da OpenAI: conclui com o texto que já existe (como antes)."""
    from . import processamento as proc

    t = MeetingTranscription.objects.get(pk=transcricao_id)
    texto = (t.raw_transcription or '').strip() or (t.formatted_transcription or '').strip()
    if not texto:
        raise proc.ErroPermanente('Chave da API OpenAI não configurada.')
    proc.conferir_dono(transcricao_id, dono)
    t.formatted_transcription = t.formatted_transcription or texto
    t.summary = (t.summary or 'Reprocessado usando a transcrição já salva. '
                              'Configure OPENAI_API_KEY para análise avançada.')
    t.status = 'completed'
    t.error_message = ''
    t.etapa = ''
    t.processando_por = ''
    t.batimento_em = None
    t.proxima_tentativa_em = None
    t.save(update_fields=['formatted_transcription', 'summary', 'status', 'error_message', 'etapa',
                          'processando_por', 'batimento_em', 'proxima_tentativa_em', 'updated_at'])


def _start_transcription_background_job(transcription_id, api_key=None, mode='upload', options=None):
    """Mantido pelo nome (outros pontos e testes o usam): agora é o job durável."""
    from . import processamento

    return processamento.iniciar_job(transcription_id, modo=mode, opcoes=options)


def _prioritize_processing_transcriptions(api_key=None, exclude_id=None, limit=2):
    """Retoma primeiro as transcrições paradas (mantido pelo nome)."""
    from . import processamento

    return processamento.retomar_paradas(limite=limit, excluir=exclude_id)


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
    """Visualizar uma transcrição visível para o usuário."""
    transcription = get_object_or_404(
        _visible_transcriptions_for_user(request.user).distinct(),
        pk=pk,
    )
    is_owner = transcription.owner_id == request.user.id
    tasks = transcription.tasks_created.select_related('assigned_to').all()
    users = User.objects.filter(is_active=True).order_by('first_name', 'username')
    shared_ids = list(transcription.shared_with.values_list('id', flat=True))

    context = {
        'transcription': transcription,
        'tasks': tasks,
        'users': users,
        'is_owner': is_owner,
        'can_reprocess_transcription': _can_reprocess_transcription(request.user, transcription),
        'shared_ids': shared_ids,
    }
    return render(request, 'agenda/transcription_detail.html', context)


@login_required
@require_POST
def api_transcription_share(request, pk):
    """Atualiza a lista de usuários com quem a transcrição é compartilhada (somente proprietário)."""
    transcription = get_object_or_404(MeetingTranscription, pk=pk, owner=request.user)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    user_ids = data.get('user_ids', [])
    if not isinstance(user_ids, list):
        return JsonResponse({'error': 'user_ids deve ser uma lista.'}, status=400)

    try:
        user_ids = [int(uid) for uid in user_ids]
    except (ValueError, TypeError):
        return JsonResponse({'error': 'IDs de usuário inválidos.'}, status=400)

    # Não permitir compartilhar com o próprio dono
    user_ids = [uid for uid in user_ids if uid != request.user.id]

    target_users = list(User.objects.filter(is_active=True, pk__in=user_ids))
    previous_ids = set(transcription.shared_with.values_list('id', flat=True))
    transcription.shared_with.set(target_users)
    new_ids = {u.id for u in target_users}

    # Notificar novos usuários adicionados
    added = new_ids - previous_ids
    for user in target_users:
        if user.id in added:
            _notify_agenda_user(
                user,
                'Transcrição compartilhada com você',
                f'{request.user.get_full_name() or request.user.username} compartilhou a transcrição "{transcription.title}" com você.',
                action_url=f'/agenda/transcricoes/{transcription.pk}/',
            )

    return JsonResponse({
        'ok': True,
        'shared_count': len(target_users),
        'shared_ids': sorted(new_ids),
        'message': 'Compartilhamento atualizado com sucesso!',
    })


def _progresso_pct(t):
    """Quanto do processamento já foi feito (para a barra da tela)."""
    if t.status == 'completed':
        return 100
    if t.status == 'recording':
        return 0
    if t.etapa == 'montagem':
        return 5
    if t.etapa == 'transcricao':
        progresso = t.progresso or {}
        total = int(progresso.get('total_segmentos') or 0)
        feitos = len(progresso.get('segmentos') or {})
        return 10 + int(70 * feitos / total) if total else 10
    if t.etapa == 'analise':
        return 85
    if t.etapa == 'tarefas':
        return 95
    return 3


@login_required_json
def api_transcription_status(request, pk):
    """Retorna status resumido da transcrição para polling da interface."""
    from . import processamento

    processamento.garantir_varredura()
    transcription = get_object_or_404(_visible_transcriptions_for_user(request.user), pk=pk)

    # "Travado" é job sem sinal de vida — não a espera de uma nova tentativa já
    # marcada. `updated_at` não serve: salvar só alguns campos não o renova.
    agora = timezone.now()
    aguardando = transcription.aguardando_nova_tentativa
    referencia = transcription.batimento_em or transcription.updated_at
    stale_seconds = 0
    if transcription.status == 'processing' and referencia and not aguardando:
        stale_seconds = int((agora - referencia).total_seconds())
    is_stale = stale_seconds >= processamento.PARADO_APOS_SEGUNDOS

    return JsonResponse({
        'id': transcription.pk,
        'status': transcription.status,
        'error_message': transcription.error_message,
        'updated_at': transcription.updated_at.isoformat(),
        'is_stale': is_stale,
        'stale_seconds': stale_seconds,
        'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        'etapa': transcription.etapa,
        'etapa_rotulo': ETAPA_ROTULOS.get(transcription.etapa, ''),
        'tentativas': transcription.tentativas,
        'aguardando_nova_tentativa': aguardando,
        'proxima_tentativa_em': (transcription.proxima_tentativa_em.isoformat()
                                 if transcription.proxima_tentativa_em else None),
        'partes_recebidas': transcription.partes_recebidas,
        'ultima_parte_em': transcription.ultima_parte_em.isoformat() if transcription.ultima_parte_em else None,
        'finalizada_automaticamente': transcription.finalizada_automaticamente,
        'progresso_pct': _progresso_pct(transcription),
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


@login_required_json
@require_POST
def api_transcription_reprocess(request, pk):
    """Reinicia o processamento em segundo plano (e retoma outras que pararam).

    Numa gravação ainda aberta (o navegador de quem gravava sumiu), serve de
    "encerrar agora": fecha a sessão com as partes que chegaram e processa.
    """
    from django.conf import settings as django_settings

    transcription = get_object_or_404(_manageable_transcriptions_for_user(request.user), pk=pk)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}') if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    # Só o que o pipeline entende vai adiante (antes ia o JSON inteiro do cliente).
    options = {}
    if payload.get('force_raw'):
        options['force_raw'] = True
    provided_raw_text = payload.get('raw_text') if isinstance(payload.get('raw_text'), str) else ''
    provided_raw_text = (provided_raw_text or '').strip()
    if provided_raw_text:
        options['raw_text'] = provided_raw_text

    if transcription.status == 'recording' and not transcription.partes_recebidas and not provided_raw_text:
        return JsonResponse({'error': 'Esta gravação ainda não recebeu nenhuma parte de áudio.'}, status=400)

    transcription.status = 'processing'
    transcription.error_message = ''
    transcription.tentativas = 0
    transcription.proxima_tentativa_em = None
    update_fields = ['status', 'error_message', 'tentativas', 'proxima_tentativa_em', 'updated_at']

    if provided_raw_text:
        transcription.raw_transcription = provided_raw_text
        update_fields.append('raw_transcription')

    transcription.save(update_fields=update_fields)

    api_key = getattr(django_settings, 'OPENAI_API_KEY', '')
    prioritized_count = _prioritize_processing_transcriptions(
        api_key,
        exclude_id=transcription.pk,
    )
    started = _start_transcription_background_job(
        transcription_id=transcription.pk,
        api_key=api_key,
        mode='reprocess',
        options=options,
    )

    return JsonResponse({
        'ok': True,
        'status': 'processing',
        'started': started,
        'prioritized_processing_count': prioritized_count,
        'redirect': f'/agenda/transcricoes/{transcription.pk}/',
        'message': 'Reprocessamento iniciado em segundo plano.',
    }, status=202)
