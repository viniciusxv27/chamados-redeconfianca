"""Agenda e Reuniões no assistente — acesso completo, com as permissões da tela.

As outras ferramentas leem só dados do próprio usuário. Aqui o assistente
enxerga e faz o que a pessoa enxerga e faz em /agenda/ e /reunioes/: a própria
agenda e a de quem ela tem permissão de ver, convites, solicitações,
transcrições/atas, reuniões e o catálogo de convite.

- Leitura consulta o banco com as mesmas regras de visibilidade das telas.
- Escrita chama a PRÓPRIA view do módulo como se a pessoa tivesse clicado:
  mesmas permissões, mesmos avisos, a sala/tarefa que nasce junto — sem uma
  segunda cópia da regra aqui dentro.
- Nenhuma escrita roda direto: a `previa` valida e descreve, e a execução
  espera a confirmação do usuário numa nova mensagem (ferramentas.executar).

Fora do alcance, por natureza: gravar/enviar áudio e entrar na sala de vídeo
(precisam do navegador) e a configuração do módulo de reuniões.
"""
import json
import logging
import re
from collections import Counter
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from .comum import (DATA_HORA, DIAS, Invalido, _acao, _bool, _cortar, _data, _data_hora, _dh, _dia, _falha,
                    _ids, _int, _inteiro, _limite, _lista_ids, _lista_txt, _local, _meia_noite, _nome,
                    _nomes, _normal, _obj, _ok_json, _periodo, _pessoas, _sem_erro, _sim, _titulo, _txt,
                    _url, chamar_view)

logger = logging.getLogger(__name__)
User = get_user_model()

LIMITE_TEXTO = 15000     # caracteres do texto de uma transcrição por chamada


# ─── Formatação ──────────────────────────────────────────────────────────────


_PRINCIPAL = ('task', 'decision', 'risk', 'title')
_ROTULOS = {
    'responsible': 'responsável', 'deadline': 'prazo', 'priority': 'prioridade',
    'success_criteria': 'critério de sucesso', 'context': 'contexto', 'impact': 'impacto',
    'severity': 'gravidade', 'mitigation': 'mitigação', 'suggested_date': 'data sugerida',
    'description': 'descrição',
}


def _item(valor):
    """Um item das listas da IA (decisão, risco, ação, evento sugerido) numa linha."""
    if not isinstance(valor, dict):
        return str(valor).strip()
    principal = next((str(valor[k]).strip() for k in _PRINCIPAL if valor.get(k)), '')
    extras = [f'{rotulo}: {valor[k]}' for k, rotulo in _ROTULOS.items() if valor.get(k)]
    return ' · '.join(([principal] if principal else []) + extras) or json.dumps(valor, ensure_ascii=False)


# ─── Leitura dos argumentos ──────────────────────────────────────────────────


# ─── A view do módulo, chamada como se fosse a pessoa ────────────────────────


# ─── Pessoas ─────────────────────────────────────────────────────────────────

def _buscar_pessoas(user, args):
    busca = ' '.join(str(args.get('busca') or '').split())
    if len(busca) < 2:
        return 'Informe pelo menos 2 letras do nome, e-mail, cargo ou setor.'
    limite = _limite(args, 15, 40)
    qs = User.objects.filter(is_active=True)
    for termo in busca.split():
        qs = qs.filter(Q(first_name__icontains=termo) | Q(last_name__icontains=termo)
                       | Q(email__icontains=termo) | Q(job_title__icontains=termo)
                       | Q(sector__name__icontains=termo))
    itens = list(qs.select_related('sector').order_by('first_name', 'last_name')[:limite + 1])
    if not itens:
        return f'Ninguém ativo encontrado para "{busca}".'
    linhas = [f'Colaboradores para "{busca}":']
    for u in itens[:limite]:
        voce = ' (você)' if u.pk == user.pk else ''
        linhas.append(f'id {u.pk} · {_nome(u)}{voce} · {u.job_title or "sem cargo"} · '
                      f'{u.sector.name if u.sector else "sem setor"} · {u.email}')
    if len(itens) > limite:
        linhas.append('… há mais resultados: refine a busca.')
    return '\n'.join(linhas)


# ─── Agenda: leitura ─────────────────────────────────────────────────────────

TIPOS_EVENTO = {'evento': 'event', 'reuniao': 'meeting', 'reunião': 'meeting', 'chamada': 'call',
                'tarefa': 'task', 'lembrete': 'reminder', 'bloqueio': 'block'}
STATUS_SOLICITACAO = {'pendente': 'pending', 'aceita': 'accepted', 'recusada': 'rejected',
                      'cancelada': 'cancelled'}
STATUS_TRANSCRICAO = {'concluida': 'completed', 'concluída': 'completed', 'processando': 'processing',
                      'gravando': 'recording', 'erro': 'error'}


def _tipo_evento(valor, padrao='event'):
    texto = str(valor or '').strip().lower()
    if not texto:
        return padrao
    if texto in TIPOS_EVENTO.values():
        return texto
    if texto in TIPOS_EVENTO:
        return TIPOS_EVENTO[texto]
    raise Invalido('tipo inválido. Use: evento, reuniao, chamada, tarefa, lembrete ou bloqueio.')


def _janela(args, dias_padrao=7):
    hoje = timezone.localdate()
    ini = _data(args['inicio'], 'inicio') if args.get('inicio') else hoje
    fim = _data(args['fim'], 'fim') if args.get('fim') else ini + timedelta(days=dias_padrao - 1)
    if fim < ini:
        raise Invalido('fim antes do início.')
    if (fim - ini).days > 92:
        raise Invalido('Período grande demais: peça até 3 meses por vez.')
    return ini, fim, _meia_noite(ini), _meia_noite(fim + timedelta(days=1))


def _pessoa_ou_eu(user, args):
    if not args.get('pessoa_id'):
        return user
    alvo = User.objects.filter(pk=_inteiro(args['pessoa_id'], 'pessoa_id'), is_active=True).first()
    if alvo is None:
        raise Invalido('Não achei colaborador ativo com esse id. Use buscar_pessoas.')
    return alvo


def _link(valor):
    return _url(valor) if (valor or '').startswith('/') else valor


def _linha_evento(ev, user):
    partes = [f'#{ev.pk}', _periodo(ev.start, ev.end, ev.all_day), ev.title, ev.get_event_type_display()]
    if ev.location:
        partes.append(f'local: {ev.location}')
    if ev.link:
        partes.append(f'link: {_link(ev.link)}')
    if ev.owner_id != user.pk:
        partes.append(f'de {_nome(ev.owner)}')
    status = Counter(p.status for p in ev.event_participants.all())
    rotulos = (('accepted', 'aceito(s)'), ('pending', 'pendente(s)'), ('rejected', 'recusado(s)'))
    convites = [f'{status[s]} {r}' for s, r in rotulos if status.get(s)]
    if convites:
        partes.append('convidados: ' + ', '.join(convites))
    if ev.is_private:
        partes.append('privado')
    if ev.recurrence_rule == 'weekly':
        partes.append('repete toda semana')
    return ' · '.join(partes)


def _agenda_eventos(user, args):
    from agenda import views as av
    from agenda.models import CalendarEvent, EventParticipant

    ini, fim, de, ate = _janela(args)
    alvo = _pessoa_ou_eu(user, args)
    limite = _limite(args, 60, 150)
    cabecalho = (('Sua agenda' if alvo == user else f'Agenda de {_nome(alvo)}')
                 + f' de {_dia(ini)} a {_dia(fim)}')

    if alvo != user and not av._can_view_full_calendar(user, alvo):
        # Mesma regra da tela: sem hierarquia sobre a pessoa, só os horários ocupados.
        ocupados = list(CalendarEvent.objects.filter(owner=alvo, start__lt=ate, end__gt=de)
                        .order_by('start').values_list('start', 'end')[:limite])
        linhas = [cabecalho + ' — você só vê os horários ocupados, sem detalhes:']
        linhas += [f'{_periodo(s, e)} · ocupado' for s, e in ocupados] or ['Nenhum horário ocupado.']
        return '\n'.join(linhas)

    if alvo == user:
        qs = CalendarEvent.objects.filter(Q(owner=user) | Q(participants=user),
                                          start__lt=ate, end__gt=de).distinct()
    else:
        qs = CalendarEvent.objects.filter(owner=alvo, start__lt=ate, end__gt=de)
    eventos = list(qs.select_related('owner').prefetch_related('event_participants')
                   .order_by('start')[:limite + 1])
    linhas = [f'{cabecalho} ({min(len(eventos), limite)} evento(s)):']
    linhas += [_linha_evento(ev, user) for ev in eventos[:limite]] or ['Nenhum evento no período.']
    if len(eventos) > limite:
        linhas.append('… há mais eventos: peça um período menor.')
    if alvo == user:
        pendentes = EventParticipant.objects.filter(user=user, status='pending').count()
        if pendentes:
            linhas.append(f'Você tem {pendentes} convite(s) pendente(s) — veja agenda_convites.')
    return '\n'.join(linhas)


def _agenda_evento(user, args):
    from agenda import views as av
    from agenda.models import CalendarEvent, EventParticipant
    from reunioes.models import Reuniao

    ev = (CalendarEvent.objects.select_related('owner', 'tarefa')
          .filter(pk=_inteiro(args.get('evento_id'), 'evento_id')).first())
    if ev is None:
        return 'Evento não encontrado.'
    meu_convite = EventParticipant.objects.filter(event=ev, user=user).first()
    if (ev.owner_id != user.pk and meu_convite is None
            and not av._can_view_full_calendar(user, ev.owner)):
        return 'Você não tem permissão para ver esse evento.'

    linhas = [f'Evento #{ev.pk}: {ev.title}',
              f'Quando: {_periodo(ev.start, ev.end, ev.all_day)}',
              f'Tipo: {ev.get_event_type_display()}',
              f'Dono: {"você" if ev.owner_id == user.pk else _nome(ev.owner)}']
    if ev.location:
        linhas.append(f'Local: {ev.location}')
    if ev.link:
        linhas.append(f'Link: {_link(ev.link)}')
    if ev.is_private:
        linhas.append('Privado: para os outros aparece só como ocupado.')
    if ev.recurrence_rule == 'weekly':
        ate = f' até {ev.recurrence_until:%d/%m/%Y}' if ev.recurrence_until else ''
        serie = f' (ocorrência da série #{ev.recurrence_parent_id})' if ev.recurrence_parent_id else ''
        linhas.append(f'Repete toda semana{ate}{serie}.')
    if ev.description:
        linhas.append('Descrição: ' + _cortar(ev.description, 2000))
    convites = list(ev.event_participants.select_related('user'))
    if convites:
        linhas.append('Convidados:')
        for p in convites:
            obs = f' — "{_cortar(p.response_notes, 200)}"' if p.response_notes else ''
            linhas.append(f'  {_nome(p.user)} (id {p.user_id}) · {p.get_status_display()}{obs}')
    if meu_convite and meu_convite.status == 'pending':
        linhas.append(f'Seu convite está pendente (convite #{meu_convite.pk}).')
    reuniao = Reuniao.objects.filter(evento=ev).first()
    if reuniao:
        linhas.append(f'Reunião do portal: #{reuniao.pk} ({reuniao.get_status_display()}) · '
                      f'{_url(f"/reunioes/{reuniao.pk}/")}')
    if ev.tarefa_id:
        linhas.append(f'Tarefa vinculada: #{ev.tarefa_id} · {ev.tarefa.get_status_display()}')
    for pk, titulo in av._visible_transcriptions_for_user(user).filter(event=ev).values_list('pk', 'title'):
        linhas.append(f'Transcrição/ata: #{pk} {titulo}')
    return '\n'.join(linhas)


