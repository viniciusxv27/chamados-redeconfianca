"""Telas do módulo de Reuniões."""
import json
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from . import publico
from .models import ConfiguracaoReunioes, ParticipanteReuniao, Reuniao

logger = logging.getLogger(__name__)
User = get_user_model()


def _avisar(destinatarios, titulo, texto, link):
    """Notificação no portal. O aviso nunca derruba a operação."""
    try:
        from core.models import Notification

        Notification.objects.bulk_create([
            Notification(user=u, title=titulo, message=texto,
                         notification_type='SYSTEM', related_url=link)
            for u in destinatarios
        ])
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Aviso de reunião não enviado: %s', exc)


def _espelhar_na_agenda(reuniao, convidados):
    """Cria/atualiza o evento correspondente na agenda do portal.

    A reunião vive no módulo dela, mas quem abre a agenda espera vê-la lá — e o
    pipeline de ata da agenda se pendura no evento.
    """
    try:
        from agenda.models import CalendarEvent, EventParticipant

        evento = reuniao.evento
        dados = {
            'owner': reuniao.organizador,
            'title': reuniao.titulo,
            'description': reuniao.pauta,
            'event_type': 'meeting',
            'color': '#0ea5e9',
            'start': reuniao.inicio,
            'end': reuniao.fim or reuniao.inicio,
            'location': 'Sala de vídeo do portal',
            'link': f'/reunioes/{reuniao.id}/sala/',
        }
        if evento is None:
            evento = CalendarEvent.objects.create(**dados)
            reuniao.evento = evento
            reuniao.save(update_fields=['evento'])
        else:
            for campo, valor in dados.items():
                setattr(evento, campo, valor)
            evento.save()

        atuais = set(evento.participants.values_list('id', flat=True)) \
            if hasattr(evento, 'participants') else set()
        for u in convidados:
            if u.id not in atuais:
                EventParticipant.objects.get_or_create(
                    event=evento, user=u, defaults={'status': 'pending'})
        return evento
    except Exception as exc:                                    # noqa: BLE001
        logger.warning('Reunião %s não espelhou na agenda: %s', reuniao.id, exc)
        return None


# ---------------------------------------------------------------------------
# Listagem
# ---------------------------------------------------------------------------
@login_required
def lista(request):
    agora = timezone.now()
    minhas = (Reuniao.objects
              .filter(Q(organizador=request.user) | Q(participantes__user=request.user))
              .select_related('organizador').distinct())

    proximas = minhas.filter(inicio__gte=agora - timezone.timedelta(hours=4)) \
                     .exclude(status=Reuniao.CANCELADA).order_by('inicio')
    passadas = minhas.filter(inicio__lt=agora - timezone.timedelta(hours=4)) \
                     .order_by('-inicio')[:30]

    return render(request, 'reunioes/lista.html', {
        'proximas': proximas,
        'passadas': passadas,
        'agora': agora,
    })


