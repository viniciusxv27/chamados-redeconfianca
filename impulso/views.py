"""Views do módulo IMPULSO.

Blocos: dashboard, CONFIAR (metas/kanban/feedback), CONECTAR (conteúdos/projeto foco),
INOVAR (ideias) e ACOMPANHAMENTO (faixas).
"""
from datetime import date

from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from core.models import NotificationMixin

from .ai import generate_feedback_summary
from . import ciclos as ciclos_service
from .models import (
    Ciclo, CicloMes, ConclusaoConteudo, ConteudoConectar, Ideia, ImpulsoFeedback,
    Meta, MetaAnexo, MetaComentario, PontuacaoMensal, ProjetoFoco, TarefaProjeto,
)
from .scoring import calcular_pontuacao, linhas_detalhadas
from .utils import (
    FAIXAS, calcular_faixa, faixa_info, get_colaboradores, get_gestores_do_setor,
    is_impulso_manager, impulso_manager_required, impulso_member_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _int_or_none(value, lo=None, hi=None):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if lo is not None and v < lo:
        return None
    if hi is not None and v > hi:
        return None
    return v


def _notify(users, title, message, url):
    try:
        NotificationMixin.create_notifications_for_users(
            users=list(users), title=title, message=message,
            notification_type='SYSTEM', related_url=url,
        )
    except Exception:
        pass


def _metas_do_usuario(user, so_aprovadas=True):
    """Metas que o usuário pode ver no Kanban.

    Por padrão traz só as aprovadas: solicitação pendente ou recusada não é
    tarefa, é pedido — vive na tela de solicitações.
    """
    if user.is_superuser:
        qs = Meta.objects.all()
    elif is_impulso_manager(user):
        qs = Meta.objects.filter(Q(gestor=user) | Q(colaborador=user))
    else:
        qs = Meta.objects.filter(colaborador=user)
    return qs.filter(aprovacao=Meta.Aprovacao.APROVADA) if so_aprovadas else qs


def _pode_ver_meta(user, meta):
    return (user.is_superuser or meta.gestor_id == user.id
            or meta.colaborador_id == user.id
            or meta.solicitada_por_id == user.id)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@impulso_member_required
def dashboard(request):
    user = request.user
    gestor = is_impulso_manager(user)
    hoje = timezone.localdate()

    metas = _metas_do_usuario(user)
    minhas_metas = Meta.objects.filter(colaborador=user)

    proximas = (minhas_metas.exclude(status=Meta.Status.CONCLUIDA)
                .order_by('prazo')[:5])
    atrasadas = [m for m in minhas_metas.exclude(status=Meta.Status.CONCLUIDA)
                 if m.is_overdue]

    dados_faixa = calcular_pontuacao(user)

    context = {
        'is_gestor': gestor,
        'total_metas': metas.count(),
        'metas_pendentes': metas.exclude(status=Meta.Status.CONCLUIDA).count(),
        'metas_concluidas': metas.filter(status=Meta.Status.CONCLUIDA).count(),
        'proximas_atividades': proximas,
        'atrasadas': atrasadas,
        'minha_faixa': dados_faixa,
        'minhas_linhas': linhas_detalhadas(dados_faixa),
        'ciclo_ativo': Ciclo.objects.filter(status=Ciclo.Status.ABERTO).first(),
        'faixa_info': faixa_info(dados_faixa['faixa']),
        'feedbacks_recebidos': ImpulsoFeedback.objects.filter(colaborador=user).count(),
        'minhas_ideias': Ideia.objects.filter(autor=user).count(),
        'minhas_tarefas_abertas': TarefaProjeto.objects.filter(
            responsavel=user).exclude(status=TarefaProjeto.Status.CONCLUIDA).count(),
        'active_tab': 'dashboard',
    }
    if gestor:
        context['metas_aguardando_avaliacao'] = Meta.objects.filter(
            gestor=user, status=Meta.Status.ENTREGUE).count()
        context['ideias_novas'] = Ideia.objects.filter(status=Ideia.Status.NOVA).count()
    return render(request, 'impulso/dashboard.html', context)


# ---------------------------------------------------------------------------
# CONFIAR — Metas / Kanban
# ---------------------------------------------------------------------------
@impulso_member_required
def metas_kanban(request):
    user = request.user
    gestor = is_impulso_manager(user)
    metas = _metas_do_usuario(user).select_related('colaborador', 'gestor')

    colaborador_id = _int_or_none(request.GET.get('colaborador'))
    if colaborador_id:
        metas = metas.filter(colaborador_id=colaborador_id)

    # O template não passa argumentos para métodos, então a permissão de
    # exclusão é resolvida aqui, uma vez por card.
    metas = list(metas)
    for m in metas:
        m.pode_apagar = m.pode_excluir(user)

    colunas = []
    for status in Meta.KANBAN_STATUSES:
        colunas.append({
            'status': status.value,
            'label': status.label,
            'metas': [m for m in metas if m.status == status.value],
        })

    # Contador do sino de solicitações: o gestor vê o que precisa decidir; o
    # colaborador vê o que ainda está esperando resposta.
    pendentes = Meta.objects.filter(aprovacao=Meta.Aprovacao.PENDENTE)
    if gestor:
        pendentes = pendentes if user.is_superuser else pendentes.filter(gestor=user)
    else:
        pendentes = pendentes.filter(solicitada_por=user)

    context = {
        'colunas': colunas,
        'is_gestor': gestor,
        'colaboradores': get_colaboradores() if gestor else None,
        'colaborador_id': colaborador_id,
        'solicitacoes_pendentes': pendentes.count(),
        'active_tab': 'confiar',
    }
    return render(request, 'impulso/metas_kanban.html', context)


@impulso_member_required
def meta_create(request):
    """Cria a meta (gestor) ou solicita uma ao gestor do próprio setor (colaborador)."""
    sou_gestor = is_impulso_manager(request.user)
    gestores_do_setor = get_gestores_do_setor(request.user)

    if request.method == 'POST':
        titulo = (request.POST.get('titulo') or '').strip()
        descricao = (request.POST.get('descricao') or '').strip()
        recorrencia = request.POST.get('recorrencia') or Meta.Recorrencia.UNICA
        prazo = parse_date(request.POST.get('prazo') or '')
        if recorrencia not in Meta.Recorrencia.values:
            recorrencia = Meta.Recorrencia.UNICA

        if sou_gestor:
            colaborador = get_colaboradores().filter(
                id=_int_or_none(request.POST.get('colaborador'))).first()
            gestor = request.user
        else:
            # O colaborador só pode pedir para um gestor do SEU setor, e a meta
            # é sempre para ele mesmo — não dá para criar tarefa para terceiros.
            colaborador = request.user
            gestor = gestores_do_setor.filter(
                id=_int_or_none(request.POST.get('gestor'))).first()
            if not gestor:
                messages.error(request, 'Escolha um gestor do seu setor.')
                return redirect('impulso:meta_create')

        if not (colaborador and titulo and descricao and prazo):
            campo = 'colaborador, título, descrição e prazo' if sou_gestor else 'título, descrição e prazo'
            messages.error(request, f'Preencha {campo}.')
            return redirect('impulso:meta_create')

        meta = Meta.objects.create(
            gestor=gestor, colaborador=colaborador, titulo=titulo,
            descricao=descricao, recorrencia=recorrencia, prazo=prazo,
            aprovacao=Meta.Aprovacao.APROVADA if sou_gestor else Meta.Aprovacao.PENDENTE,
            solicitada_por=None if sou_gestor else request.user,
            created_by=request.user,
        )

        if sou_gestor:
            _notify([colaborador], 'Nova meta atribuída',
                    f'"{meta.titulo}" foi atribuída a você.',
                    f'/impulso/metas/{meta.id}/')
            messages.success(request, 'Meta criada com sucesso.')
        else:
            _notify([gestor], 'Nova solicitação de meta',
                    f'{request.user.get_full_name() or request.user.email} pediu a meta '
                    f'"{meta.titulo}". Aprove ou recuse.',
                    f'/impulso/metas/{meta.id}/')
            messages.success(
                request, 'Solicitação enviada. Ela entra no seu Kanban assim que o gestor aprovar.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    context = {
        'sou_gestor': sou_gestor,
        'colaboradores': get_colaboradores() if sou_gestor else None,
        'gestores_do_setor': gestores_do_setor,
        'setor': getattr(request.user, 'sector', None),
        'recorrencias': Meta.Recorrencia.choices,
        'active_tab': 'confiar',
    }
    return render(request, 'impulso/meta_form.html', context)


@require_POST
@impulso_member_required
def meta_decidir(request, meta_id):
    """Gestor aprova ou recusa uma solicitação do colaborador."""
    meta = get_object_or_404(Meta, id=meta_id)
    if not meta.pode_decidir(request.user):
        messages.error(request, 'Apenas o gestor escolhido pode decidir esta solicitação.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    decisao = request.POST.get('decisao')
    if decisao == 'aprovar':
        meta.aprovacao = Meta.Aprovacao.APROVADA
        aviso = ('Solicitação aprovada', f'Sua meta "{meta.titulo}" foi aprovada e já está no Kanban.')
        retorno = 'Solicitação aprovada. A meta entrou no Kanban do colaborador.'
    elif decisao == 'recusar':
        meta.aprovacao = Meta.Aprovacao.RECUSADA
        meta.motivo_recusa = (request.POST.get('motivo_recusa') or '').strip()
        motivo = f' Motivo: {meta.motivo_recusa}' if meta.motivo_recusa else ''
        aviso = ('Solicitação recusada', f'Sua meta "{meta.titulo}" foi recusada.{motivo}')
        retorno = 'Solicitação recusada.'
    else:
        messages.error(request, 'Decisão inválida.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    meta.decidida_por = request.user
    meta.decidida_em = timezone.now()
    meta.save(update_fields=['aprovacao', 'motivo_recusa', 'decidida_por',
                             'decidida_em', 'updated_at'])
    _notify([meta.colaborador], aviso[0], aviso[1], f'/impulso/metas/{meta.id}/')
    messages.success(request, retorno)
    return redirect('impulso:meta_detail', meta_id=meta.id)


@require_POST
@impulso_member_required
def meta_excluir(request, meta_id):
    """Apaga a meta. Só o gestor que a criou ou aprovou.

    A permissão é conferida no servidor, não só escondendo o ícone: sem isso,
    um POST direto apagaria meta alheia.
    """
    meta = get_object_or_404(Meta, id=meta_id)
    if not meta.pode_excluir(request.user):
        messages.error(request, 'Você só pode excluir metas que criou ou aprovou.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    titulo = meta.titulo
    colaborador = meta.colaborador
    era_avaliada = meta.is_avaliada
    meta.delete()          # anexos e comentários caem junto (CASCADE)

    _notify([colaborador], 'Meta removida',
            f'A meta "{titulo}" foi removida por '
            f'{request.user.get_full_name() or request.user.email}.',
            '/impulso/metas/')
    if era_avaliada:
        messages.warning(
            request,
            f'Meta "{titulo}" excluída. Ela já estava avaliada, então a pontuação '
            f'do mês de {colaborador.get_full_name() or colaborador.email} foi recalculada.')
    else:
        messages.success(request, f'Meta "{titulo}" excluída.')
    return redirect('impulso:metas_kanban')


@impulso_member_required
def meta_solicitacoes(request):
    """Fila de solicitações aguardando decisão."""
    if is_impulso_manager(request.user):
        pendentes = Meta.objects.filter(aprovacao=Meta.Aprovacao.PENDENTE)
        if not request.user.is_superuser:
            pendentes = pendentes.filter(gestor=request.user)
        titulo = 'Solicitações para aprovar'
    else:
        pendentes = Meta.objects.filter(solicitada_por=request.user,
                                        aprovacao=Meta.Aprovacao.PENDENTE)
        titulo = 'Minhas solicitações aguardando aprovação'

    decididas = Meta.objects.filter(
        aprovacao__in=[Meta.Aprovacao.RECUSADA],
    ).filter(Q(gestor=request.user) | Q(solicitada_por=request.user))[:20]

    context = {
        'titulo': titulo,
        'pendentes': pendentes.select_related('colaborador', 'gestor', 'solicitada_por'),
        'recusadas': decididas.select_related('colaborador', 'gestor', 'decidida_por'),
        'is_gestor': is_impulso_manager(request.user),
        'active_tab': 'confiar',
    }
    return render(request, 'impulso/meta_solicitacoes.html', context)


@impulso_member_required
def meta_detail(request, meta_id):
    meta = get_object_or_404(
        Meta.objects.select_related('colaborador', 'gestor', 'avaliado_por'), id=meta_id)
    if not _pode_ver_meta(request.user, meta):
        messages.error(request, 'Você não tem acesso a esta meta.')
        return redirect('impulso:metas_kanban')

    context = {
        'meta': meta,
        'anexos': meta.anexos.select_related('enviado_por'),
        'comentarios': meta.comentarios.select_related('autor'),
        'is_gestor_da_meta': meta.gestor_id == request.user.id or request.user.is_superuser,
        'is_colaborador_da_meta': meta.colaborador_id == request.user.id,
        'pode_decidir': meta.pode_decidir(request.user),
        'proxima_ocorrencia': meta.ocorrencias.first(),
        'notas_range': range(0, 6),
        'active_tab': 'confiar',
    }
    return render(request, 'impulso/meta_detail.html', context)


@require_POST
@impulso_member_required
def meta_update_status(request, meta_id):
    meta = get_object_or_404(Meta, id=meta_id)
    if not _pode_ver_meta(request.user, meta):
        return JsonResponse({'ok': False, 'error': 'sem permissão'}, status=403)

    if not meta.vale_pontos:
        return JsonResponse(
            {'ok': False, 'error': 'Esta meta ainda não foi aprovada pelo gestor.'}, status=400)

    novo = request.POST.get('status') or ''
    permitidos = [Meta.Status.A_FAZER, Meta.Status.EM_ANDAMENTO, Meta.Status.ENTREGUE]
    if novo not in [s.value for s in permitidos]:
        return JsonResponse(
            {'ok': False, 'error': 'Status inválido. A conclusão é feita na avaliação do gestor.'},
            status=400)

    meta.status = novo
    if novo == Meta.Status.ENTREGUE and not meta.entregue_em:
        meta.entregue_em = timezone.now()
        _notify(_gestores_da_meta(meta), 'Meta entregue',
                f'"{meta.titulo}" foi marcada como entregue.',
                f'/impulso/metas/{meta.id}/')
    meta.save(update_fields=['status', 'entregue_em', 'updated_at'])
    return JsonResponse({'ok': True, 'status': meta.status})


def _gestores_da_meta(meta):
    return [meta.gestor] if meta.gestor_id else []


@require_POST
@impulso_member_required
def meta_entregar(request, meta_id):
    meta = get_object_or_404(Meta, id=meta_id)
    if not (meta.colaborador_id == request.user.id or request.user.is_superuser):
        messages.error(request, 'Apenas o colaborador da meta pode entregá-la.')
        return redirect('impulso:meta_detail', meta_id=meta.id)
    if not meta.vale_pontos:
        messages.error(request, 'Esta meta ainda não foi aprovada pelo gestor.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    link = (request.POST.get('entrega_link') or '').strip()
    if link:
        meta.entrega_link = link
    meta.status = Meta.Status.ENTREGUE
    meta.entregue_em = timezone.now()
    meta.save(update_fields=['entrega_link', 'status', 'entregue_em', 'updated_at'])
    _notify(_gestores_da_meta(meta), 'Meta entregue',
            f'"{meta.titulo}" foi entregue por {request.user.get_full_name() or request.user.email}.',
            f'/impulso/metas/{meta.id}/')
    messages.success(request, 'Meta marcada como entregue.')
    return redirect('impulso:meta_detail', meta_id=meta.id)


@require_POST
@impulso_manager_required
def meta_avaliar(request, meta_id):
    meta = get_object_or_404(Meta, id=meta_id)
    if not (meta.gestor_id == request.user.id or request.user.is_superuser):
        messages.error(request, 'Apenas o gestor da meta pode avaliá-la.')
        return redirect('impulso:meta_detail', meta_id=meta.id)
    if not meta.vale_pontos:
        messages.error(request, 'Aprove a solicitação antes de avaliar a meta.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    nota_q = _int_or_none(request.POST.get('nota_qualidade'), 0, 5)
    nota_p = _int_or_none(request.POST.get('nota_prazo'), 0, 5)
    if nota_q is None or nota_p is None:
        messages.error(request, 'Informe as duas notas (0 a 5).')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    meta.nota_qualidade = nota_q
    meta.nota_prazo = nota_p
    meta.avaliacao_comentario = (request.POST.get('avaliacao_comentario') or '').strip()
    meta.avaliado_em = timezone.now()
    meta.avaliado_por = request.user
    meta.status = Meta.Status.CONCLUIDA
    meta.save()
    _notify([meta.colaborador], 'Meta avaliada',
            f'Sua meta "{meta.titulo}" foi avaliada: qualidade {nota_q}/5, prazo {nota_p}/5.',
            f'/impulso/metas/{meta.id}/')

    # Recorrência: concluir uma meta que se repete já abre a próxima ocorrência.
    proxima = meta.criar_proxima_ocorrencia()
    if proxima:
        _notify([meta.colaborador], 'Tarefa recorrente reaberta',
                f'"{proxima.titulo}" voltou para o Kanban com prazo '
                f'{proxima.prazo.strftime("%d/%m/%Y")}.',
                f'/impulso/metas/{proxima.id}/')
        messages.success(
            request,
            f'Avaliação registrada. Como a meta é {meta.get_recorrencia_display().lower()}, '
            f'a próxima ocorrência já foi criada com prazo {proxima.prazo.strftime("%d/%m/%Y")}.')
    else:
        messages.success(request, 'Avaliação registrada e meta concluída.')
    return redirect('impulso:meta_detail', meta_id=meta.id)


@require_POST
@impulso_member_required
def meta_add_anexo(request, meta_id):
    meta = get_object_or_404(Meta, id=meta_id)
    if not _pode_ver_meta(request.user, meta):
        messages.error(request, 'Sem permissão.')
        return redirect('impulso:metas_kanban')

    titulo = (request.POST.get('titulo') or '').strip()
    url = (request.POST.get('url') or '').strip()
    arquivo = request.FILES.get('arquivo')

    if arquivo:
        MetaAnexo.objects.create(
            meta=meta, tipo=MetaAnexo.Tipo.ARQUIVO, titulo=titulo,
            arquivo=arquivo, enviado_por=request.user)
        messages.success(request, 'Arquivo anexado.')
    elif url:
        MetaAnexo.objects.create(
            meta=meta, tipo=MetaAnexo.Tipo.LINK, titulo=titulo,
            url=url, enviado_por=request.user)
        messages.success(request, 'Link anexado.')
    else:
        messages.error(request, 'Envie um arquivo ou informe um link.')
    return redirect('impulso:meta_detail', meta_id=meta.id)


@require_POST
@impulso_member_required
def meta_add_comentario(request, meta_id):
    meta = get_object_or_404(Meta, id=meta_id)
    if not _pode_ver_meta(request.user, meta):
        messages.error(request, 'Sem permissão.')
        return redirect('impulso:metas_kanban')

    mensagem = (request.POST.get('mensagem') or '').strip()
    if mensagem:
        MetaComentario.objects.create(meta=meta, autor=request.user, mensagem=mensagem)
        # Notifica a outra ponta (gestor <-> colaborador).
        destinatarios = []
        if request.user.id != meta.colaborador_id:
            destinatarios.append(meta.colaborador)
        if meta.gestor_id and request.user.id != meta.gestor_id:
            destinatarios.append(meta.gestor)
        if destinatarios:
            _notify(destinatarios, 'Novo comentário em meta',
                    f'{request.user.get_full_name() or request.user.email} comentou em "{meta.titulo}".',
                    f'/impulso/metas/{meta.id}/')
    return redirect('impulso:meta_detail', meta_id=meta.id)


@impulso_member_required
def minhas_atividades(request):
    """Próximas atividades e prazos do colaborador."""
    metas = (Meta.objects.filter(colaborador=request.user)
             .exclude(status=Meta.Status.CONCLUIDA)
             .select_related('gestor').order_by('prazo'))
    context = {
        'metas': metas,
        'hoje': timezone.localdate(),
        'active_tab': 'confiar',
    }
    return render(request, 'impulso/minhas_atividades.html', context)


# ---------------------------------------------------------------------------
# CONFIAR — Feedback mensal
# ---------------------------------------------------------------------------
@impulso_member_required
def feedback_list(request):
    user = request.user
    gestor = is_impulso_manager(user)
    if gestor:
        feedbacks = ImpulsoFeedback.objects.filter(gestor=user)
    else:
        feedbacks = ImpulsoFeedback.objects.filter(colaborador=user)
    context = {
        'feedbacks': feedbacks.select_related('colaborador', 'gestor'),
        'is_gestor': gestor,
        'active_tab': 'confiar',
    }
    return render(request, 'impulso/feedback_list.html', context)


@impulso_manager_required
def feedback_create(request):
    if request.method == 'POST':
        colaborador_id = _int_or_none(request.POST.get('colaborador'))
        colaborador = get_colaboradores().filter(id=colaborador_id).first()
        ref = request.POST.get('referencia_mes') or ''  # 'YYYY-MM'
        pontos_fortes = (request.POST.get('pontos_fortes') or '').strip()
        pontos_melhoria = (request.POST.get('pontos_melhoria') or '').strip()
        comentario = (request.POST.get('comentario') or '').strip()

        ref_date = None
        try:
            ano, mes = ref.split('-')
            ref_date = date(int(ano), int(mes), 1)
        except (ValueError, AttributeError):
            ref_date = None

        if not (colaborador and ref_date and pontos_fortes and pontos_melhoria):
            messages.error(request, 'Preencha colaborador, mês, pontos fortes e pontos a melhorar.')
            return redirect('impulso:feedback_create')

        fb = ImpulsoFeedback.objects.create(
            gestor=request.user, colaborador=colaborador, referencia_mes=ref_date,
            pontos_fortes=pontos_fortes, pontos_melhoria=pontos_melhoria,
            comentario=comentario)
        # Resumo IA (best-effort).
        try:
            generate_feedback_summary(fb)
        except Exception:
            pass
        _notify([colaborador], 'Novo feedback mensal',
                f'Você recebeu um feedback referente a {ref_date:%m/%Y}.',
                f'/impulso/feedbacks/{fb.id}/')
        messages.success(request, 'Feedback registrado.')
        return redirect('impulso:feedback_detail', fb_id=fb.id)

    context = {
        'colaboradores': get_colaboradores(),
        'mes_atual': timezone.localdate().strftime('%Y-%m'),
        'active_tab': 'confiar',
    }
    return render(request, 'impulso/feedback_form.html', context)


@impulso_member_required
def feedback_detail(request, fb_id):
    fb = get_object_or_404(
        ImpulsoFeedback.objects.select_related('colaborador', 'gestor'), id=fb_id)
    if not (request.user.is_superuser or fb.gestor_id == request.user.id
            or fb.colaborador_id == request.user.id):
        messages.error(request, 'Você não tem acesso a este feedback.')
        return redirect('impulso:feedback_list')

    # Gera o resumo IA sob demanda se ainda não existir.
    if not fb.ai_summary and not fb.ai_summary_error:
        try:
            generate_feedback_summary(fb)
        except Exception:
            pass

    context = {
        'fb': fb,
        'is_gestor_do_fb': fb.gestor_id == request.user.id or request.user.is_superuser,
        'active_tab': 'confiar',
    }
    return render(request, 'impulso/feedback_detail.html', context)


@require_POST
@impulso_manager_required
def feedback_regenerar_ia(request, fb_id):
    fb = get_object_or_404(ImpulsoFeedback, id=fb_id)
    if not (fb.gestor_id == request.user.id or request.user.is_superuser):
        messages.error(request, 'Sem permissão.')
        return redirect('impulso:feedback_detail', fb_id=fb.id)
    try:
        generate_feedback_summary(fb, force=True)
        messages.success(request, 'Resumo IA atualizado.')
    except Exception:
        messages.error(request, 'Não foi possível gerar o resumo IA agora.')
    return redirect('impulso:feedback_detail', fb_id=fb.id)


# ---------------------------------------------------------------------------
# CONECTAR — Conteúdos (cursos/vídeos/POPs)
# ---------------------------------------------------------------------------
@impulso_member_required
def conectar_list(request):
    user = request.user
    gestor = is_impulso_manager(user)
    conteudos = ConteudoConectar.objects.filter(ativo=True).prefetch_related('conclusoes')

    # status de conclusão do usuário atual
    minhas = {c.conteudo_id: c for c in
              ConclusaoConteudo.objects.filter(user=user)}

    grupos = {'CURSO': [], 'VIDEO': [], 'POP': []}
    for c in conteudos:
        c.minha_conclusao = minhas.get(c.id)
        grupos.get(c.tipo, grupos['POP']).append(c)

    context = {
        'cursos': grupos['CURSO'],
        'videos': grupos['VIDEO'],
        'pops': grupos['POP'],
        'is_gestor': gestor,
        'active_tab': 'conectar',
    }
    return render(request, 'impulso/conectar_list.html', context)


@impulso_member_required
def conteudo_create(request):
    """Gestor cria curso/vídeo/POP; equipe pode criar apenas POP."""
    gestor = is_impulso_manager(request.user)
    if request.method == 'POST':
        tipo = request.POST.get('tipo') or ConteudoConectar.Tipo.POP
        if tipo not in ConteudoConectar.Tipo.values:
            tipo = ConteudoConectar.Tipo.POP
        if not gestor and tipo != ConteudoConectar.Tipo.POP:
            messages.error(request, 'A equipe só pode subir POPs.')
            return redirect('impulso:conteudo_create')

        titulo = (request.POST.get('titulo') or '').strip()
        if not titulo:
            messages.error(request, 'Informe o título.')
            return redirect('impulso:conteudo_create')

        conteudo = ConteudoConectar.objects.create(
            tipo=tipo, titulo=titulo,
            descricao=(request.POST.get('descricao') or '').strip(),
            url=(request.POST.get('url') or '').strip(),
            arquivo=request.FILES.get('arquivo'),
            obrigatorio=bool(request.POST.get('obrigatorio')) if gestor else False,
            inicio=parse_date(request.POST.get('inicio') or '') or None,
            fim=parse_date(request.POST.get('fim') or '') or None,
            criado_por=request.user,
            criado_por_equipe=not gestor,
        )
        if gestor:
            ids = [i for i in (request.POST.getlist('obrigatorio_para') or [])]
            if ids:
                conteudo.obrigatorio_para.set(
                    get_colaboradores().filter(id__in=ids))
                _notify(conteudo.obrigatorio_para.all(),
                        f'Novo {conteudo.get_tipo_display().lower()} obrigatório',
                        f'"{conteudo.titulo}" foi atribuído a você.',
                        f'/impulso/conectar/{conteudo.id}/')
        messages.success(request, 'Conteúdo publicado.')
        return redirect('impulso:conteudo_detail', conteudo_id=conteudo.id)

    context = {
        'is_gestor': gestor,
        'tipos': ConteudoConectar.Tipo.choices,
        'colaboradores': get_colaboradores() if gestor else None,
        'active_tab': 'conectar',
    }
    return render(request, 'impulso/conteudo_form.html', context)


@impulso_member_required
def conteudo_detail(request, conteudo_id):
    conteudo = get_object_or_404(ConteudoConectar, id=conteudo_id)
    conclusao = ConclusaoConteudo.objects.filter(
        conteudo=conteudo, user=request.user).first()
    context = {
        'conteudo': conteudo,
        'conclusao': conclusao,
        'is_gestor': is_impulso_manager(request.user),
        'conclusoes': conteudo.conclusoes.select_related('user') if is_impulso_manager(request.user) else None,
        'active_tab': 'conectar',
    }
    return render(request, 'impulso/conteudo_detail.html', context)


@require_POST
@impulso_member_required
def conteudo_progresso_video(request, conteudo_id):
    """Recebe o avanço da reprodução e guarda até onde a pessoa assistiu.

    O navegador avisa a cada poucos segundos. O servidor só aceita avanço
    compatível com o tempo real decorrido: sem isso, bastaria mandar
    "assisti tudo" de uma vez e o vídeo obrigatório viraria enfeite.
    """
    conteudo = get_object_or_404(ConteudoConectar, id=conteudo_id)
    if not conteudo.video_reproduzivel:
        return JsonResponse({'ok': False, 'erro': 'Conteúdo não é um vídeo do portal.'}, status=400)

    try:
        posicao = float(request.POST.get('posicao') or 0)
        duracao = float(request.POST.get('duracao') or 0)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'erro': 'Progresso inválido.'}, status=400)

    conclusao, _ = ConclusaoConteudo.objects.get_or_create(
        conteudo=conteudo, user=request.user)

    agora = timezone.now()
    if duracao > 0:
        conclusao.video_duracao = duracao

    # Quanto tempo real passou desde o último aviso? O avanço não pode ser
    # maior que isso (com folga para latência e para o primeiro aviso).
    if conclusao.video_atualizado_em:
        decorrido = (agora - conclusao.video_atualizado_em).total_seconds()
    else:
        decorrido = 30
    teto = conclusao.video_assistido_ate + max(decorrido * 1.5, 5)

    if posicao > conclusao.video_assistido_ate:
        conclusao.video_assistido_ate = min(posicao, teto)

    alvo = (conclusao.video_duracao or 0) * ConclusaoConteudo.FRACAO_PARA_CONCLUIR
    if alvo and conclusao.video_assistido_ate >= alvo:
        conclusao.video_concluido = True

    conclusao.video_atualizado_em = agora
    conclusao.save(update_fields=['video_assistido_ate', 'video_duracao',
                                  'video_concluido', 'video_atualizado_em'])
    return JsonResponse({
        'ok': True,
        'assistido_ate': round(conclusao.video_assistido_ate, 1),
        'video_concluido': conclusao.video_concluido,
    })


@require_POST
@impulso_member_required
def conteudo_concluir(request, conteudo_id):
    conteudo = get_object_or_404(ConteudoConectar, id=conteudo_id)
    conclusao, _ = ConclusaoConteudo.objects.get_or_create(
        conteudo=conteudo, user=request.user)

    # Vídeo do portal só conclui depois de assistido — conferido aqui, não só
    # escondendo o botão na tela.
    if conteudo.video_reproduzivel and not conclusao.video_concluido:
        falta = max(0, (conclusao.video_duracao or 0) - conclusao.video_assistido_ate)
        messages.error(
            request,
            'Assista o vídeo até o fim para concluir.'
            + (f' Faltam cerca de {int(falta // 60)}min{int(falta % 60):02d}s.' if falta else ''))
        return redirect('impulso:conteudo_detail', conteudo_id=conteudo.id)

    conclusao.concluido = True
    conclusao.concluido_em = timezone.now()
    certificado = request.FILES.get('certificado')
    if certificado:
        conclusao.certificado = certificado
    conclusao.save()
    messages.success(request, 'Conteúdo marcado como concluído.')
    return redirect('impulso:conteudo_detail', conteudo_id=conteudo.id)


# ---------------------------------------------------------------------------
# CONECTAR — Projeto Foco
# ---------------------------------------------------------------------------
@impulso_member_required
def projeto_foco_list(request):
    user = request.user
    gestor = is_impulso_manager(user)
    if gestor:
        projetos = ProjetoFoco.objects.all()
    else:
        projetos = ProjetoFoco.objects.filter(membros=user, ativo=True)
    context = {
        'projetos': projetos.prefetch_related('membros', 'tarefas').distinct(),
        'is_gestor': gestor,
        'active_tab': 'conectar',
    }
    return render(request, 'impulso/projeto_list.html', context)


@impulso_manager_required
def projeto_foco_create(request):
    if request.method == 'POST':
        nome = (request.POST.get('nome') or '').strip()
        if not nome:
            messages.error(request, 'Informe o nome do projeto.')
            return redirect('impulso:projeto_foco_create')
        projeto = ProjetoFoco.objects.create(
            nome=nome, descricao=(request.POST.get('descricao') or '').strip(),
            criado_por=request.user)
        ids = request.POST.getlist('membros')
        if ids:
            membros = get_colaboradores().filter(id__in=ids)
            projeto.membros.set(membros)
            _notify(membros, 'Adicionado a projeto foco',
                    f'Você faz parte do projeto "{projeto.nome}".',
                    f'/impulso/conectar/projetos/{projeto.id}/')
        messages.success(request, 'Projeto foco criado.')
        return redirect('impulso:projeto_foco_detail', projeto_id=projeto.id)

    context = {
        'colaboradores': get_colaboradores(),
        'active_tab': 'conectar',
    }
    return render(request, 'impulso/projeto_form.html', context)


@impulso_manager_required
def projeto_foco_edit(request, projeto_id):
    """Altera nome, descrição e equipe do projeto foco.

    Quem sai da equipe perde as tarefas do projeto? Não: as tarefas ficam,
    porque são histórico de trabalho feito. O que muda é quem passa a ver o
    projeto e a receber tarefas novas.
    """
    projeto = get_object_or_404(ProjetoFoco, id=projeto_id)

    if request.method == 'POST':
        nome = (request.POST.get('nome') or '').strip()
        if not nome:
            messages.error(request, 'Informe o nome do projeto.')
            return redirect('impulso:projeto_foco_edit', projeto_id=projeto.id)

        projeto.nome = nome
        projeto.descricao = (request.POST.get('descricao') or '').strip()
        projeto.ativo = request.POST.get('ativo') == 'on'
        projeto.save(update_fields=['nome', 'descricao', 'ativo'])

        antes = set(projeto.membros.values_list('id', flat=True))
        ids = request.POST.getlist('membros')
        novos_membros = get_colaboradores().filter(id__in=ids)
        projeto.membros.set(novos_membros)
        depois = {u.id for u in novos_membros}

        # Só avisa quem entrou agora — quem já estava não precisa de aviso.
        entraram = [u for u in novos_membros if u.id not in antes]
        if entraram:
            _notify(entraram, 'Adicionado a projeto foco',
                    f'Você faz parte do projeto "{projeto.nome}".',
                    f'/impulso/conectar/projetos/{projeto.id}/')
        sairam = antes - depois
        if sairam:
            from django.contrib.auth import get_user_model
            _notify(list(get_user_model().objects.filter(id__in=sairam)),
                    'Removido de projeto foco',
                    f'Você não faz mais parte do projeto "{projeto.nome}".',
                    '/impulso/conectar/projetos/')

        messages.success(request, 'Projeto atualizado.')
        return redirect('impulso:projeto_foco_detail', projeto_id=projeto.id)

    return render(request, 'impulso/projeto_form.html', {
        'projeto': projeto,
        'colaboradores': get_colaboradores(),
        'membros_ids': set(projeto.membros.values_list('id', flat=True)),
        'active_tab': 'conectar',
    })


@impulso_member_required
def projeto_foco_detail(request, projeto_id):
    projeto = get_object_or_404(ProjetoFoco, id=projeto_id)
    gestor = is_impulso_manager(request.user)
    if not (gestor or projeto.membros.filter(id=request.user.id).exists()):
        messages.error(request, 'Você não faz parte deste projeto.')
        return redirect('impulso:projeto_foco_list')

    tarefas = projeto.tarefas.select_related('responsavel')
    if not gestor:
        # Membro vê apenas as tarefas destinadas a ele.
        tarefas = tarefas.filter(responsavel=request.user)

    context = {
        'projeto': projeto,
        'tarefas': tarefas,
        'is_gestor': gestor,
        'membros': projeto.membros.all(),
        'status_choices': TarefaProjeto.Status.choices,
        'active_tab': 'conectar',
    }
    return render(request, 'impulso/projeto_detail.html', context)


@require_POST
@impulso_manager_required
def tarefa_create(request, projeto_id):
    projeto = get_object_or_404(ProjetoFoco, id=projeto_id)
    titulo = (request.POST.get('titulo') or '').strip()
    if not titulo:
        messages.error(request, 'Informe o título da tarefa.')
        return redirect('impulso:projeto_foco_detail', projeto_id=projeto.id)
    responsavel_id = _int_or_none(request.POST.get('responsavel'))
    responsavel = projeto.membros.filter(id=responsavel_id).first()
    tarefa = TarefaProjeto.objects.create(
        projeto=projeto, titulo=titulo,
        descricao=(request.POST.get('descricao') or '').strip(),
        responsavel=responsavel,
        prazo=parse_date(request.POST.get('prazo') or '') or None,
        criado_por=request.user)
    if responsavel:
        _notify([responsavel], 'Nova tarefa em projeto foco',
                f'"{tarefa.titulo}" foi atribuída a você em {projeto.nome}.',
                f'/impulso/conectar/projetos/{projeto.id}/')
    messages.success(request, 'Tarefa adicionada.')
    return redirect('impulso:projeto_foco_detail', projeto_id=projeto.id)


@require_POST
@impulso_member_required
def tarefa_update_status(request, tarefa_id):
    tarefa = get_object_or_404(TarefaProjeto.objects.select_related('projeto'), id=tarefa_id)
    gestor = is_impulso_manager(request.user)
    if not (gestor or tarefa.responsavel_id == request.user.id):
        messages.error(request, 'Sem permissão para alterar esta tarefa.')
        return redirect('impulso:projeto_foco_detail', projeto_id=tarefa.projeto_id)
    novo = request.POST.get('status') or ''
    if novo in TarefaProjeto.Status.values:
        tarefa.status = novo
        tarefa.save(update_fields=['status'])
        messages.success(request, 'Status da tarefa atualizado.')
    destino = request.POST.get('next') or ''
    if destino == 'minhas':
        return redirect('impulso:minhas_tarefas')
    return redirect('impulso:projeto_foco_detail', projeto_id=tarefa.projeto_id)


@impulso_member_required
def minhas_tarefas(request):
    tarefas = (TarefaProjeto.objects.filter(responsavel=request.user)
               .select_related('projeto').order_by('status', 'prazo'))
    context = {'tarefas': tarefas, 'active_tab': 'conectar'}
    return render(request, 'impulso/minhas_tarefas.html', context)


# ---------------------------------------------------------------------------
# INOVAR — Ideias
# ---------------------------------------------------------------------------
@impulso_member_required
def inovar_list(request):
    """Ideias: cada um vê as suas; o gestor avalia sem saber de quem é.

    O colaborador nunca vê ideia de outro. O gestor precisa ver o conteúdo
    para aprovar — mas não a autoria: assim a avaliação é da ideia, não de
    quem escreveu. O nome só aparece na própria ideia de quem está olhando.
    """
    user = request.user
    gestor = is_impulso_manager(user)
    ideias = Ideia.objects.all() if gestor else Ideia.objects.filter(autor=user)

    lista = list(ideias.select_related('autor'))
    for ideia in lista:
        # Único caso em que o nome aparece: a ideia é de quem está vendo.
        ideia.mostrar_autor = (ideia.autor_id == user.id)

    context = {
        'ideias': lista,
        'is_gestor': gestor,
        'status_choices': Ideia.Status.choices,
        'active_tab': 'inovar',
    }
    return render(request, 'impulso/inovar_list.html', context)


@impulso_member_required
def ideia_create(request):
    if request.method == 'POST':
        descricao = (request.POST.get('descricao') or '').strip()
        setor_impacto = (request.POST.get('setor_impacto') or '').strip()
        motivo = (request.POST.get('motivo') or '').strip()
        if not (descricao and setor_impacto and motivo):
            messages.error(request, 'Preencha a ideia, o setor de impacto e o motivo.')
            return redirect('impulso:ideia_create')
        ideia = Ideia.objects.create(
            autor=request.user, descricao=descricao,
            setor_impacto=setor_impacto, motivo=motivo)
        messages.success(request, 'Ideia enviada. Obrigado por inovar!')
        return redirect('impulso:inovar_list')
    return render(request, 'impulso/ideia_form.html', {'active_tab': 'inovar'})


@impulso_member_required
def ideia_edit(request, ideia_id):
    """O autor edita a própria ideia enquanto ela não foi decidida."""
    ideia = get_object_or_404(Ideia, id=ideia_id)

    if ideia.autor_id != request.user.id and not request.user.is_superuser:
        messages.error(request, 'Você só pode editar as suas próprias ideias.')
        return redirect('impulso:inovar_list')
    if not ideia.editavel:
        messages.error(
            request,
            f'Esta ideia já foi {ideia.get_status_display().lower()} e não pode mais ser editada.')
        return redirect('impulso:inovar_list')

    if request.method == 'POST':
        descricao = (request.POST.get('descricao') or '').strip()
        setor_impacto = (request.POST.get('setor_impacto') or '').strip()
        motivo = (request.POST.get('motivo') or '').strip()
        if not (descricao and setor_impacto and motivo):
            messages.error(request, 'Preencha a ideia, o setor de impacto e o motivo.')
            return redirect('impulso:ideia_edit', ideia_id=ideia.id)

        ideia.descricao = descricao
        ideia.setor_impacto = setor_impacto
        ideia.motivo = motivo
        ideia.save(update_fields=['descricao', 'setor_impacto', 'motivo', 'atualizado_em'])
        messages.success(request, 'Ideia atualizada.')
        return redirect('impulso:inovar_list')

    return render(request, 'impulso/ideia_form.html',
                  {'ideia': ideia, 'active_tab': 'inovar'})


@require_POST
@impulso_manager_required
def ideia_update_status(request, ideia_id):
    ideia = get_object_or_404(Ideia, id=ideia_id)
    novo = request.POST.get('status') or ''
    if novo in Ideia.Status.values:
        ideia.status = novo
    ideia.resposta_gestor = (request.POST.get('resposta_gestor') or '').strip()
    ideia.save(update_fields=['status', 'resposta_gestor', 'atualizado_em'])
    _notify([ideia.autor], 'Atualização na sua ideia',
            f'Sua ideia sobre "{ideia.setor_impacto}" agora está: {ideia.get_status_display()}.',
            '/impulso/inovar/')
    messages.success(request, 'Ideia atualizada.')
    return redirect('impulso:inovar_list')


# ---------------------------------------------------------------------------
# ACOMPANHAMENTO — Faixas, ranking e detalhamento
# ---------------------------------------------------------------------------
@impulso_member_required
def acompanhamento(request):
    """Pontuação do mês corrente (ao vivo) + ranking + faixas."""
    colaboradores = get_colaboradores()
    ranking = []
    for c in colaboradores:
        dados = calcular_pontuacao(c)
        ranking.append({'user': c, 'dados': dados, 'faixa': faixa_info(dados['faixa'])})
    ranking.sort(key=lambda r: float(r['dados']['percentual']), reverse=True)

    minha = calcular_pontuacao(request.user)
    context = {
        'ranking': ranking,
        'minha': minha,
        'minha_faixa_info': faixa_info(minha['faixa']),
        'minhas_linhas': linhas_detalhadas(minha),
        'faixas': FAIXAS,
        'ciclo_ativo': Ciclo.objects.filter(status=Ciclo.Status.ABERTO).first(),
        'is_gestor': is_impulso_manager(request.user),
        'active_tab': 'acompanhamento',
    }
    return render(request, 'impulso/acompanhamento.html', context)


@impulso_member_required
def detalhe_colaborador(request, user_id):
    """Detalhamento da pontuação de um colaborador (por medalha/mês)."""
    alvo = get_object_or_404(get_colaboradores(), id=user_id)
    if not (is_impulso_manager(request.user) or alvo.id == request.user.id):
        messages.error(request, 'Você só pode ver o seu próprio detalhamento.')
        return redirect('impulso:acompanhamento')

    # Mês corrente ao vivo
    atual = calcular_pontuacao(alvo)
    # Histórico já fechado
    historico = (PontuacaoMensal.objects.filter(user=alvo)
                 .select_related('mes', 'mes__ciclo', 'setor')
                 .order_by('-mes__referencia'))

    mes_id = _int_or_none(request.GET.get('mes'))
    snapshot = historico.filter(mes_id=mes_id).first() if mes_id else None

    context = {
        'alvo': alvo,
        'atual': atual,
        'atual_faixa': faixa_info(atual['faixa']),
        'linhas': linhas_detalhadas(atual),
        'historico': historico,
        'snapshot': snapshot,
        'snapshot_faixa': faixa_info(snapshot.faixa) if snapshot else None,
        'medalhas': [{'p': p, 'faixa': faixa_info(p.faixa)} for p in historico],
        'is_gestor': is_impulso_manager(request.user),
        'active_tab': 'acompanhamento',
    }
    return render(request, 'impulso/detalhe_colaborador.html', context)


# ---------------------------------------------------------------------------
# ACOMPANHAMENTO — Ciclos
# ---------------------------------------------------------------------------
@impulso_member_required
def ciclo_list(request):
    context = {
        'ciclos': Ciclo.objects.prefetch_related('meses'),
        'is_gestor': is_impulso_manager(request.user),
        'active_tab': 'acompanhamento',
    }
    return render(request, 'impulso/ciclo_list.html', context)


@impulso_manager_required
def ciclo_create(request):
    if request.method == 'POST':
        nome = (request.POST.get('nome') or '').strip()
        inicio = parse_date(request.POST.get('inicio') or '')
        fim = parse_date(request.POST.get('fim') or '')
        if not (nome and inicio and fim):
            messages.error(request, 'Informe nome, início e fim do ciclo.')
            return redirect('impulso:ciclo_create')
        if fim < inicio:
            messages.error(request, 'O fim do ciclo não pode ser anterior ao início.')
            return redirect('impulso:ciclo_create')

        ciclo = Ciclo.objects.create(
            nome=nome, inicio=inicio, fim=fim, criado_por=request.user)
        qtd = ciclos_service.criar_meses(ciclo)
        messages.success(request, f'Ciclo "{ciclo.nome}" iniciado com {qtd} mês(es).')
        return redirect('impulso:ciclo_detail', ciclo_id=ciclo.id)

    hoje = timezone.localdate()
    context = {
        'hoje': hoje,
        'sugestao_nome': f'Ciclo {hoje:%m/%Y}',
        'active_tab': 'acompanhamento',
    }
    return render(request, 'impulso/ciclo_form.html', context)


@impulso_member_required
def ciclo_detail(request, ciclo_id):
    ciclo = get_object_or_404(Ciclo.objects.prefetch_related('meses'), id=ciclo_id)
    meses = list(ciclo.meses.all())
    resumo = ciclos_service.resumo_ciclo(ciclo)
    context = {
        'ciclo': ciclo,
        'meses': meses,
        'resumo': [{**linha, 'faixa_info': faixa_info(linha['faixa'])} for linha in resumo],
        'tem_mes_aberto': any(not m.is_fechado for m in meses),
        'is_gestor': is_impulso_manager(request.user),
        'active_tab': 'acompanhamento',
    }
    return render(request, 'impulso/ciclo_detail.html', context)


@impulso_member_required
def mes_detail(request, mes_id):
    mes = get_object_or_404(CicloMes.objects.select_related('ciclo'), id=mes_id)
    pontuacoes = (mes.pontuacoes.select_related('user', 'setor').order_by('-percentual'))
    context = {
        'mes': mes,
        'ciclo': mes.ciclo,
        'pontuacoes': [{'p': p, 'faixa': faixa_info(p.faixa)} for p in pontuacoes],
        'setores': ciclos_service.setores_do_mes(mes),
        'is_gestor': is_impulso_manager(request.user),
        'active_tab': 'acompanhamento',
    }
    return render(request, 'impulso/mes_detail.html', context)


@require_POST
@impulso_manager_required
def mes_fechar(request, mes_id):
    mes = get_object_or_404(CicloMes.objects.select_related('ciclo'), id=mes_id)
    if mes.is_fechado:
        messages.info(request, 'Este mês já está fechado.')
    else:
        qtd = ciclos_service.fechar_mes(mes, request.user)
        messages.success(
            request, f'Mês {mes.referencia:%m/%Y} fechado — {qtd} colaborador(es) pontuado(s).')
    return redirect('impulso:mes_detail', mes_id=mes.id)


@require_POST
@impulso_manager_required
def mes_reabrir(request, mes_id):
    mes = get_object_or_404(CicloMes, id=mes_id)
    if mes.ciclo.status == Ciclo.Status.ENCERRADO:
        messages.error(request, 'Não é possível reabrir um mês de ciclo encerrado.')
    else:
        ciclos_service.reabrir_mes(mes)
        messages.success(request, f'Mês {mes.referencia:%m/%Y} reaberto para recálculo.')
    return redirect('impulso:mes_detail', mes_id=mes.id)


@require_POST
@impulso_manager_required
def ciclo_encerrar(request, ciclo_id):
    ciclo = get_object_or_404(Ciclo, id=ciclo_id)
    if not ciclo.is_aberto:
        messages.info(request, 'Este ciclo já está encerrado.')
        return redirect('impulso:ciclo_detail', ciclo_id=ciclo.id)
    if ciclo.meses.filter(status=CicloMes.Status.ABERTO).exists():
        messages.error(request, 'Feche todos os meses antes de encerrar o ciclo.')
        return redirect('impulso:ciclo_detail', ciclo_id=ciclo.id)

    creditados = ciclos_service.encerrar_ciclo(ciclo, request.user)
    total = sum(c['valor'] for c in creditados) if creditados else 0
    messages.success(
        request,
        f'Ciclo encerrado. {len(creditados)} colaborador(es) receberam {total} C$ no total.')
    for item in creditados:
        _notify([item['user']], 'Prêmio do Impulso',
                f"Você recebeu {item['valor']} C$ pelo ciclo {ciclo.nome}. Parabéns!",
                '/impulso/acompanhamento/')
    return redirect('impulso:ciclo_detail', ciclo_id=ciclo.id)