def _agenda_convites(user, args):
    from agenda.models import EventParticipant, MeetingRequest

    convites = list(EventParticipant.objects.filter(user=user, status='pending')
                    .select_related('event', 'event__owner').order_by('event__start')[:_limite(args, 30)])
    linhas = [f'Convites pendentes ({len(convites)}):' if convites else 'Nenhum convite de evento pendente.']
    for c in convites:
        ev = c.event
        local = f' · local: {ev.location}' if ev.location else ''
        linhas.append(f'convite #{c.pk} · evento #{ev.pk} · {ev.title} · {ev.get_event_type_display()} · '
                      f'{_periodo(ev.start, ev.end, ev.all_day)} · de {_nome(ev.owner)}{local}')
    pedidos = MeetingRequest.objects.filter(target=user, status='pending').count()
    if pedidos:
        linhas.append(f'Há também {pedidos} solicitação(ões) de reunião esperando sua resposta '
                      '— veja agenda_solicitacoes.')
    return '\n'.join(linhas)


def _agenda_disponibilidade(user, args):
    from agenda import views as av

    alvo = _pessoa_ou_eu(user, args)
    dia = _data(args['data'], 'data') if args.get('data') else timezone.localdate()
    faixas = []
    for slot in av._get_available_slots(alvo, dia):
        if faixas and faixas[-1][1] == slot['start']:
            faixas[-1][1] = slot['end']
        else:
            faixas.append([slot['start'], slot['end']])
    quem = 'você' if alvo == user else _nome(alvo)
    if not faixas:
        return f'Sem horário livre para {quem} em {_dia(dia)} entre 08:00 e 18:00.'
    texto = ', '.join(f'{_local(a):%H:%M}–{_local(b):%H:%M}' for a, b in faixas)
    return (f'Horários livres de {quem} em {_dia(dia)} (08:00–18:00, pelos compromissos da '
            f'própria agenda): {texto}')


def _agenda_solicitacoes(user, args):
    from agenda.models import MeetingRequest

    tipo = str(args.get('tipo') or 'todas').lower()
    status = str(args.get('status') or '').lower()
    limite = _limite(args, 20)
    linhas = []
    for chave, rotulo, filtro, outro in (('recebidas', 'Recebidas', {'target': user}, 'requester'),
                                          ('enviadas', 'Enviadas', {'requester': user}, 'target')):
        if tipo not in ('todas', chave):
            continue
        qs = MeetingRequest.objects.filter(**filtro).select_related('requester', 'target')
        if status:
            qs = qs.filter(status=STATUS_SOLICITACAO.get(status, status))
        itens = list(qs.order_by('-created_at')[:limite])
        linhas.append(f'{rotulo} ({len(itens)}):')
        for m in itens:
            pessoa = getattr(m, outro)
            direcao = f'de {_nome(pessoa)}' if chave == 'recebidas' else f'para {_nome(pessoa)}'
            local = f' · local: {m.location}' if m.location else ''
            obs = f' · resposta: "{_cortar(m.response_notes, 200)}"' if m.response_notes else ''
            linhas.append(f'  solicitação #{m.pk} · {m.get_status_display()} · {m.title} · '
                          f'{m.get_meeting_type_display()} · {_periodo(m.proposed_start, m.proposed_end)} · '
                          f'{direcao}{local}{obs}')
        if not itens:
            linhas.append('  nenhuma.')
    return '\n'.join(linhas) or 'Use tipo recebidas, enviadas ou todas.'


def _agenda_transcricoes(user, args):
    from agenda import views as av

    qs = av._visible_transcriptions_for_user(user).select_related('owner')
    busca = (args.get('busca') or '').strip()
    if busca:
        qs = qs.filter(title__icontains=busca)
    status = str(args.get('status') or '').lower()
    if status:
        qs = qs.filter(status=STATUS_TRANSCRICAO.get(status, status))
    itens = list(qs.order_by('-created_at')[:_limite(args, 15)])
    if not itens:
        return 'Nenhuma transcrição encontrada' + (f' para "{busca}".' if busca else '.')
    linhas = [f'Transcrições/atas ({len(itens)}):']
    for t in itens:
        dono = 'sua' if t.owner_id == user.pk else f'de {_nome(t.owner)}'
        duracao = f' · {round(t.duration_seconds / 60)} min' if t.duration_seconds else ''
        linhas.append(f'#{t.pk} · {t.title} · {_dh(t.created_at)} · {t.get_status_display()}{duracao} · {dono}')
    return '\n'.join(linhas)


def _lista(valor):
    return valor if isinstance(valor, list) else []


def _agenda_transcricao(user, args):
    from agenda import views as av
    from reunioes.models import Reuniao

    t = (av._visible_transcriptions_for_user(user).select_related('owner', 'event')
         .filter(pk=_inteiro(args.get('transcricao_id'), 'transcricao_id')).first())
    if t is None:
        return 'Transcrição não encontrada — ou não foi compartilhada com você.'
    dono = t.owner_id == user.pk
    duracao = f' · {round(t.duration_seconds / 60)} min' if t.duration_seconds else ''
    linhas = [f'Transcrição #{t.pk}: {t.title}',
              f'Gravada em {_dh(t.created_at)} · {t.get_status_display()}{duracao} · '
              f'{"sua" if dono else "de " + _nome(t.owner)}',
              f'Tela: {_url(f"/agenda/transcricoes/{t.pk}/")}']
    if t.status == 'error' and t.error_message:
        linhas.append(f'Erro: {_cortar(t.error_message, 300)}')
    if t.status in ('recording', 'processing'):
        linhas.append(f'Ainda não terminou (etapa: {t.get_etapa_display() or "—"}).')
    if t.event_id:
        linhas.append(f'Evento: #{t.event_id} {t.event.title} · {_periodo(t.event.start, t.event.end, t.event.all_day)}')
        reuniao = Reuniao.objects.filter(evento_id=t.event_id).first()
        if reuniao:
            linhas.append(f'Reunião: #{reuniao.pk} {reuniao.titulo}')
    if t.status == 'completed':
        linhas.append(f'Tipo: {t.get_meeting_type_detected_display()} · clima: {t.get_sentiment_display()}')
    if _lista(t.participants_identified):
        linhas.append('Participantes citados: ' + ', '.join(map(str, t.participants_identified)))
    if _lista(t.tags):
        linhas.append('Tags: ' + ', '.join(map(str, t.tags)))
    if t.summary:
        linhas += ['', 'RESUMO', _cortar(t.summary, 6000)]
    if _lista(t.sections):
        linhas += ['', 'SEÇÕES']
        for s in t.sections[:20]:
            if isinstance(s, dict):
                tempo = f' ({s["duration_estimate"]})' if s.get('duration_estimate') else ''
                linhas.append(f'• {s.get("title") or "Seção"}{tempo}: {_cortar(s.get("content"), 500)}')
            else:
                linhas.append(f'• {_cortar(str(s), 500)}')
    for titulo, itens in (('DECISÕES', t.key_decisions), ('ITENS DE AÇÃO', t.action_items),
                          ('RISCOS', t.risks), ('EVENTOS SUGERIDOS', t.suggested_events)):
        if _lista(itens):
            linhas += ['', titulo] + [f'• {_cortar(_item(i), 600)}' for i in itens[:30]]

    tarefas = list(t.tasks_created.select_related('assigned_to').order_by('pk'))
    if tarefas:
        vivas, gestores = {}, []
        try:
            from impulso import importacao
            from impulso.utils import is_impulso_member

            if is_impulso_member(user):
                vivas = importacao.importacoes_vivas(user, [x.pk for x in tarefas])
                gestores = list(importacao.gestores_para(user)[:30])
        except Exception as exc:                                # noqa: BLE001 — o Impulso é um extra
            logger.debug('Impulso indisponível na transcrição %s: %s', t.pk, exc)
        linhas += ['', 'TAREFAS CRIADAS']
        for x in tarefas:
            prazo = f' · prazo {_dh(x.due_date)}' if x.due_date else ''
            meta = vivas.get(x.pk)
            impulso = f' · no Impulso (meta #{meta.pk}, {meta.get_aprovacao_display()})' if meta else ''
            linhas.append(f'tarefa #{x.pk} · {x.title} · com {_nome(x.assigned_to)} · '
                          f'{x.get_status_display()}{prazo}{impulso}')
        if gestores:
            linhas.append('Gestores que você pode escolher ao levar uma tarefa ao Impulso: '
                          + _nomes(gestores, 30))

    if dono or av._is_superadmin(user):
        compartilhada = list(t.shared_with.all())
        linhas += ['', 'Compartilhada com: ' + (_nomes(compartilhada) if compartilhada else 'ninguém')]
    if dono:
        linhas.append('Como dona(o), você pode compartilhar, agendar eventos sugeridos, atribuir '
                      'tarefas e reprocessar.')
    elif av._can_reprocess_transcription(user, t):
        linhas.append('Como SUPERADMIN, você pode reprocessar.')

    texto = t.formatted_transcription or t.raw_transcription or ''
    if _sim(args.get('incluir_texto')):
        inicio = max(0, _inteiro(args.get('a_partir_de') or 0, 'a_partir_de'))
        trecho = texto[inicio:inicio + LIMITE_TEXTO]
        linhas += ['', f'TEXTO (caracteres {inicio}–{inicio + len(trecho)} de {len(texto)})',
                   trecho or '(sem texto neste trecho)']
        if inicio + LIMITE_TEXTO < len(texto):
            linhas.append(f'… continua: chame de novo com a_partir_de={inicio + LIMITE_TEXTO}.')
    elif texto:
        linhas.append('O texto completo não veio junto: use incluir_texto=true se precisar dele.')
    return '\n'.join(linhas)


# ─── Reuniões: leitura ───────────────────────────────────────────────────────

CAMINHOS_CONVITE = ('cargos', 'setores', 'grupos', 'coordenacoes')


def _linha_reuniao(r, user, n_convidados=None):
    partes = [f'#{r.pk}', _periodo(r.inicio, r.fim), r.titulo, r.get_status_display()]
    if r.tipo != r.REUNIAO:
        partes.append(r.get_tipo_display())
    partes.append('organizada por você' if r.organizador_id == user.pk
                  else f'organizador: {_nome(r.organizador)}')
    if n_convidados is not None:
        partes.append(f'{n_convidados} convidado(s)')
    partes.append(_url(f'/reunioes/{r.pk}/'))
    return ' · '.join(partes)


