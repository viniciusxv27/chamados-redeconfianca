"""Formato dos dados da Agenda para o app (eventos, convites e transcrições de reunião).

Funções simples que montam dict, no estilo de ``api_mobile/dados.py``. Nenhuma regra de acesso aqui —
quem decide o que a pessoa vê é ``api_mobile/modulos/agenda.py`` (com as funções de agenda/views.py).
"""
from django.utils import timezone

from .. import dados


def hora_local(valor):
    """ISO com o fuso de Brasília (o app agrupa por dia a partir disto)."""
    if not valor:
        return None
    if timezone.is_naive(valor):
        valor = timezone.make_aware(valor)
    return timezone.localtime(valor).isoformat()


def nome(user):
    if user is None:
        return None
    return user.get_full_name() or user.username or user.email


def pessoa_curta(user):
    if user is None:
        return None
    return {'id': user.pk, 'nome': nome(user), 'setor': user.sector.name if user.sector_id else None}


def link_do_evento(request, link):
    """Sala de reunião do portal vem como caminho (/reunioes/12/sala/): vira URL absoluta do portal."""
    link = (link or '').strip()
    if not link:
        return None, False
    if link.startswith('/'):
        return dados.absoluta(request, link), True
    return link, False


def evento(request, ev, user):
    link, do_portal = link_do_evento(request, ev.link)
    return {
        'id': ev.pk,
        'titulo': ev.title,
        'tipo': ev.event_type,
        'tipo_nome': ev.get_event_type_display(),
        'cor': ev.color or '#4f46e5',
        'inicio': hora_local(ev.start),
        'fim': hora_local(ev.end),
        'dia_inteiro': ev.all_day,
        'local': ev.location or None,
        'link': link,
        'link_do_portal': do_portal,
        'privado': ev.is_private,
        'recorrente': ev.recurrence_rule != 'none',
        'sou_dono': ev.owner_id == user.pk,
        'dono': nome(ev.owner),
    }


def participante(ep):
    return {
        'id': ep.user_id,
        'nome': nome(ep.user),
        'setor': ep.user.sector.name if ep.user.sector_id else None,
        'status': ep.status,
        'status_nome': ep.get_status_display(),
        'observacao': ep.response_notes or None,
    }


def convite(request, inv, user):
    return {
        'id': inv.pk,
        'evento': evento(request, inv.event, user),
        'convidado_em': hora_local(inv.invited_at),
    }


def solicitacao(sol):
    return {
        'id': sol.pk,
        'titulo': sol.title,
        'descricao': sol.description or None,
        'tipo': sol.meeting_type,
        'tipo_nome': sol.get_meeting_type_display(),
        'inicio': hora_local(sol.proposed_start),
        'fim': hora_local(sol.proposed_end),
        'local': sol.location or None,
        'solicitante': pessoa_curta(sol.requester),
        'criada_em': hora_local(sol.created_at),
    }


# ── Transcrições ─────────────────────────────────────────────────────────────

STATUS_TRANSCRICAO = {
    'recording': 'Gravando',
    'processing': 'Processando',
    'completed': 'Concluída',
    'error': 'Erro',
}