# ---------------------------------------------------------------------------
# Criar / editar
# ---------------------------------------------------------------------------
@login_required
def nova(request, reuniao_id=None):
    reuniao = None
    if reuniao_id:
        reuniao = get_object_or_404(Reuniao, id=reuniao_id)
        if not reuniao.pode_editar(request.user):
            messages.error(request, 'Só quem organizou pode editar a reunião.')
            return redirect('reunioes:detalhe', reuniao_id=reuniao.id)

    catalogo = publico.tudo(request.user)

    if request.method == 'POST':
        titulo = (request.POST.get('titulo') or '').strip()
        inicio = parse_datetime(request.POST.get('inicio') or '')
        if inicio and timezone.is_naive(inicio):
            inicio = timezone.make_aware(inicio)
        fim = parse_datetime(request.POST.get('fim') or '')
        if fim and timezone.is_naive(fim):
            fim = timezone.make_aware(fim)

        if not titulo or not inicio:
            messages.error(request, 'Tema e horário de início são obrigatórios.')
        elif fim and fim <= inicio:
            messages.error(request, 'O fim previsto tem que ser depois do início.')
        else:
            escolhidos = {
                'cargos': request.POST.getlist('cargos'),
                'setores': request.POST.getlist('setores'),
                'grupos': request.POST.getlist('grupos'),
                'coordenacoes': request.POST.getlist('coordenacoes'),
            }
            ids = publico.expandir(catalogo, escolhidos)
            try:
                ids |= {int(x) for x in request.POST.getlist('usuarios')}
            except (TypeError, ValueError):
                pass
            ids.discard(request.user.id)

            convidados = list(User.objects.filter(id__in=ids, is_active=True))
            origem = publico.origens(catalogo, escolhidos)

            if reuniao is None:
                reuniao = Reuniao.objects.create(
                    titulo=titulo[:200], pauta=(request.POST.get('pauta') or '').strip(),
                    inicio=inicio, fim=fim, organizador=request.user,
                    gravar_ata=request.POST.get('gravar_ata') == 'on')
                novo = True
            else:
                reuniao.titulo = titulo[:200]
                reuniao.pauta = (request.POST.get('pauta') or '').strip()
                reuniao.inicio, reuniao.fim = inicio, fim
                reuniao.gravar_ata = request.POST.get('gravar_ata') == 'on'
                reuniao.save()
                novo = False

            antes = set(reuniao.participantes.values_list('user_id', flat=True))
            agora_ids = {u.id for u in convidados}

            # Quem saiu da seleção sai da reunião; quem já entrou na sala fica,
            # porque apagar quem participou apagaria a lista de presença.
            remover = {uid for uid in (antes - agora_ids)
                       if not reuniao.participantes.filter(
                           user_id=uid, entrou_em__isnull=False).exists()}
            if remover:
                reuniao.participantes.filter(user_id__in=remover).delete()

            for u in convidados:
                if u.id in antes:
                    continue
                tipo, rotulo = origem.get(u.id, (ParticipanteReuniao.MANUAL, ''))
                ParticipanteReuniao.objects.get_or_create(
                    reuniao=reuniao, user=u,
                    defaults={'origem': tipo, 'rotulo_origem': rotulo[:120]})

            _espelhar_na_agenda(reuniao, convidados)

            quando = timezone.localtime(reuniao.inicio).strftime('%d/%m às %H:%M')
            novos = [u for u in convidados if u.id not in antes]
            if novos:
                _avisar(novos, 'Convite para reunião',
                        f'{request.user.get_full_name() or request.user.email} marcou '
                        f'"{reuniao.titulo}" para {quando}.',
                        f'/reunioes/{reuniao.id}/')

            messages.success(request, 'Reunião criada.' if novo else 'Reunião atualizada.')
            return redirect('reunioes:detalhe', reuniao_id=reuniao.id)

    marcados = {}
    if reuniao:
        marcados = {p.user_id for p in reuniao.participantes.all()}

    return render(request, 'reunioes/form.html', {
        'reuniao': reuniao,
        'catalogo_json': json.dumps(catalogo),
        'catalogo': catalogo,
        'pessoas': (User.objects.filter(is_active=True)
                    .exclude(id=request.user.id)
                    .order_by('first_name', 'last_name')),
        'marcados': marcados,
    })


# ---------------------------------------------------------------------------
# Detalhe / sala
# ---------------------------------------------------------------------------
@login_required
def detalhe(request, reuniao_id):
    reuniao = get_object_or_404(
        Reuniao.objects.select_related('organizador', 'evento'), id=reuniao_id)
    if not reuniao.pode_ver(request.user):
        messages.error(request, 'Você não está nesta reunião.')
        return redirect('reunioes:lista')

    atas = []
    if reuniao.evento_id:
        try:
            from agenda.models import MeetingTranscription
            atas = list(MeetingTranscription.objects
                        .filter(event_id=reuniao.evento_id).order_by('-id'))
        except Exception as exc:                                # noqa: BLE001
            logger.warning('Atas da reunião %s não carregaram: %s', reuniao.id, exc)

    return render(request, 'reunioes/detalhe.html', {
        'reuniao': reuniao,
        'participantes': reuniao.participantes.select_related('user'),
        'pode_editar': reuniao.pode_editar(request.user),
        'atas': atas,
        'agora': timezone.now(),
    })