def _reunioes_lista(user, args):
    from reunioes.models import Reuniao
    from reunioes.permissoes import e_superadmin

    periodo = _normal(args.get('periodo') or 'proximas')
    if periodo not in ('proximas', 'passadas'):
        raise Invalido('periodo deve ser proximas ou passadas.')
    corte = timezone.now() - timedelta(hours=4)          # o mesmo corte da tela
    todas = _sim(args.get('incluir_outras')) and e_superadmin(user)
    if todas:
        qs = Reuniao.objects.all()
    else:
        minhas = Reuniao.objects.filter(Q(organizador=user) | Q(participantes__user=user)).values('id')
        qs = Reuniao.objects.filter(id__in=minhas)
    busca = (args.get('busca') or '').strip()
    if busca:
        qs = qs.filter(Q(titulo__icontains=busca) | Q(pauta__icontains=busca)
                       | Q(organizador__first_name__icontains=busca)
                       | Q(organizador__last_name__icontains=busca))
    if periodo == 'proximas':
        qs = qs.filter(inicio__gte=corte).exclude(status=Reuniao.CANCELADA).order_by('inicio')
    else:
        qs = qs.filter(Q(inicio__lt=corte) | Q(status=Reuniao.CANCELADA)).order_by('-inicio')
    limite = _limite(args, 20)
    itens = list(qs.select_related('organizador')
                 .annotate(n=Count('participantes', distinct=True))[:limite + 1])

    rotulo = ('Próximas reuniões' if periodo == 'proximas' else 'Reuniões anteriores e canceladas')
    rotulo += ' da rede inteira (SUPERADMIN)' if todas else ''
    if not itens:
        return f'{rotulo}: nenhuma' + (f' para "{busca}".' if busca else '.')
    linhas = [f'{rotulo} ({min(len(itens), limite)}):']
    linhas += [_linha_reuniao(r, user, r.n) for r in itens[:limite]]
    if len(itens) > limite:
        linhas.append('… há mais: refine a busca ou aumente o limite.')
    return '\n'.join(linhas)


def _reunioes_detalhe(user, args):
    from agenda import views as av
    from agenda.models import MeetingTranscription
    from reunioes.models import ConfiguracaoReunioes, Reuniao

    r = (Reuniao.objects.select_related('organizador')
         .filter(pk=_inteiro(args.get('reuniao_id'), 'reuniao_id')).first())
    if r is None:
        return 'Reunião não encontrada.'
    if not r.pode_ver(user):
        return 'Você não está nesta reunião (nem é o organizador), então não pode vê-la.'

    linhas = [f'Reunião #{r.pk}: {r.titulo}',
              f'Quando: {_periodo(r.inicio, r.fim)}' + ('' if r.fim else ' (sem fim previsto)'),
              f'Status: {r.get_status_display()} · tipo: {r.get_tipo_display()} · '
              f'ata: {"gerada ao gravar na sala" if r.gravar_ata else "desligada"}',
              f'Organizador: {"você" if r.organizador_id == user.pk else _nome(r.organizador)} '
              f'(id {r.organizador_id})',
              f'Detalhe: {_url(f"/reunioes/{r.pk}/")}']
    if not r.acabou:
        linhas.append(f'Sala de vídeo (só pelo navegador): {_url(f"/reunioes/{r.pk}/sala/")}')
    if r.evento_id:
        linhas.append(f'Evento na agenda: #{r.evento_id}')
    if r.pauta:
        linhas.append('Pauta: ' + _cortar(r.pauta, 3000))
    participantes = list(r.participantes.select_related('user'))
    linhas.append(f'Convidados ({len(participantes)}):' if participantes else 'Sem convidados.')
    for p in participantes:
        origem = p.get_origem_display() + (f' — {p.rotulo_origem}' if p.rotulo_origem else '')
        presenca = f'entrou {_dh(p.entrou_em)}' if p.entrou_em else 'não entrou na sala'
        linhas.append(f'  {_nome(p.user)} (id {p.user_id}) · {origem} · {presenca}')
    visitantes = list(r.visitantes.all())
    if visitantes:
        linhas.append('Visitantes (link público): '
                      + ', '.join(f'{v.nome} ({_dh(v.entrou_em)})' for v in visitantes))
    if r.pode_editar(user):
        if not ConfiguracaoReunioes.get().permitir_link_publico:
            linhas.append('Link de visitante: desligado na configuração do módulo.')
        elif r.token_publico:
            linhas.append('Link de visitante: '
                          + _url(reverse('reunioes:sala_publica', args=[r.token_publico]))
                          + ' (vale até a reunião ser encerrada ou cancelada)')
        else:
            linhas.append('Link de visitante: fechado.')
        linhas.append('Você pode editar, encerrar e cancelar esta reunião e mexer no link de visitante.')
    if r.evento_id:
        visiveis = set(av._visible_transcriptions_for_user(user)
                       .filter(event_id=r.evento_id).values_list('pk', flat=True))
        for t in MeetingTranscription.objects.filter(event_id=r.evento_id).order_by('-id'):
            acesso = '' if t.pk in visiveis else ' (não compartilhada com você)'
            linhas.append(f'Ata: #{t.pk} {t.title} · {t.get_status_display()}{acesso}')
    return '\n'.join(linhas)


def _reunioes_opcoes_convite(user, args):
    from reunioes import publico

    tipo = _normal(args.get('tipo'))
    if tipo not in CAMINHOS_CONVITE:
        raise Invalido('tipo deve ser cargos, setores, grupos ou coordenacoes.')
    busca = _normal(args.get('busca'))
    itens = [i for i in getattr(publico, tipo)(user) if not busca or busca in _normal(i['nome'])]
    if not itens:
        return f'Nada encontrado em {tipo}' + (f' para "{args.get("busca")}".' if busca else '.')
    limite = _limite(args, 40, 100)
    regra = 'o id é o próprio nome do cargo' if tipo == 'cargos' else 'use o id'
    linhas = [f'Opções em {tipo} ({len(itens)}; {regra}):']
    for i in itens[:limite]:
        ident = f'"{i["id"]}"' if tipo == 'cargos' else f'id {i["id"]}'
        linhas.append(f'{ident} · {i["nome"]} · {len(i["membros"])} pessoa(s)')
    if len(itens) > limite:
        linhas.append('… há mais: use busca.')
    return '\n'.join(linhas)


# ─── Agenda: ações ───────────────────────────────────────────────────────────
# Cada ação tem duas metades. A prévia valida, resolve nomes e descreve o que
# vai acontecer — sem gravar nada — e devolve os dados já normalizados. A
# execução recebe exatamente esses dados e chama a view da tela.

TIPOS_SOLICITACAO = {'reuniao': 'meeting', 'chamada': 'call', 'horario': 'appointment',
                     'meeting': 'meeting', 'call': 'call', 'appointment': 'appointment'}


def _ocorrencias_semanais(inicio, ate):
    """Quantas vezes o evento semanal acontece (a agenda repete até `ate` ou por 3 meses)."""
    atual = _local(inicio)
    limite = ate or (atual + timedelta(days=90)).date()
    n = 0
    while atual.date() <= limite:
        n += 1
        atual += timedelta(weeks=1)
    return n


def _previa_criar_evento(user, args):
    from agenda.models import CalendarEvent

    titulo = _titulo(args, obrigatorio='Informe o título do evento.')
    tipo = _tipo_evento(args.get('tipo'))
    dia_inteiro = _sim(args.get('dia_inteiro'))
    if dia_inteiro:
        d_ini = _data(args.get('inicio'), 'inicio')
        d_fim = _data(args['fim'], 'fim') if args.get('fim') else d_ini
        inicio, fim = _meia_noite(d_ini), _meia_noite(d_fim + timedelta(days=1))
    else:
        inicio = _data_hora(args.get('inicio'), 'inicio')
        fim = _data_hora(args['fim'], 'fim') if args.get('fim') else inicio + timedelta(hours=1)
    if fim <= inicio:
        raise Invalido('O fim precisa ser depois do início.')
    convidados = _pessoas(_ids(args.get('participantes'), 'participantes'), eu=user)
    semanal = _sim(args.get('repetir_semanal'))
    ate = _data(args['repetir_ate'], 'repetir_ate') if semanal and args.get('repetir_ate') else None
    if ate and ate < _local(inicio).date():
        raise Invalido('repetir_ate não pode ser antes do início.')
    descricao = str(args.get('descricao') or '').strip()
    local = str(args.get('local') or '').strip()[:255]
    link = str(args.get('link') or '').strip()[:500]
    privado = _sim(args.get('privado'))

    rotulo = dict(CalendarEvent.TYPE_CHOICES)[tipo].lower()
    linhas = [f'Criar {rotulo} "{titulo}" em {_periodo(inicio, fim, dia_inteiro)} na sua agenda.']
    if local:
        linhas.append(f'Local: {local}')
    if link:
        linhas.append(f'Link: {link}')
    if descricao:
        linhas.append('Descrição: ' + _cortar(descricao, 300))
    if privado:
        linhas.append('Privado: os outros veem só "ocupado".')
    if convidados:
        linhas.append(f'Convidados ({len(convidados)}): {_nomes(convidados)} — cada um recebe o '
                      'convite e responde na agenda.')
    if tipo == 'call':
        linhas.append('Por ser chamada, o portal já cria a sala de vídeo junto.')
    if tipo == 'task':
        linhas.append('Por ser tarefa, vira tarefa de verdade em /users/tasks/ (sua e de cada convidado).')
    if semanal:
        quando = f'até {ate:%d/%m/%Y}' if ate else 'por 3 meses (padrão da agenda)'
        linhas.append(f'Repete toda semana ({DIAS[_local(inicio).weekday()]}) {quando}: '
                      f'{_ocorrencias_semanais(inicio, ate)} ocorrência(s).')
    return '\n'.join(linhas), {
        'title': titulo[:255], 'description': descricao, 'event_type': tipo,
        'start': inicio.isoformat(), 'end': fim.isoformat(), 'all_day': dia_inteiro,
        'location': local, 'link': link, 'is_private': privado,
        'recurrence': 'weekly' if semanal else 'none',
        'recurrence_until': ate.isoformat() if ate else None,
        'participants': [u.pk for u in convidados],
    }


def _exec_criar_evento(user, dados):
    from agenda import views as av
    from agenda.models import CalendarEvent

    r = chamar_view(av.api_event_create, user, '/agenda/api/events/create/', dados=dados, json_corpo=True)
    corpo = r['json'] if isinstance(r['json'], dict) else {}
    if r['status'] != 201 or not corpo.get('id'):
        return 'O evento não foi criado: ' + _falha(r)
    ev = CalendarEvent.objects.get(pk=corpo['id'])
    linhas = [f'Evento criado: #{ev.pk} "{ev.title}" em {_periodo(ev.start, ev.end, ev.all_day)}.']
    if dados['participants']:
        linhas.append(f'Convite enviado para {len(dados["participants"])} pessoa(s).')
    if corpo.get('sala'):
        linhas.append(f'Sala de vídeo: {_url(corpo["sala"])}')
    if corpo.get('tarefa_id'):
        linhas.append(f'Tarefa criada: #{corpo["tarefa_id"]} ({_url("/users/tasks/")}).')
    if ev.recurrence_rule == 'weekly':
        linhas.append(f'Série semanal: {1 + ev.recurrence_children.count()} ocorrência(s).')
    linhas.append(f'Agenda: {_url("/agenda/")}')
    return '\n'.join(linhas)