def _texto(item, *chaves):
    """Itens da IA às vezes vêm como texto solto, às vezes como dict: pega o primeiro campo que existir."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for chave in chaves:
            valor = item.get(chave)
            if isinstance(valor, str) and valor.strip():
                return valor.strip()
    return ''


def _lista(valor):
    return valor if isinstance(valor, list) else []


def _nivel(valor):
    valor = str(valor or '').strip().lower()
    return valor if valor in ('high', 'medium', 'low') else None


def processamento(t):
    """O andamento que a tela do portal mostra (agenda/views.py api_transcription_status)."""
    from agenda.views import ETAPA_ROTULOS, _progresso_pct

    return {
        'status': t.status,
        'status_nome': STATUS_TRANSCRICAO.get(t.status, t.get_status_display()),
        'etapa': t.etapa or None,
        'etapa_rotulo': ETAPA_ROTULOS.get(t.etapa, '') or None,
        'progresso_pct': _progresso_pct(t),
        'aguardando_nova_tentativa': t.aguardando_nova_tentativa,
        'proxima_tentativa_em': hora_local(t.proxima_tentativa_em),
        'tentativas': t.tentativas,
        'erro': (t.error_message or None) if t.status == 'error' else None,
    }


def transcricao_resumo(t, user):
    resumo = (t.summary or '').strip()
    if len(resumo) > 180:
        resumo = resumo[:180].rsplit(' ', 1)[0].rstrip(' .,;:') + '…'
    return {
        'id': t.pk,
        'titulo': t.title,
        **processamento(t),
        'criada_em': hora_local(t.created_at),
        'duracao_segundos': t.duration_seconds or 0,
        'dono': nome(t.owner),
        'sou_dono': t.owner_id == user.pk,
        'evento': {'id': t.event_id, 'titulo': t.event.title} if t.event_id else None,
        'resumo': resumo or None,
        'acoes': len(_lista(t.action_items)),
        'decisoes': len(_lista(t.key_decisions)),
        'tarefas': getattr(t, 'total_tarefas', None),
    }


def secoes(t):
    saida = []
    for s in _lista(t.sections):
        if not isinstance(s, dict):
            continue
        saida.append({
            'titulo': _texto(s, 'title') or 'Seção',
            'conteudo': _texto(s, 'content'),
            'minutos': s.get('duration_estimate') if isinstance(s.get('duration_estimate'), (int, float)) else None,
            'topicos': [x for x in _lista(s.get('topics_discussed')) if isinstance(x, str)],
            'destaques': [x for x in _lista(s.get('highlights')) if isinstance(x, str)],
        })
    return saida


def decisoes(t):
    saida = []
    for d in _lista(t.key_decisions):
        texto = _texto(d, 'decision', 'description', 'text')
        if texto:
            saida.append({'texto': texto, 'contexto': _texto(d, 'context') or None,
                          'impacto': _nivel(d.get('impact')) if isinstance(d, dict) else None})
    return saida


def riscos(t):
    saida = []
    for r in _lista(t.risks):
        # transcription_detail.html: {% firstof risk.risk risk.description risk %}
        texto = _texto(r, 'risk', 'description')
        if texto:
            saida.append({'texto': texto, 'mitigacao': _texto(r, 'mitigation') or None,
                          'responsavel': _texto(r, 'owner') or None,
                          'severidade': _nivel(r.get('severity')) if isinstance(r, dict) else None})
    return saida


def itens_de_acao(t):
    saida = []
    for i in _lista(t.action_items):
        texto = _texto(i, 'task', 'description', 'text')
        if not texto:
            continue
        responsavel = _texto(i, 'responsible')
        saida.append({
            'texto': texto,
            # O portal esconde o "A definir" (transcription_detail.html: item.responsible != 'A definir').
            'responsavel': responsavel if responsavel and responsavel != 'A definir' else None,
            'prazo': _texto(i, 'deadline') or None,
            'prioridade': _nivel(i.get('priority')) if isinstance(i, dict) else None,
        })
    return saida


def meta_curta(meta):
    if meta is None:
        return None
    return {'id': meta.id, 'pendente': bool(meta.pendente_aprovacao), 'url_web': f'/impulso/metas/{meta.id}/'}


def tarefa(task, impulso_disponivel):
    saida = {
        'id': task.pk,
        'titulo': task.title,
        'descricao': task.description or '',
        'status': task.status,
        'status_nome': task.get_status_display(),
        'prioridade': task.priority,
        'prioridade_nome': task.get_priority_display(),
        'prazo': hora_local(task.due_date),
        'responsavel': pessoa_curta(task.assigned_to),
    }
    if impulso_disponivel:
        # Os mesmos três estados do card do portal (transcription_detail.html: task.meta_impulso*,
        # task.impulso_para_responsavel), calculados por agenda.views._contexto_impulso.
        saida['impulso'] = {
            'minha_meta': meta_curta(getattr(task, 'meta_impulso', None)),
            'meta_do_responsavel': meta_curta(getattr(task, 'meta_impulso_responsavel', None)),
            'enviar_para_responsavel_url': (
                f'/impulso/metas/nova/?tarefa={task.pk}&colaborador={task.assigned_to_id}'
                if getattr(task, 'impulso_para_responsavel', False)
                and getattr(task, 'meta_impulso_responsavel', None) is None else None),
        }
    return saida