@login_required
def sala(request, reuniao_id):
    reuniao = get_object_or_404(Reuniao, id=reuniao_id)
    if not reuniao.pode_ver(request.user):
        messages.error(request, 'Você não está nesta reunião.')
        return redirect('reunioes:lista')
    if reuniao.status == Reuniao.CANCELADA:
        messages.error(request, 'Esta reunião foi cancelada.')
        return redirect('reunioes:detalhe', reuniao_id=reuniao.id)

    # Marca presença e abre a reunião no primeiro que entra.
    reuniao.participantes.filter(user=request.user, entrou_em__isnull=True) \
                         .update(entrou_em=timezone.now())
    if reuniao.status == Reuniao.AGENDADA:
        Reuniao.objects.filter(id=reuniao.id, status=Reuniao.AGENDADA) \
                       .update(status=Reuniao.EM_ANDAMENTO)
        reuniao.status = Reuniao.EM_ANDAMENTO

    cfg = ConfiguracaoReunioes.get()
    from .jaas import dados_da_sala

    sala_video = dados_da_sala(cfg, request.user, reuniao.sala)
    base = request.build_absolute_uri('/').rstrip('/')
    return render(request, 'reunioes/sala.html', {
        'url_branding': base + reverse('reunioes:branding'),
        'url_logo': base + static('images/logo-t.png'),
        'url_portal': base,
        'reuniao': reuniao,
        'servidor': sala_video['servidor'],
        'sala_video': sala_video['sala'],
        'token_video': sala_video['token'],
        'sala_autenticada': sala_video['autenticado'],
        'gerar_ata': cfg.gerar_ata and reuniao.gravar_ata,
        'e_organizador': reuniao.organizador_id == request.user.id,
        'nome_exibicao': request.user.get_full_name() or request.user.email,
        'email_exibicao': request.user.email,
    })


@require_POST
@login_required
def encerrar(request, reuniao_id):
    reuniao = get_object_or_404(Reuniao, id=reuniao_id)
    if not reuniao.pode_editar(request.user):
        return JsonResponse({'ok': False, 'erro': 'Só o organizador encerra.'}, status=403)

    reuniao.status = Reuniao.ENCERRADA
    reuniao.save(update_fields=['status', 'atualizado_em'])
    return JsonResponse({'ok': True})


@require_POST
@login_required
def cancelar(request, reuniao_id):
    reuniao = get_object_or_404(Reuniao, id=reuniao_id)
    if not reuniao.pode_editar(request.user):
        messages.error(request, 'Só quem organizou pode cancelar.')
        return redirect('reunioes:detalhe', reuniao_id=reuniao.id)

    reuniao.status = Reuniao.CANCELADA
    reuniao.save(update_fields=['status', 'atualizado_em'])

    quando = timezone.localtime(reuniao.inicio).strftime('%d/%m às %H:%M')
    _avisar([u for u in reuniao.destinatarios() if u.id != request.user.id],
            'Reunião cancelada',
            f'"{reuniao.titulo}" ({quando}) foi cancelada.',
            f'/reunioes/{reuniao.id}/')

    if reuniao.evento_id:
        try:
            reuniao.evento.delete()
        except Exception as exc:                                # noqa: BLE001
            logger.warning('Evento da reunião %s não foi removido: %s', reuniao.id, exc)

    messages.success(request, 'Reunião cancelada e todos avisados.')
    return redirect('reunioes:lista')