def _meu_evento(user, args):
    from agenda.models import CalendarEvent

    ev = CalendarEvent.objects.filter(pk=_inteiro(args.get('evento_id'), 'evento_id')).first()
    if ev is None:
        raise Invalido('Evento não encontrado.')
    if ev.owner_id != user.pk:
        raise Invalido('Só quem criou o evento pode alterá-lo ou excluí-lo. Se você foi convidado, '
                       'responda o convite (agenda_responder_convite).')
    return ev


def _previa_editar_evento(user, args):
    from agenda.models import CalendarEvent

    ev = _meu_evento(user, args)
    dados, mudancas = {}, []
    if args.get('titulo'):
        dados['title'] = _titulo(args)[:255]
        mudancas.append(f'título: "{dados["title"]}"')
    for campo, chave, rotulo, limite in (('descricao', 'description', 'descrição', None),
                                         ('local', 'location', 'local', 255),
                                         ('link', 'link', 'link', 500)):
        if args.get(campo) is not None:
            valor = str(args[campo]).strip()
            dados[chave] = valor[:limite] if limite else valor
            mudancas.append(f'{rotulo}: {_cortar(valor, 200) or "(apagar)"}')
    if args.get('tipo'):
        dados['event_type'] = _tipo_evento(args['tipo'])
        mudancas.append(f'tipo: {dict(CalendarEvent.TYPE_CHOICES)[dados["event_type"]]}')
    if args.get('privado') is not None:
        dados['is_private'] = _sim(args['privado'])
        mudancas.append('passa a ser privado' if dados['is_private'] else 'deixa de ser privado')
    if args.get('inicio') or args.get('fim'):
        inicio = _data_hora(args['inicio'], 'inicio') if args.get('inicio') else ev.start
        # Remarcar só o início mantém a duração: "passa para as 15h" não encurta a reunião.
        fim = _data_hora(args['fim'], 'fim') if args.get('fim') else inicio + (ev.end - ev.start)
        if fim <= inicio:
            raise Invalido('O fim precisa ser depois do início.')
        dados.update(start=inicio.isoformat(), end=fim.isoformat())
        mudancas.append(f'horário: {_periodo(inicio, fim, ev.all_day)} '
                        f'(era {_periodo(ev.start, ev.end, ev.all_day)})')
    atuais = list(ev.event_participants.values_list('user_id', flat=True))
    novos = [u for u in _pessoas(_ids(args.get('adicionar_participantes'), 'adicionar_participantes'), eu=user)
             if u.pk not in atuais]
    tirar = [i for i in _ids(args.get('remover_participantes'), 'remover_participantes') if i in atuais]
    if novos or tirar:
        dados['participants'] = [i for i in atuais if i not in tirar] + [u.pk for u in novos]
        if novos:
            mudancas.append(f'convidar {_nomes(novos)} (recebem convite)')
        if tirar:
            mudancas.append(f'tirar do evento {_nomes(User.objects.filter(pk__in=tirar))}')
    if not dados:
        raise Invalido('Diga o que mudar no evento (título, horário, local, convidados...).')
    linhas = [f'Alterar o evento #{ev.pk} "{ev.title}":'] + [f'• {m}' for m in mudancas]
    if 'start' in dados and ev.participants.exclude(pk=user.pk).exists():
        linhas.append('Quem já aceitou o convite recebe aviso da remarcação.')
    return '\n'.join(linhas), {'evento_id': ev.pk, 'dados': dados}


def _exec_editar_evento(user, d):
    from agenda import views as av
    from agenda.models import CalendarEvent

    r = chamar_view(av.api_event_update, user, f'/agenda/api/events/{d["evento_id"]}/update/',
                    dados=d['dados'], json_corpo=True, pk=d['evento_id'])
    if not _ok_json(r):
        return 'O evento não foi alterado: ' + _falha(r)
    ev = CalendarEvent.objects.get(pk=d['evento_id'])
    linhas = [f'Evento #{ev.pk} atualizado: "{ev.title}" em {_periodo(ev.start, ev.end, ev.all_day)}.']
    if r['json'].get('sala'):
        linhas.append(f'Sala de vídeo: {_url(r["json"]["sala"])}')
    return '\n'.join(linhas)


def _previa_excluir_evento(user, args):
    from reunioes.models import Reuniao

    ev = _meu_evento(user, args)
    serie = _sim(args.get('toda_a_serie')) and ev.recurrence_rule != 'none'
    if serie:
        pai = ev.recurrence_parent or ev
        linhas = [f'Excluir a série inteira de "{ev.title}" ({1 + pai.recurrence_children.count()} evento(s)).']
    else:
        linhas = [f'Excluir o evento #{ev.pk} "{ev.title}" ({_periodo(ev.start, ev.end, ev.all_day)}).']
    if ev.event_participants.exists():
        linhas.append('Os convidados não recebem aviso da exclusão.')
    reuniao = Reuniao.objects.filter(evento=ev).first()
    if reuniao and not reuniao.acabou:
        linhas.append(f'A reunião #{reuniao.pk} continua marcada em /reunioes/; para desmarcar para '
                      'todos, use reunioes_cancelar.')
    linhas.append('Não dá para desfazer.')
    return '\n'.join(linhas), {'evento_id': ev.pk, 'toda_a_serie': serie}


def _exec_excluir_evento(user, d):
    from agenda import views as av

    r = chamar_view(av.api_event_delete, user, f'/agenda/api/events/{d["evento_id"]}/delete/',
                    dados={'delete_all_recurrences': d['toda_a_serie']}, json_corpo=True, pk=d['evento_id'])
    if not _ok_json(r):
        return 'O evento não foi excluído: ' + _falha(r)
    return 'A série foi excluída da agenda.' if d['toda_a_serie'] else f'Evento #{d["evento_id"]} excluído da agenda.'


def _previa_responder_convite(user, args):
    from agenda.models import EventParticipant

    acao = {'aceitar': 'accept', 'recusar': 'reject'}.get(_normal(args.get('resposta')))
    if not acao:
        raise Invalido('resposta deve ser aceitar ou recusar.')
    convite = (EventParticipant.objects.select_related('event', 'event__owner')
               .filter(pk=_inteiro(args.get('convite_id'), 'convite_id'), user=user).first())
    if convite is None:
        raise Invalido('Convite não encontrado entre os seus — veja agenda_convites.')
    if convite.status != 'pending':
        raise Invalido(f'Esse convite já foi respondido ({convite.get_status_display()}).')
    ev, obs = convite.event, str(args.get('observacao') or '').strip()
    linhas = [f'{"Aceitar" if acao == "accept" else "Recusar"} o convite de {_nome(ev.owner)} para '
              f'"{ev.title}" ({_periodo(ev.start, ev.end, ev.all_day)}).']
    if acao == 'accept':
        linhas.append('O evento entra na sua agenda.')
    linhas.append(f'{_nome(ev.owner)} recebe aviso da sua resposta.')
    if obs:
        linhas.append(f'Observação: {obs}')
    return '\n'.join(linhas), {'convite_id': convite.pk, 'action': acao, 'notes': obs}


def _exec_responder_convite(user, d):
    from agenda import views as av

    r = chamar_view(av.api_event_invitation_respond, user, f'/agenda/api/invitations/{d["convite_id"]}/respond/',
                    dados={'action': d['action'], 'notes': d['notes']}, json_corpo=True, pk=d['convite_id'])
    if not _ok_json(r):
        return 'O convite não foi respondido: ' + _falha(r)
    return r['json'].get('message') or 'Convite respondido.'


def _previa_solicitar_reuniao(user, args):
    from agenda.models import CalendarEvent, MeetingRequest

    alvo = _pessoas([_inteiro(args.get('pessoa_id'), 'pessoa_id')])[0]
    if alvo.pk == user.pk:
        raise Invalido('Não dá para pedir reunião a você mesmo.')
    titulo = _titulo(args, obrigatorio='Informe o título da solicitação.')
    tipo = TIPOS_SOLICITACAO.get(_normal(args.get('tipo') or 'reuniao'))
    if not tipo:
        raise Invalido('tipo deve ser reuniao, chamada ou horario.')
    inicio = _data_hora(args.get('inicio'), 'inicio')
    fim = _data_hora(args['fim'], 'fim') if args.get('fim') else inicio + timedelta(minutes=30)
    if fim <= inicio:
        raise Invalido('O fim precisa ser depois do início.')
    descricao = str(args.get('descricao') or '').strip()
    local = str(args.get('local') or '').strip()[:255]

    linhas = [f'Pedir a {_nome(alvo)}: {dict(MeetingRequest.TYPE_CHOICES)[tipo].lower()} "{titulo}" '
              f'em {_periodo(inicio, fim)}.']
    if local:
        linhas.append(f'Local sugerido: {local}')
    if descricao:
        linhas.append('Motivo: ' + _cortar(descricao, 300))
    if CalendarEvent.objects.filter(owner=alvo, start__lt=fim, end__gt=inicio).exists():
        linhas.append(f'Atenção: {_nome(alvo)} já tem compromisso nesse horário (veja agenda_disponibilidade).')
    linhas.append(f'{_nome(alvo)} recebe a solicitação; se aceitar, o evento entra na agenda de vocês dois.')
    return '\n'.join(linhas), {
        'pessoa_id': alvo.pk, 'title': titulo[:255], 'description': descricao, 'meeting_type': tipo,
        'proposed_start': inicio.isoformat(), 'proposed_end': fim.isoformat(), 'location': local,
    }


def _exec_solicitar_reuniao(user, d):
    from agenda import views as av
    from agenda.models import MeetingRequest

    campos = {k: v for k, v in d.items() if k != 'pessoa_id'}
    r = chamar_view(av.request_meeting, user, f'/agenda/solicitar/{d["pessoa_id"]}/',
                    dados=campos, user_id=d['pessoa_id'])
    if not _sem_erro(r):
        return 'A solicitação não foi enviada: ' + _falha(r)
    pedido = (MeetingRequest.objects.filter(requester=user, target_id=d['pessoa_id'], title=d['title'])
              .order_by('-pk').first())
    ref = f' (solicitação #{pedido.pk})' if pedido else ''
    return f'Solicitação enviada{ref}. Acompanhe em {_url("/agenda/solicitacoes/?tab=sent")}.'


def _previa_responder_solicitacao(user, args):
    from agenda.models import MeetingRequest

    acao = _normal(args.get('acao'))
    if acao not in ('aceitar', 'recusar', 'cancelar'):
        raise Invalido('acao deve ser aceitar, recusar ou cancelar.')
    m = (MeetingRequest.objects.select_related('requester', 'target')
         .filter(pk=_inteiro(args.get('solicitacao_id'), 'solicitacao_id')).first())
    if m is None or user.pk not in (m.requester_id, m.target_id):
        raise Invalido('Solicitação não encontrada entre as suas — veja agenda_solicitacoes.')
    if acao in ('aceitar', 'recusar') and m.target_id != user.pk:
        raise Invalido('Só quem recebeu a solicitação aceita ou recusa; quem pediu pode cancelar.')
    if acao == 'cancelar' and m.requester_id != user.pk:
        raise Invalido('Só quem pediu pode cancelar a solicitação; quem recebeu aceita ou recusa.')
    if m.status != 'pending':
        raise Invalido(f'Essa solicitação não está mais pendente ({m.get_status_display()}).')
    quando, obs = _periodo(m.proposed_start, m.proposed_end), str(args.get('observacao') or '').strip()
    if acao == 'aceitar':
        linhas = [f'Aceitar "{m.title}", pedido por {_nome(m.requester)}, para {quando}.',
                  f'O evento entra na sua agenda e na de {_nome(m.requester)}, que recebe aviso.']
    elif acao == 'recusar':
        linhas = [f'Recusar "{m.title}", pedido por {_nome(m.requester)} para {quando}.',
                  f'{_nome(m.requester)} recebe aviso.']
    else:
        linhas = [f'Cancelar o seu pedido "{m.title}" a {_nome(m.target)} ({quando}).',
                  f'{_nome(m.target)} recebe aviso.']
    if obs and acao != 'cancelar':
        linhas.append(f'Observação: {obs}')
    return '\n'.join(linhas), {'solicitacao_id': m.pk, 'acao': acao, 'observacao': obs}


def _exec_responder_solicitacao(user, d):
    from agenda import views as av
    from agenda.models import MeetingRequest

    view = {'aceitar': av.meeting_request_accept, 'recusar': av.meeting_request_reject,
            'cancelar': av.meeting_request_cancel}[d['acao']]
    r = chamar_view(view, user, f'/agenda/solicitacoes/{d["solicitacao_id"]}/{d["acao"]}/',
                    dados={'response_notes': d['observacao']}, pk=d['solicitacao_id'])
    if not _sem_erro(r):
        return 'Não foi possível: ' + _falha(r)
    m = MeetingRequest.objects.get(pk=d['solicitacao_id'])
    texto = f'Solicitação #{m.pk} agora está {m.get_status_display().lower()}.'
    if m.status == 'accepted' and m.created_event_id:
        texto += f' Evento criado na agenda: #{m.created_event_id}.'
    return texto


def _transcricao_visivel(user, args):
    from agenda import views as av

    t = (av._visible_transcriptions_for_user(user)
         .filter(pk=_inteiro(args.get('transcricao_id'), 'transcricao_id')).first())
    if t is None:
        raise Invalido('Transcrição não encontrada — ou não foi compartilhada com você.')
    return t


def _minha_transcricao(user, args):
    t = _transcricao_visivel(user, args)
    if t.owner_id != user.pk:
        raise Invalido('Só quem gravou a transcrição pode fazer isso; você tem acesso só de leitura.')
    return t


def _transcricao_gerenciavel(user, args):
    from agenda import views as av

    t = (av._manageable_transcriptions_for_user(user)
         .filter(pk=_inteiro(args.get('transcricao_id'), 'transcricao_id')).first())
    if t is None:
        raise Invalido('Só quem gravou a transcrição (ou o SUPERADMIN) pode fazer isso — ou ela não existe.')
    return t


def _tarefa_da_transcricao(t, args):
    tarefa = (t.tasks_created.select_related('assigned_to')
              .filter(pk=_inteiro(args.get('tarefa_id'), 'tarefa_id')).first())
    if tarefa is None:
        raise Invalido('Essa tarefa não é desta transcrição — veja agenda_transcricao.')
    return tarefa


def _link_transcricao(tid):
    return _url(f'/agenda/transcricoes/{tid}/')


def _previa_compartilhar_transcricao(user, args):
    t = _minha_transcricao(user, args)
    atuais = list(t.shared_with.values_list('pk', flat=True))
    novos = [u for u in _pessoas(_ids(args.get('adicionar'), 'adicionar'), eu=user) if u.pk not in atuais]
    tirar = [i for i in _ids(args.get('remover'), 'remover') if i in atuais]
    if not novos and not tirar:
        raise Invalido('Nada muda: diga com quem compartilhar (adicionar) ou de quem tirar o acesso (remover).')
    final = [i for i in atuais if i not in tirar] + [u.pk for u in novos]
    linhas = [f'Compartilhamento da transcrição #{t.pk} "{t.title}":']
    if novos:
        linhas.append(f'• dar acesso de leitura a {_nomes(novos)} — cada um recebe aviso')
    if tirar:
        linhas.append(f'• tirar o acesso de {_nomes(User.objects.filter(pk__in=tirar))}')
    linhas.append(f'Fica compartilhada com {len(final)} pessoa(s).')
    return '\n'.join(linhas), {'transcricao_id': t.pk, 'user_ids': final}


def _exec_compartilhar_transcricao(user, d):
    from agenda import views as av

    tid = d['transcricao_id']
    r = chamar_view(av.api_transcription_share, user, f'/agenda/api/transcricoes/{tid}/compartilhar/',
                    dados={'user_ids': d['user_ids']}, json_corpo=True, pk=tid)
    if not _ok_json(r):
        return 'O compartilhamento não mudou: ' + _falha(r)
    return (f'Compartilhamento atualizado: {r["json"].get("shared_count", 0)} pessoa(s) com acesso. '
            f'{_link_transcricao(tid)}')


def _previa_agendar_da_transcricao(user, args):
    t = _minha_transcricao(user, args)
    titulo = _titulo(args, obrigatorio='Informe o título do evento.')
    inicio = _data_hora(args.get('inicio'), 'inicio')
    if args.get('fim'):
        fim = _data_hora(args['fim'], 'fim')
    else:
        fim = inicio + timedelta(minutes=max(_inteiro(args.get('duracao_minutos') or 60, 'duracao_minutos'), 5))
    if fim <= inicio:
        raise Invalido('O fim precisa ser depois do início.')
    descricao = str(args.get('descricao') or '').strip()
    linhas = [f'Marcar a reunião "{titulo}" em {_periodo(inicio, fim)} na sua agenda, a partir da '
              f'transcrição "{t.title}".']
    if descricao:
        linhas.append('Descrição: ' + _cortar(descricao, 300))
    linhas.append('Vai sem convidados: dá para convidar depois com agenda_editar_evento.')
    return '\n'.join(linhas), {'transcricao_id': t.pk, 'title': titulo[:255], 'description': descricao,
                               'start': inicio.isoformat(), 'end': fim.isoformat()}


def _exec_agendar_da_transcricao(user, d):
    from agenda import views as av

    tid = d['transcricao_id']
    campos = {k: v for k, v in d.items() if k != 'transcricao_id'}
    r = chamar_view(av.api_transcription_schedule, user, f'/agenda/api/transcricoes/{tid}/agendar/',
                    dados=campos, json_corpo=True, pk=tid)
    if not _ok_json(r):
        return 'O evento não foi marcado: ' + _falha(r)
    return f'Evento #{r["json"].get("event_id")} marcado na sua agenda: "{d["title"]}". {_url("/agenda/")}'


def _previa_atribuir_tarefa(user, args):
    t = _minha_transcricao(user, args)
    tarefa = _tarefa_da_transcricao(t, args)
    alvo = _pessoas([_inteiro(args.get('pessoa_id'), 'pessoa_id')])[0]
    if alvo.pk == tarefa.assigned_to_id:
        raise Invalido(f'A tarefa já está com {_nome(alvo)}.')
    return (f'Passar a tarefa #{tarefa.pk} "{tarefa.title}" de {_nome(tarefa.assigned_to)} para {_nome(alvo)}.\n'
            'A troca não manda aviso automático.'), {
        'transcricao_id': t.pk, 'tarefa_id': tarefa.pk, 'user_id': alvo.pk}


def _exec_atribuir_tarefa(user, d):
    from agenda import views as av

    tid, tarefa_id = d['transcricao_id'], d['tarefa_id']
    r = chamar_view(av.api_transcription_assign_task, user,
                    f'/agenda/api/transcricoes/{tid}/tasks/{tarefa_id}/assign/',
                    dados={'user_id': d['user_id']}, json_corpo=True, pk=tid, task_id=tarefa_id)
    if not _ok_json(r):
        return 'A tarefa não foi atribuída: ' + _falha(r)
    return r['json'].get('message') or 'Tarefa atribuída.'


def _previa_tarefa_impulso(user, args):
    from impulso import importacao
    from impulso.utils import is_impulso_manager, is_impulso_member

    t = _transcricao_visivel(user, args)
    tarefa = _tarefa_da_transcricao(t, args)
    if not is_impulso_member(user):
        raise Invalido('Você não participa do Impulso.')
    viva = importacao.importacoes_vivas(user, [tarefa.pk]).get(tarefa.pk)
    if viva is not None:
        raise Invalido(f'Você já levou essa tarefa ao Impulso (meta #{viva.pk}, {viva.get_aprovacao_display()}).')
    possiveis = importacao.gestores_para(user)
    gestor = (possiveis.filter(pk=_inteiro(args['gestor_id'], 'gestor_id')).first()
              if args.get('gestor_id') else None)
    if gestor is None:
        raise Invalido('Escolha um gestor que você pode indicar: '
                       + (_nomes(list(possiveis[:30]), 30) or 'nenhum disponível') + '.')
    prazo = _data(args.get('prazo'), 'prazo')
    if prazo < timezone.localdate():
        raise Invalido('O prazo não pode ser anterior a hoje.')
    titulo = ' '.join(str(args.get('titulo') or tarefa.title).split())[:200]
    descricao = str(args.get('descricao') or tarefa.description or '').strip()
    if not descricao:
        raise Invalido('Informe a descrição da meta.')
    precisa = _sim(args.get('precisa_aprovacao'), padrao=True)
    aprovada = is_impulso_manager(user) or not precisa
    linhas = [f'Levar a tarefa "{tarefa.title}" ao Impulso como meta sua: "{titulo}", prazo '
              f'{prazo:%d/%m/%Y}, gestor {_nome(gestor)}.',
              'Entra direto no seu Kanban.' if aprovada
              else f'Vai para {_nome(gestor)} aprovar antes de entrar no seu Kanban.']
    if gestor.pk != user.pk:
        linhas.append(f'{_nome(gestor)} recebe aviso.')
    return '\n'.join(linhas), {
        'transcricao_id': t.pk, 'tarefa_id': tarefa.pk, 'gestor': gestor.pk, 'prazo': prazo.isoformat(),
        'titulo': titulo, 'descricao': descricao, 'precisa_aprovacao': 'sim' if precisa else 'nao'}


def _exec_tarefa_impulso(user, d):
    from agenda import views as av

    tid, tarefa_id = d['transcricao_id'], d['tarefa_id']
    campos = {k: v for k, v in d.items() if k not in ('transcricao_id', 'tarefa_id')}
    r = chamar_view(av.api_transcription_task_impulso, user,
                    f'/agenda/api/transcricoes/{tid}/tasks/{tarefa_id}/impulso/',
                    dados=campos, json_corpo=True, pk=tid, task_id=tarefa_id)
    if not _ok_json(r) or not r['json'].get('url'):
        return 'A tarefa não foi levada ao Impulso: ' + _falha(r)
    return f'{r["json"].get("message")} Meta: {_url(r["json"]["url"])}'


def _previa_reprocessar_transcricao(user, args):
    t = _transcricao_gerenciavel(user, args)
    if t.status == 'recording' and not t.partes_recebidas:
        raise Invalido('Essa gravação ainda não recebeu nenhuma parte de áudio.')
    linhas = [f'Reprocessar a transcrição #{t.pk} "{t.title}" (hoje: {t.get_status_display()}).',
              'Roda em segundo plano: a transcrição e a análise da IA são refeitas, e a tela mostra o andamento.']
    if t.status == 'recording':
        linhas.append('A gravação ainda estava aberta: ela é fechada com as partes que chegaram.')
    return '\n'.join(linhas), {'transcricao_id': t.pk}