# ---------------------------------------------------------------------------
# Ata
# ---------------------------------------------------------------------------
@require_POST
@login_required
def registrar_ata(request, reuniao_id):
    """Liga a transcrição recém-criada à reunião e manda para todo mundo.

    A gravação sobe pelo mesmo caminho da agenda (upload em pedaços + IA); aqui
    só amarramos o resultado na reunião e avisamos quem estava na pauta.
    """
    reuniao = get_object_or_404(Reuniao, id=reuniao_id)
    if not reuniao.pode_ver(request.user):
        return JsonResponse({'ok': False, 'erro': 'Você não está nesta reunião.'}, status=403)

    try:
        transcricao_id = int(request.POST.get('transcricao') or 0)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'erro': 'Transcrição inválida.'}, status=400)

    try:
        from agenda.models import MeetingTranscription

        ata = MeetingTranscription.objects.filter(id=transcricao_id).first()
        if ata is None:
            return JsonResponse({'ok': False, 'erro': 'Ata não encontrada.'}, status=404)
        if ata.owner_id != request.user.id and not request.user.is_superuser:
            return JsonResponse({'ok': False, 'erro': 'Ata de outra pessoa.'}, status=403)

        if reuniao.evento_id:
            ata.event_id = reuniao.evento_id
            ata.save(update_fields=['event'])

        destinatarios = list(reuniao.destinatarios())
        ata.shared_with.add(*[u for u in destinatarios if u.id != ata.owner_id])

        _avisar([u for u in destinatarios if u.id != request.user.id],
                'Ata da reunião disponível',
                f'A ata de "{reuniao.titulo}" já está no portal.',
                f'/agenda/transcricoes/{ata.id}/')

        reuniao.status = Reuniao.ENCERRADA
        reuniao.save(update_fields=['status', 'atualizado_em'])

        return JsonResponse({'ok': True, 'destinatarios': len(destinatarios),
                             'url': f'/agenda/transcricoes/{ata.id}/'})
    except Exception as exc:                                    # noqa: BLE001
        logger.error('Falha ao registrar a ata da reunião %s: %s', reuniao.id, exc)
        return JsonResponse({'ok': False, 'erro': 'Não foi possível registrar a ata.'},
                            status=500)


def branding(request):
    """Identidade visual da sala, no formato que o Jitsi lê.

    O Jitsi roda dentro de um iframe de outro domínio: não dá para alcançar o
    chat nem o fundo dele por CSS daqui. O caminho suportado é este arquivo —
    o próprio Jitsi busca a URL e aplica logo e fundo por dentro.

    Fica aberto de propósito: quem busca é o JavaScript do Jitsi, do domínio
    dele, sem o cookie da sessão do portal. São duas URLs de imagem pública,
    nada de dado de ninguém.
    """
    base = request.build_absolute_uri('/').rstrip('/')
    dados = {
        'logoImageUrl': f'{base}{static("images/logo-t.png")}',
        'logoClickUrl': base,
        'backgroundImageUrl': f'{base}{static("images/logo.png")}',
        'backgroundColor': '#111827',
        'didPageUrl': base,
    }
    resposta = JsonResponse(dados)
    resposta['Access-Control-Allow-Origin'] = '*'
    resposta['Cache-Control'] = 'public, max-age=300'
    return resposta


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
@login_required
def configuracao(request):
    if not (request.user.is_superuser
            or getattr(request.user, 'hierarchy', '') == 'SUPERADMIN'):
        messages.error(request, 'Só o SUPERADMIN configura as reuniões.')
        return redirect('reunioes:lista')

    cfg = ConfiguracaoReunioes.get()
    if request.method == 'POST':
        servidor = (request.POST.get('servidor_jitsi') or '').strip()
        # Só o host: uma URL inteira aqui quebraria o script da sala.
        servidor = servidor.replace('https://', '').replace('http://', '').strip('/')
        cfg.servidor_jitsi = servidor or 'meet.jit.si'
        cfg.gerar_ata = request.POST.get('gerar_ata') == 'on'
        cfg.jaas_app_id = (request.POST.get('jaas_app_id') or '').strip()[:120]
        cfg.jaas_api_key_id = (request.POST.get('jaas_api_key_id') or '').strip()[:120]
        chave = (request.POST.get('jaas_chave_privada') or '').strip()
        # Campo em branco não apaga a chave já guardada: o formulário mostra a
        # chave mascarada, e um "salvar" sem tocar nela não pode desconfigurar
        # o módulo inteiro.
        if chave and not chave.startswith('•'):
            cfg.jaas_chave_privada = chave
        cfg.save()
        messages.success(request, 'Configuração salva.')
        return redirect('reunioes:configuracao')

    from .jaas import gerar_token

    testando = gerar_token(cfg, request.user, 'teste') if cfg.jaas_app_id else None
    return render(request, 'reunioes/configuracao.html', {
        'cfg': cfg,
        'tem_jaas': bool(cfg.jaas_app_id and cfg.jaas_api_key_id and cfg.jaas_chave_privada),
        'jaas_ok': bool(testando),
        'servidor_publico': (cfg.servidor_jitsi or '').strip() in ('meet.jit.si', ''),
    })