def _exec_reprocessar_transcricao(user, d):
    from agenda import views as av

    tid = d['transcricao_id']
    r = chamar_view(av.api_transcription_reprocess, user, f'/agenda/api/transcricoes/{tid}/reprocess/',
                    dados={}, json_corpo=True, pk=tid)
    if not _ok_json(r, status=202):
        return 'O reprocessamento não começou: ' + _falha(r)
    return f'Reprocessamento iniciado em segundo plano. Acompanhe em {_link_transcricao(tid)}.'


def _previa_descartar_gravacao(user, args):
    t = _transcricao_gerenciavel(user, args)
    if t.status not in ('recording', 'error') or (t.raw_transcription or '').strip():
        raise Invalido('Só dá para descartar gravação em andamento ou que falhou antes de ter texto.')
    return (f'Descartar a gravação #{t.pk} "{t.title}" ({t.get_status_display()}, '
            f'{t.partes_recebidas} parte(s) de áudio): apaga o áudio e o registro. Não dá para desfazer.'), {
        'transcricao_id': t.pk}


def _exec_descartar_gravacao(user, d):
    from agenda import views as av

    tid = d['transcricao_id']
    r = chamar_view(av.api_transcription_discard, user, f'/agenda/api/transcricoes/{tid}/descartar/',
                    dados={}, json_corpo=True, pk=tid)
    if not _ok_json(r):
        return 'A gravação não foi descartada: ' + _falha(r)
    return f'Gravação #{tid} descartada.'


# ─── Reuniões: ações ─────────────────────────────────────────────────────────

TIPOS_REUNIAO = {'reuniao': 'REUNIAO', 'entrevista': 'ENTREVISTA'}
_ROTULO_CAMINHO = {'cargos': 'cargo', 'setores': 'setor', 'grupos': 'grupo', 'coordenacoes': 'coordenação'}


def _tipo_reuniao(valor, padrao='REUNIAO'):
    texto = _normal(valor)
    if not texto:
        return padrao
    tipo = TIPOS_REUNIAO.get(texto)
    if not tipo:
        raise Invalido('tipo deve ser reuniao ou entrevista.')
    return tipo


def _caminhos_de_convite(args, catalogo, prefixo=''):
    """Cargos/setores/grupos/coordenações pedidos, conferidos no catálogo da própria tela."""
    escolhidos = {}
    for chave in CAMINHOS_CONVITE:
        valores = args.get(prefixo + chave) or []
        if not isinstance(valores, (list, tuple)):
            valores = [valores]
        por_id = {str(item['id']): item for item in catalogo.get(chave, [])}
        por_nome = {_normal(item['id']): item for item in catalogo.get(chave, [])}
        escolha = []
        for valor in valores:
            item = por_id.get(str(valor).strip()) or por_nome.get(_normal(valor))
            if item is None:
                raise Invalido(f'{chave}: não achei "{valor}". Use reunioes_opcoes_convite (tipo={chave}).')
            escolha.append(str(item['id']))
        escolhidos[chave] = escolha
    return escolhidos


def _rotulos_caminhos(escolhidos, catalogo):
    return [f'{_ROTULO_CAMINHO[chave]} {item["nome"]} ({len(item["membros"])} pessoa(s))'
            for chave, ids in escolhidos.items()
            for item in catalogo.get(chave, []) if str(item['id']) in ids]


def _post_reuniao(d):
    """O formulário de /reunioes/nova/ preenchido com o que a prévia aprovou."""
    post = {'titulo': d['titulo'], 'inicio': d['inicio'], 'fim': d.get('fim') or '',
            'pauta': d.get('pauta') or '', 'tipo': d['tipo'],
            'usuarios': [str(i) for i in d.get('usuarios') or []]}
    for chave in CAMINHOS_CONVITE:
        post[chave] = list(d.get(chave) or [])
    if d.get('gravar_ata'):
        post['gravar_ata'] = 'on'
    return post


def _id_da_reuniao(destino):
    achado = re.search(r'/reunioes/(\d+)/$', destino or '')
    return int(achado.group(1)) if achado else None


def _previa_criar_reuniao(user, args):
    from reunioes import publico

    titulo = _titulo(args, obrigatorio='Informe o tema da reunião.')
    inicio = _data_hora(args.get('inicio'), 'inicio')
    fim = _data_hora(args['fim'], 'fim') if args.get('fim') else None
    if fim and fim <= inicio:
        raise Invalido('O fim previsto tem que ser depois do início.')
    tipo = _tipo_reuniao(args.get('tipo'))
    gerar_ata = _sim(args.get('gerar_ata'), padrao=True)
    pauta = str(args.get('pauta') or '').strip()
    catalogo = publico.tudo(user)
    escolhidos = _caminhos_de_convite(args, catalogo)
    pessoas = _pessoas(_ids(args.get('pessoas'), 'pessoas'), eu=user)
    ids = (publico.expandir(catalogo, escolhidos) | {u.pk for u in pessoas}) - {user.pk}
    convidados = list(User.objects.filter(pk__in=ids, is_active=True).order_by('first_name', 'last_name'))

    linhas = [f'Criar a {"entrevista" if tipo == "ENTREVISTA" else "reunião"} "{titulo}" em '
              f'{_periodo(inicio, fim)}, com você como organizador.']
    if pauta:
        linhas.append('Pauta: ' + _cortar(pauta, 300))
    linhas.append('Ata: gerada ao gravar na sala.' if gerar_ata else 'Ata: desligada.')
    caminhos = _rotulos_caminhos(escolhidos, catalogo)
    if caminhos:
        linhas.append('Convite por ' + ', '.join(caminhos) + '.')
    if convidados:
        linhas.append(f'Convidados ({len(convidados)}): {_nomes(convidados)}.')
        linhas.append('Cada convidado recebe aviso no portal, e a reunião entra na agenda de todos, '
                      'com a sala de vídeo.')
    else:
        linhas.append('Sem convidados por enquanto (dá para convidar depois com reunioes_editar).')
    if tipo == 'ENTREVISTA':
        linhas.append('Entrevista também abre a ficha no banco de talentos.')
    return '\n'.join(linhas), {
        'titulo': titulo[:200], 'inicio': inicio.isoformat(), 'fim': fim.isoformat() if fim else '',
        'pauta': pauta, 'tipo': tipo, 'gravar_ata': gerar_ata, 'usuarios': [u.pk for u in pessoas],
        **escolhidos}


def _exec_criar_reuniao(user, d):
    from reunioes import views as rv
    from reunioes.models import Reuniao

    r = chamar_view(rv.nova, user, '/reunioes/nova/', dados=_post_reuniao(d))
    rid = _id_da_reuniao(r['destino'])
    if not _sem_erro(r) or not rid:
        return 'A reunião não foi criada: ' + _falha(r)
    reuniao = Reuniao.objects.get(pk=rid)
    return (f'Reunião criada: #{rid} "{reuniao.titulo}" em {_periodo(reuniao.inicio, reuniao.fim)}, '
            f'{reuniao.participantes.count()} convidado(s).\n'
            f'Detalhe: {_url(f"/reunioes/{rid}/")}\nSala: {_url(f"/reunioes/{rid}/sala/")}')


def _reuniao_editavel(user, args):
    from reunioes.models import Reuniao

    r = (Reuniao.objects.select_related('organizador')
         .filter(pk=_inteiro(args.get('reuniao_id'), 'reuniao_id')).first())
    if r is None or not r.pode_ver(user):
        raise Invalido('Reunião não encontrada entre as suas.')
    if not r.pode_editar(user):
        raise Invalido('Só quem organizou a reunião (ou o SUPERADMIN) pode fazer isso.')
    return r


def _previa_editar_reuniao(user, args):
    from reunioes import publico

    r = _reuniao_editavel(user, args)
    if r.status == r.CANCELADA:
        raise Invalido('Essa reunião foi cancelada.')
    mudancas = []
    titulo = r.titulo
    if args.get('titulo'):
        titulo = _titulo(args)[:200]
        mudancas.append(f'tema: "{titulo}"')
    inicio, fim = r.inicio, r.fim
    if args.get('inicio') or args.get('fim'):
        inicio = _data_hora(args['inicio'], 'inicio') if args.get('inicio') else r.inicio
        if args.get('fim'):
            fim = _data_hora(args['fim'], 'fim')
        elif r.fim:
            fim = inicio + (r.fim - r.inicio)          # remarcar mantém a duração prevista
        if fim and fim <= inicio:
            raise Invalido('O fim previsto tem que ser depois do início.')
        mudancas.append(f'horário: {_periodo(inicio, fim)} (era {_periodo(r.inicio, r.fim)})')
    pauta = r.pauta
    if args.get('pauta') is not None:
        pauta = str(args['pauta']).strip()
        mudancas.append('pauta: ' + (_cortar(pauta, 200) or '(apagar)'))
    tipo = _tipo_reuniao(args.get('tipo'), padrao=r.tipo)
    if tipo != r.tipo:
        mudancas.append(f'tipo: {dict(r.TIPOS)[tipo]}')
    gerar_ata = _sim(args.get('gerar_ata'), padrao=r.gravar_ata)
    if gerar_ata != r.gravar_ata:
        mudancas.append('ata ligada' if gerar_ata else 'ata desligada')

    catalogo = publico.tudo(user)
    escolhidos = _caminhos_de_convite(args, catalogo, prefixo='adicionar_')
    atuais = list(r.participantes.values_list('user_id', flat=True))
    novos = [u for u in _pessoas(_ids(args.get('adicionar_pessoas'), 'adicionar_pessoas'), eu=user)
             if u.pk not in atuais]
    tirar = [i for i in _ids(args.get('remover_pessoas'), 'remover_pessoas') if i in atuais]
    pelos_caminhos = publico.expandir(catalogo, escolhidos) - set(atuais) - {u.pk for u in novos} - {user.pk}
    if novos or pelos_caminhos:
        chegam = novos + list(User.objects.filter(pk__in=pelos_caminhos, is_active=True))
        mudancas.append(f'convidar {_nomes(chegam)} (recebem aviso)')
    if tirar:
        texto = f'tirar {_nomes(User.objects.filter(pk__in=tirar))}'
        if r.participantes.filter(user_id__in=tirar, entrou_em__isnull=False).exists():
            texto += ' — quem já entrou na sala continua, para não sumir da lista de presença'
        mudancas.append(texto)
    if not mudancas:
        raise Invalido('Diga o que mudar na reunião (tema, horário, pauta, convidados...).')
    linhas = [f'Alterar a reunião #{r.pk} "{r.titulo}":'] + [f'• {m}' for m in mudancas]
    linhas.append('A agenda de todos acompanha a mudança.')
    return '\n'.join(linhas), {
        'reuniao_id': r.pk, 'titulo': titulo, 'inicio': inicio.isoformat(),
        'fim': fim.isoformat() if fim else '', 'pauta': pauta, 'tipo': tipo, 'gravar_ata': gerar_ata,
        'usuarios': [i for i in atuais if i not in tirar] + [u.pk for u in novos], **escolhidos}


def _exec_editar_reuniao(user, d):
    from reunioes import views as rv
    from reunioes.models import Reuniao

    rid = d['reuniao_id']
    r = chamar_view(rv.nova, user, f'/reunioes/{rid}/editar/', dados=_post_reuniao(d), reuniao_id=rid)
    if not _sem_erro(r) or _id_da_reuniao(r['destino']) != rid:
        return 'A reunião não foi alterada: ' + _falha(r)
    reuniao = Reuniao.objects.get(pk=rid)
    return (f'Reunião #{rid} atualizada: "{reuniao.titulo}" em {_periodo(reuniao.inicio, reuniao.fim)}, '
            f'{reuniao.participantes.count()} convidado(s). {_url(f"/reunioes/{rid}/")}')


def _previa_encerrar_reuniao(user, args):
    r = _reuniao_editavel(user, args)
    if r.status == r.CANCELADA:
        raise Invalido('Essa reunião foi cancelada — não há o que encerrar.')
    if r.status == r.ENCERRADA:
        raise Invalido('Essa reunião já está encerrada.')
    linhas = [f'Encerrar a reunião #{r.pk} "{r.titulo}" ({_periodo(r.inicio, r.fim)}) e marcar como finalizada.']
    if r.token_publico:
        linhas.append('O link de visitante deixa de valer.')
    linhas.append('Quem ainda estiver na chamada de vídeo não é desconectado por aqui.')
    return '\n'.join(linhas), {'reuniao_id': r.pk}


def _exec_encerrar_reuniao(user, d):
    from reunioes import views as rv

    rid = d['reuniao_id']
    r = chamar_view(rv.encerrar, user, f'/reunioes/{rid}/encerrar/', aceita_json=True, reuniao_id=rid)
    if not _ok_json(r):
        return 'A reunião não foi encerrada: ' + _falha(r)
    return f'{r["json"].get("mensagem") or "Reunião encerrada."} {_url(f"/reunioes/{rid}/")}'


def _previa_cancelar_reuniao(user, args):
    r = _reuniao_editavel(user, args)
    if r.status == r.CANCELADA:
        raise Invalido('Essa reunião já foi cancelada.')
    avisados = r.destinatarios().exclude(pk=user.pk).count()
    linhas = [f'Cancelar a reunião #{r.pk} "{r.titulo}" ({_periodo(r.inicio, r.fim)}).',
              f'{avisados} pessoa(s) recebem aviso do cancelamento, e o evento sai da agenda.']
    if r.status == r.ENCERRADA:
        linhas.append('Ela já estava encerrada.')
    linhas.append('Não dá para desfazer.')
    return '\n'.join(linhas), {'reuniao_id': r.pk}


def _exec_cancelar_reuniao(user, d):
    from reunioes import views as rv
    from reunioes.models import Reuniao

    rid = d['reuniao_id']
    r = chamar_view(rv.cancelar, user, f'/reunioes/{rid}/cancelar/', reuniao_id=rid)
    if not _sem_erro(r) or not Reuniao.objects.filter(pk=rid, status=Reuniao.CANCELADA).exists():
        return 'A reunião não foi cancelada: ' + _falha(r)
    return f'Reunião #{rid} cancelada; todos foram avisados.'


def _previa_link_publico(user, args):
    from reunioes.models import ConfiguracaoReunioes

    r = _reuniao_editavel(user, args)
    acao = _normal(args.get('acao'))
    if acao not in ('abrir', 'trocar', 'fechar'):
        raise Invalido('acao deve ser abrir, trocar ou fechar.')
    if not ConfiguracaoReunioes.get().permitir_link_publico:
        raise Invalido('O link de visitante está desligado na configuração do módulo (quem liga é o SUPERADMIN).')
    if acao == 'fechar':
        if not r.token_publico:
            raise Invalido('Essa reunião não tem link de visitante aberto.')
        texto = 'Quem tinha o endereço não entra mais.'
    else:
        if r.acabou:
            raise Invalido('A reunião já acabou; link de visitante só vale enquanto ela não é encerrada.')
        if acao == 'abrir' and r.token_publico:
            acao = 'trocar'
        texto = ('O endereço atual para de valer na hora e um novo é gerado.' if acao == 'trocar' else
                 'Quem receber o endereço entra na sala sem conta no portal, como visitante (sem '
                 'moderar), até a reunião ser encerrada.')
    verbo = {'abrir': 'Abrir', 'trocar': 'Trocar', 'fechar': 'Fechar'}[acao]
    return (f'{verbo} o link de visitante da reunião #{r.pk} "{r.titulo}". {texto}',
            {'reuniao_id': r.pk, 'acao': acao})


def _exec_link_publico(user, d):
    from reunioes import views as rv
    from reunioes.models import Reuniao

    rid = d['reuniao_id']
    r = chamar_view(rv.link_publico, user, f'/reunioes/{rid}/link-publico/',
                    dados={'acao': d['acao']}, reuniao_id=rid)
    if not _sem_erro(r):
        return 'O link não mudou: ' + _falha(r)
    reuniao = Reuniao.objects.get(pk=rid)
    if not reuniao.token_publico:
        return 'Link de visitante fechado.'
    return 'Link de visitante: ' + _url(reverse('reunioes:sala_publica', args=[reuniao.token_publico]))


def _previa_registrar_ata(user, args):
    from agenda.models import MeetingTranscription
    from reunioes.models import Reuniao
    from reunioes.permissoes import e_superadmin

    r = Reuniao.objects.filter(pk=_inteiro(args.get('reuniao_id'), 'reuniao_id')).first()
    if r is None or not r.pode_ver(user):
        raise Invalido('Reunião não encontrada entre as suas.')
    t = MeetingTranscription.objects.filter(pk=_inteiro(args.get('transcricao_id'), 'transcricao_id')).first()
    if t is None or (t.owner_id != user.pk and not e_superadmin(user)):
        raise Invalido('Só dá para registrar como ata uma transcrição sua — veja agenda_transcricoes.')
    pessoas = r.destinatarios().exclude(pk=t.owner_id).count()
    linhas = [f'Registrar a transcrição #{t.pk} "{t.title}" como ata da reunião #{r.pk} "{r.titulo}".',
              f'Ela fica compartilhada com {pessoas} pessoa(s) da reunião, que recebem aviso, e a '
              'reunião é marcada como encerrada.']
    if not r.evento_id:
        linhas.append('A reunião não tem evento na agenda, então a ata não fica pendurada em um evento.')
    return '\n'.join(linhas), {'reuniao_id': r.pk, 'transcricao_id': t.pk}


def _exec_registrar_ata(user, d):
    from reunioes import views as rv

    rid = d['reuniao_id']
    r = chamar_view(rv.registrar_ata, user, f'/reunioes/{rid}/ata/',
                    dados={'transcricao': str(d['transcricao_id'])}, reuniao_id=rid)
    if not _ok_json(r) or not r['json'].get('url'):
        return 'A ata não foi registrada: ' + _falha(r)
    return f'Ata registrada e enviada para {r["json"].get("destinatarios", 0)} pessoa(s): {_url(r["json"]["url"])}'


# ─── Registro ────────────────────────────────────────────────────────────────
# Leitura: {'fn', 'description', 'input_schema'}. Ação: também 'acao' e 'previa'
# — 'fn' é a execução, que só roda via confirmar_acao (ferramentas.executar).


_LIMITE = _int('Quantos listar.')

TOOLS = {
    'buscar_pessoas': {
        'fn': _buscar_pessoas,
        'description': 'Acha colegas ativos por nome, e-mail, cargo ou setor e devolve o id de cada um '
                       '(necessário para convidar, pedir reunião, compartilhar ata ou ver agenda).',
        'input_schema': _obj(['busca'], busca=_txt('Parte do nome, e-mail, cargo ou setor.'), limite=_LIMITE),
    },
    'agenda_eventos': {
        'fn': _agenda_eventos,
        'description': 'Eventos da agenda num período: os do usuário (dele e os aceitos) ou os de outra '
                       'pessoa — completos se ele tem hierarquia sobre ela (ou é SUPERADMIN), senão só '
                       'os horários ocupados.',
        'input_schema': _obj(inicio=_txt('Primeiro dia, AAAA-MM-DD (padrão: hoje).'),
                             fim=_txt('Último dia, AAAA-MM-DD (padrão: 7 dias a partir do início).'),
                             pessoa_id=_int('Ver a agenda de outra pessoa (id de buscar_pessoas).'),
                             limite=_LIMITE),
    },
    'agenda_evento': {
        'fn': _agenda_evento,
        'description': 'Detalhe de um evento: horário, local, link, descrição, convidados e respostas, '
                       'reunião, tarefa e atas ligadas.',
        'input_schema': _obj(['evento_id'], evento_id=_int('Id do evento.')),
    },
    'agenda_convites': {
        'fn': _agenda_convites,
        'description': 'Convites de evento que o usuário ainda não respondeu (com o id de cada convite).',
        'input_schema': _obj(limite=_LIMITE),
    },
    'agenda_disponibilidade': {
        'fn': _agenda_disponibilidade,
        'description': 'Horários livres de uma pessoa num dia (08:00–18:00), para achar horário de reunião.',
        'input_schema': _obj(pessoa_id=_int('Id da pessoa (padrão: o próprio usuário).'),
                             data=_txt('Dia, AAAA-MM-DD (padrão: hoje).')),
    },
    'agenda_solicitacoes': {
        'fn': _agenda_solicitacoes,
        'description': 'Solicitações de reunião recebidas e enviadas pelo usuário, com status e id.',
        'input_schema': _obj(tipo=_txt('recebidas | enviadas | todas (padrão).'),
                             status=_txt('pendente | aceita | recusada | cancelada.'), limite=_LIMITE),
    },
    'agenda_transcricoes': {
        'fn': _agenda_transcricoes,
        'description': 'Transcrições/atas de reunião que o usuário pode ver (as dele, as compartilhadas '
                       'com ele; todas, se SUPERADMIN).',
        'input_schema': _obj(busca=_txt('Parte do título.'),
                             status=_txt('concluida | processando | gravando | erro.'), limite=_LIMITE),
    },
    'agenda_transcricao': {
        'fn': _agenda_transcricao,
        'description': 'Uma transcrição/ata completa: resumo, seções, decisões, itens de ação, riscos, '
                       'eventos sugeridos, tarefas criadas (com id), compartilhamento e, se pedido, o '
                       'texto em trechos.',
        'input_schema': _obj(['transcricao_id'], transcricao_id=_int('Id da transcrição.'),
                             incluir_texto=_bool('Trazer também o texto transcrito (em trechos).'),
                             a_partir_de=_int('Caractere inicial do trecho do texto (padrão 0).')),
    },
    'reunioes_lista': {
        'fn': _reunioes_lista,
        'description': 'Reuniões do módulo Reuniões em que o usuário organiza ou foi convidado (próximas ou '
                       'anteriores/canceladas). SUPERADMIN pode incluir as da rede inteira.',
        'input_schema': _obj(periodo=_txt('proximas (padrão) | passadas.'),
                             busca=_txt('Parte do tema, da pauta ou do nome do organizador.'),
                             incluir_outras=_bool('Só SUPERADMIN: incluir reuniões de toda a rede.'),
                             limite=_LIMITE),
    },
    'reunioes_detalhe': {
        'fn': _reunioes_detalhe,
        'description': 'Detalhe de uma reunião: pauta, status, convidados (e por que foram chamados), '
                       'presença na sala, visitantes, link de visitante, sala de vídeo e atas.',
        'input_schema': _obj(['reuniao_id'], reuniao_id=_int('Id da reunião.')),
    },
    'reunioes_opcoes_convite': {
        'fn': _reunioes_opcoes_convite,
        'description': 'Caminhos para convidar gente em bloco numa reunião: cargos, setores/lojas, grupos '
                       'ou coordenações, com quantas pessoas cada um chama.',
        'input_schema': _obj(['tipo'], tipo=_txt('cargos | setores | grupos | coordenacoes.'),
                             busca=_txt('Parte do nome.'), limite=_LIMITE),
    },

    # ─ Agenda: ações
    'agenda_criar_evento': _acao(
        _previa_criar_evento, _exec_criar_evento,
        'Cria evento na agenda do usuário, com convidados opcionais. Tipo chamada já cria a sala de vídeo; '
        'tipo tarefa vira tarefa em /users/tasks/.',
        _obj(['titulo', 'inicio'], titulo=_txt('Título.'),
             inicio=_txt(f'Início ({DATA_HORA}); em dia inteiro, só AAAA-MM-DD.'),
             fim=_txt('Fim no mesmo formato (padrão: 1 hora depois; em dia inteiro, o último dia).'),
             tipo=_txt('evento (padrão) | reuniao | chamada | tarefa | lembrete | bloqueio.'),
             descricao=_txt('Descrição.'), local=_txt('Local.'), link=_txt('Link (videoconferência etc.).'),
             participantes=_lista_ids('Ids dos convidados.'), privado=_bool('Outros veem só "ocupado".'),
             dia_inteiro=_bool('Evento de dia inteiro.'), repetir_semanal=_bool('Repetir toda semana.'),
             repetir_ate=_txt('Última data da repetição, AAAA-MM-DD (padrão: 3 meses).'))),
    'agenda_editar_evento': _acao(
        _previa_editar_evento, _exec_editar_evento,
        'Altera ou remarca um evento criado pelo usuário (só o dono altera). Mudar só o início mantém a duração.',
        _obj(['evento_id'], evento_id=_int('Id do evento.'), titulo=_txt('Novo título.'),
             inicio=_txt(f'Novo início ({DATA_HORA}).'), fim=_txt(f'Novo fim ({DATA_HORA}).'),
             tipo=_txt('evento | reuniao | chamada | tarefa | lembrete | bloqueio.'),
             descricao=_txt('Nova descrição.'), local=_txt('Novo local.'), link=_txt('Novo link.'),
             privado=_bool('Privado ou não.'),
             adicionar_participantes=_lista_ids('Ids de quem convidar.'),
             remover_participantes=_lista_ids('Ids de quem tirar do evento.'))),
    'agenda_excluir_evento': _acao(
        _previa_excluir_evento, _exec_excluir_evento,
        'Exclui um evento criado pelo usuário (ou a série semanal inteira).',
        _obj(['evento_id'], evento_id=_int('Id do evento.'),
             toda_a_serie=_bool('Em evento semanal, excluir a série inteira.'))),
    'agenda_responder_convite': _acao(
        _previa_responder_convite, _exec_responder_convite,
        'Aceita ou recusa um convite de evento recebido pelo usuário.',
        _obj(['convite_id', 'resposta'], convite_id=_int('Id do convite (de agenda_convites).'),
             resposta=_txt('aceitar | recusar.'), observacao=_txt('Observação para quem convidou.'))),
    'agenda_solicitar_reuniao': _acao(
        _previa_solicitar_reuniao, _exec_solicitar_reuniao,
        'Pede a um colega uma reunião, chamada ou horário; ele aceita ou recusa na agenda.',
        _obj(['pessoa_id', 'titulo', 'inicio'], pessoa_id=_int('Id do colega.'), titulo=_txt('Título.'),
             inicio=_txt(f'Início proposto ({DATA_HORA}).'),
             fim=_txt(f'Fim proposto ({DATA_HORA}; padrão: 30 min depois).'),
             tipo=_txt('reuniao (padrão) | chamada | horario.'), descricao=_txt('Motivo.'),
             local=_txt('Local sugerido.'))),
    'agenda_responder_solicitacao': _acao(
        _previa_responder_solicitacao, _exec_responder_solicitacao,
        'Aceita ou recusa uma solicitação de reunião recebida, ou cancela uma enviada.',
        _obj(['solicitacao_id', 'acao'], solicitacao_id=_int('Id da solicitação.'),
             acao=_txt('aceitar | recusar | cancelar.'), observacao=_txt('Observação da resposta.'))),
    'agenda_compartilhar_transcricao': _acao(
        _previa_compartilhar_transcricao, _exec_compartilhar_transcricao,
        'Dá ou tira o acesso de leitura de colegas a uma transcrição/ata do usuário.',
        _obj(['transcricao_id'], transcricao_id=_int('Id da transcrição.'),
             adicionar=_lista_ids('Ids de quem passa a ver.'), remover=_lista_ids('Ids de quem deixa de ver.'))),
    'agenda_agendar_da_transcricao': _acao(
        _previa_agendar_da_transcricao, _exec_agendar_da_transcricao,
        'Marca na agenda do usuário um evento sugerido por uma transcrição dele.',
        _obj(['transcricao_id', 'titulo', 'inicio'], transcricao_id=_int('Id da transcrição.'),
             titulo=_txt('Título.'), inicio=_txt(f'Início ({DATA_HORA}).'), fim=_txt(f'Fim ({DATA_HORA}).'),
             duracao_minutos=_int('Duração, se não houver fim (padrão 60).'), descricao=_txt('Descrição.'))),
    'agenda_atribuir_tarefa': _acao(
        _previa_atribuir_tarefa, _exec_atribuir_tarefa,
        'Passa uma tarefa criada pela transcrição do usuário para outro colega.',
        _obj(['transcricao_id', 'tarefa_id', 'pessoa_id'], transcricao_id=_int('Id da transcrição.'),
             tarefa_id=_int('Id da tarefa (de agenda_transcricao).'), pessoa_id=_int('Id do novo responsável.'))),
    'agenda_tarefa_para_impulso': _acao(
        _previa_tarefa_impulso, _exec_tarefa_impulso,
        'Leva uma tarefa de uma ata ao Impulso como meta do próprio usuário, com o gestor escolhido.',
        _obj(['transcricao_id', 'tarefa_id', 'gestor_id', 'prazo'], transcricao_id=_int('Id da transcrição.'),
             tarefa_id=_int('Id da tarefa.'), gestor_id=_int('Id do gestor (a lista vem em agenda_transcricao).'),
             prazo=_txt('Prazo da meta, AAAA-MM-DD.'), titulo=_txt('Título da meta (padrão: o da tarefa).'),
             descricao=_txt('Descrição da meta (padrão: a da tarefa).'),
             precisa_aprovacao=_bool('Passar pela aprovação do gestor (padrão sim; gestor do Impulso não precisa).'))),
    'agenda_reprocessar_transcricao': _acao(
        _previa_reprocessar_transcricao, _exec_reprocessar_transcricao,
        'Reprocessa uma transcrição (a do usuário, ou qualquer uma se SUPERADMIN) em segundo plano.',
        _obj(['transcricao_id'], transcricao_id=_int('Id da transcrição.'))),
    'agenda_descartar_gravacao': _acao(
        _previa_descartar_gravacao, _exec_descartar_gravacao,
        'Descarta uma gravação em andamento ou que falhou antes de ter texto (apaga áudio e registro).',
        _obj(['transcricao_id'], transcricao_id=_int('Id da transcrição.'))),

    # ─ Reuniões: ações
    'reunioes_criar': _acao(
        _previa_criar_reuniao, _exec_criar_reuniao,
        'Cria reunião (ou entrevista) no módulo Reuniões com o usuário como organizador, convidando '
        'pessoas e/ou cargos, setores, grupos e coordenações. Entra na agenda de todos, com sala de vídeo.',
        _obj(['titulo', 'inicio'], titulo=_txt('Tema.'), inicio=_txt(f'Início ({DATA_HORA}).'),
             fim=_txt(f'Fim previsto ({DATA_HORA}).'), pauta=_txt('Pauta.'),
             tipo=_txt('reuniao (padrão) | entrevista.'), gerar_ata=_bool('Gerar ata ao gravar (padrão sim).'),
             pessoas=_lista_ids('Ids de pessoas convidadas.'),
             cargos=_lista_txt('Cargos (o id é o nome, de reunioes_opcoes_convite).'),
             setores=_lista_ids('Ids de setores/lojas.'), grupos=_lista_ids('Ids de grupos.'),
             coordenacoes=_lista_ids('Ids de coordenações.'))),
    'reunioes_editar': _acao(
        _previa_editar_reuniao, _exec_editar_reuniao,
        'Altera uma reunião que o usuário organiza (ou qualquer uma, se SUPERADMIN): tema, horário, pauta, '
        'tipo, ata e convidados. Quem já está convidado continua, a não ser que seja removido.',
        _obj(['reuniao_id'], reuniao_id=_int('Id da reunião.'), titulo=_txt('Novo tema.'),
             inicio=_txt(f'Novo início ({DATA_HORA}).'), fim=_txt(f'Novo fim previsto ({DATA_HORA}).'),
             pauta=_txt('Nova pauta.'), tipo=_txt('reuniao | entrevista.'), gerar_ata=_bool('Gerar ata.'),
             adicionar_pessoas=_lista_ids('Ids de quem convidar.'),
             remover_pessoas=_lista_ids('Ids de quem tirar.'),
             adicionar_cargos=_lista_txt('Cargos a convidar.'), adicionar_setores=_lista_ids('Setores a convidar.'),
             adicionar_grupos=_lista_ids('Grupos a convidar.'),
             adicionar_coordenacoes=_lista_ids('Coordenações a convidar.'))),
    'reunioes_encerrar': _acao(
        _previa_encerrar_reuniao, _exec_encerrar_reuniao,
        'Encerra uma reunião que o usuário organiza e a marca como finalizada.',
        _obj(['reuniao_id'], reuniao_id=_int('Id da reunião.'))),
    'reunioes_cancelar': _acao(
        _previa_cancelar_reuniao, _exec_cancelar_reuniao,
        'Cancela uma reunião que o usuário organiza: todos são avisados e o evento sai da agenda.',
        _obj(['reuniao_id'], reuniao_id=_int('Id da reunião.'))),
    'reunioes_link_publico': _acao(
        _previa_link_publico, _exec_link_publico,
        'Abre, troca ou fecha o link de visitante (entrada sem conta no portal) de uma reunião do usuário.',
        _obj(['reuniao_id', 'acao'], reuniao_id=_int('Id da reunião.'), acao=_txt('abrir | trocar | fechar.'))),
    'reunioes_registrar_ata': _acao(
        _previa_registrar_ata, _exec_registrar_ata,
        'Registra uma transcrição do usuário como ata de uma reunião: compartilha com todos os '
        'convidados, avisa e marca a reunião como encerrada.',
        _obj(['reuniao_id', 'transcricao_id'], reuniao_id=_int('Id da reunião.'),
             transcricao_id=_int('Id da transcrição.'))),
}
