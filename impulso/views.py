"""Views do módulo IMPULSO.

Blocos: dashboard, CONFIAR (metas/kanban/feedback), CONECTAR (conteúdos/projeto foco),
INOVAR (ideias) e ACOMPANHAMENTO (faixas).
"""
from datetime import date, timedelta
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from core.models import NotificationMixin

from . import ai
from .ai import generate_feedback_summary
from . import ciclos as ciclos_service
from .models import (
    FAIXAS_DA_NOTA,
    Ciclo, CicloMes, ConclusaoConteudo, ConteudoConectar, ExcecaoAssiduidade, Ideia,
    ImpulsoFeedback,
    Meta, MetaAnexo, MetaComentario, MetaItem, MetaVisualizacao, PontuacaoMensal,
    ProjetoAnexo, ProjetoFoco, TarefaProjeto,
)
from .scoring import calcular_pontuacao, linhas_detalhadas
from .utils import (
    FAIXAS, calcular_faixa, faixa_info, get_colaboradores, get_gestores,
    get_colaboradores_do_gestor, get_gestores_do_setor,
    is_impulso_manager, impulso_manager_required, impulso_member_required,
)

User = get_user_model()


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
        # O gestor responde por TODOS os setores atrelados a ele — principal e
        # secundários. Antes ele só via meta em que era o gestor do registro:
        # gente do setor secundário aparecia no seletor e sumia ao ser
        # escolhida, porque a meta dela tinha outro gestor.
        equipe = list(get_colaboradores_do_gestor(user).values_list('id', flat=True))
        qs = Meta.objects.filter(Q(gestor=user) | Q(colaborador=user)
                                 | Q(participantes=user)
                                 | Q(colaborador_id__in=equipe)).distinct()
    else:
        # Meta compartilhada aparece no Kanban de quem participa dela também.
        qs = Meta.objects.filter(Q(colaborador=user) | Q(participantes=user)).distinct()
    return qs.filter(aprovacao=Meta.Aprovacao.APROVADA) if so_aprovadas else qs


def _pode_ver_meta(user, meta):
    return (user.is_superuser or meta.gestor_id == user.id
            or meta.colaborador_id == user.id
            or meta.solicitada_por_id == user.id
            # Gestor abre a meta de quem está em qualquer setor dele — senão a
            # meta aparece no Kanban e dá "sem permissão" ao clicar.
            or (is_impulso_manager(user)
                and get_colaboradores_do_gestor(user)
                .filter(id=meta.colaborador_id).exists())
            # Participante também é responsável pela meta: precisa abrir,
            # comentar e marcar os itens do to-do.
            or meta.participantes.filter(id=user.id).exists())


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
def _por_prazo(metas, hoje=None):
    """Reparte as metas em faixas de vencimento, para a pessoa se organizar.

    Não é agenda: é o corte que responde "o que vence hoje, o que vence amanhã
    e o que já passou do prazo". As faixas vazias não aparecem na tela — uma
    coluna cheia de títulos sem nada embaixo atrapalha mais do que ajuda.
    """
    hoje = hoje or timezone.localdate()
    amanha = hoje + timedelta(days=1)
    fim_da_semana = hoje + timedelta(days=7)

    faixas = [
        {'chave': 'atrasadas', 'titulo': 'Atrasadas', 'icone': 'fa-triangle-exclamation',
         'cor': 'text-red-700', 'fundo': 'bg-red-50', 'borda': 'border-red-200',
         'teste': lambda p: p < hoje},
        {'chave': 'hoje', 'titulo': 'Vence hoje', 'icone': 'fa-bolt',
         'cor': 'text-orange-700', 'fundo': 'bg-orange-50', 'borda': 'border-orange-200',
         'teste': lambda p: p == hoje},
        {'chave': 'amanha', 'titulo': 'Vence amanhã', 'icone': 'fa-sun',
         'cor': 'text-amber-700', 'fundo': 'bg-amber-50', 'borda': 'border-amber-200',
         'teste': lambda p: p == amanha},
        {'chave': 'semana', 'titulo': 'Próximos 7 dias', 'icone': 'fa-calendar-week',
         'cor': 'text-blue-700', 'fundo': 'bg-blue-50', 'borda': 'border-blue-200',
         'teste': lambda p: amanha < p <= fim_da_semana},
        {'chave': 'depois', 'titulo': 'Mais para frente', 'icone': 'fa-calendar',
         'cor': 'text-gray-500', 'fundo': 'bg-gray-50', 'borda': 'border-gray-200',
         'teste': lambda p: p > fim_da_semana},
    ]

    grupos = []
    for faixa in faixas:
        do_grupo = sorted((m for m in metas if m.prazo and faixa['teste'](m.prazo)),
                          key=lambda m: (m.prazo, m.titulo))
        if not do_grupo:
            continue
        for m in do_grupo:
            m.dias_para_o_prazo = (m.prazo - hoje).days
        grupos.append(dict(faixa, metas=do_grupo, n=len(do_grupo)))

    sem_prazo = [m for m in metas if not m.prazo]
    if sem_prazo:
        grupos.append({'chave': 'sem_prazo', 'titulo': 'Sem prazo', 'icone': 'fa-circle-question',
                       'cor': 'text-gray-400', 'fundo': 'bg-gray-50', 'borda': 'border-gray-200',
                       'metas': sem_prazo, 'n': len(sem_prazo)})
    return grupos


# Filtros que o Kanban entende. Ficam listados aqui porque o "Voltar" da tela
# da meta reconstrói a URL a partir da sessão — só o que está nesta tupla é
# guardado, então filtro novo precisa ser acrescentado junto.
FILTROS_KANBAN = ('colaborador',)
CHAVE_FILTROS_KANBAN = 'impulso_kanban_filtros'


def _guardar_filtros_kanban(request):
    """Lembra como o Kanban estava, para o Voltar da meta devolver igual."""
    escolhidos = {c: request.GET[c] for c in FILTROS_KANBAN
                  if (request.GET.get(c) or '').strip()}
    request.session[CHAVE_FILTROS_KANBAN] = escolhidos
    return escolhidos


def _url_kanban(request):
    """O Kanban com os filtros que a pessoa tinha escolhido.

    Guardar na sessão em vez de carregar a query na URL de cada card resolve
    também quem chegou na meta por notificação ou link direto: o Voltar leva de
    volta para a tela de onde a pessoa saiu, não para a lista sem filtro.
    """
    base = reverse('impulso:metas_kanban')
    guardados = request.session.get(CHAVE_FILTROS_KANBAN) or {}
    limpos = {c: str(v) for c, v in guardados.items()
              if c in FILTROS_KANBAN and str(v).strip()}
    return f'{base}?{urlencode(limpos)}' if limpos else base


@impulso_member_required
def metas_kanban(request):
    user = request.user
    gestor = is_impulso_manager(user)
    metas = _metas_do_usuario(user).select_related('colaborador', 'gestor')

    _guardar_filtros_kanban(request)

    colaborador_id = _int_or_none(request.GET.get('colaborador'))
    if colaborador_id:
        metas = metas.filter(colaborador_id=colaborador_id)

    # O template não passa argumentos para métodos, então a permissão de
    # exclusão é resolvida aqui, uma vez por card.
    metas = list(metas)
    vistas = {v.meta_id: v.visto_em for v in
              MetaVisualizacao.objects.filter(user=user, meta__in=metas)}
    # A equipe do gestor sai numa consulta só, não uma por card.
    equipe_ids = (set(get_colaboradores_do_gestor(user).values_list('id', flat=True))
                  if gestor else set())
    for m in metas:
        m.pode_apagar = m.pode_excluir(user, equipe_ids=equipe_ids)
        # Mesma régua da tela da meta: quem edita, duplica.
        m.pode_duplicar = m.pode_editar(user, equipe_ids=equipe_ids)
        m.novidades = m.novidades_para(user)
        m.tem_novidade = any(m.novidades.values())
        m.feitos, m.total_itens = m.progresso_itens

    colunas = []
    for status in Meta.KANBAN_STATUSES:
        do_status = [m for m in metas if m.status == status.value]
        colunas.append({
            'status': status.value,
            'label': status.label,
            'metas': do_status,
            # A coluna "A Fazer" ganha o corte por prazo: a pergunta de quem
            # abre o Kanban de manhã é "o que vence hoje?", e uma pilha única
            # não responde isso.
            'grupos': _por_prazo(do_status) if status == Meta.Status.A_FAZER else None,
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

        fora_da_area = False
        if sou_gestor:
            colaborador = get_colaboradores().filter(
                id=_int_or_none(request.POST.get('colaborador'))).first()

            # Quem responde pela meta pode ser outro gestor: é ele quem avalia
            # no fim e quem aparece no card. Antes o criador ficava amarrado a
            # si mesmo, e uma área com dois gestores não tinha como registrar
            # de quem a atividade era de verdade.
            #
            # A lista é validada no servidor: só gestor do Impulso entra, e a
            # falta de escolha (ou uma escolha inválida) cai em quem criou.
            escolhido = get_gestores().filter(
                id=_int_or_none(request.POST.get('gestor'))).first()
            gestor = escolhido or request.user

            # Demanda para gente de OUTRA área não entra na fila direto: quem
            # responde pela área do colaborador precisa aprovar. Sem isso, um
            # gestor encheria a agenda da equipe de outro sem ele saber.
            if colaborador is not None:
                da_minha_area = (get_colaboradores_do_gestor(request.user)
                                 .filter(id=colaborador.id).exists())
                if not da_minha_area:
                    gestores_da_area = get_gestores_do_setor(colaborador).exclude(
                        id=request.user.id)
                    if not gestores_da_area.exists():
                        messages.error(
                            request,
                            f'{colaborador.get_full_name() or colaborador.email} é de outra '
                            f'área e não há gestor do Impulso cadastrado nela para aprovar '
                            f'a demanda. Fale com o RH para ajustar o cadastro.')
                        return redirect('impulso:meta_create')
                    # Fora da área, a escolha livre acima não vale: quem fica
                    # com a meta é um gestor da área do colaborador, que é
                    # quem precisa aprovar. Deixar o criador apontar alguém de
                    # fora anularia justamente essa trava.
                    aprovador = gestores_da_area.filter(
                        id=_int_or_none(request.POST.get('gestor_aprovador'))).first()
                    gestor = aprovador or gestores_da_area.first()
                    fora_da_area = True
        else:
            # O colaborador só pode pedir para um gestor do SEU setor, e a meta
            # é sempre para ele mesmo — não dá para criar tarefa para terceiros.
            colaborador = request.user
            gestor = gestores_do_setor.filter(
                id=_int_or_none(request.POST.get('gestor'))).first()
            if not gestor:
                messages.error(request, 'Escolha um gestor do seu setor.')
                return redirect('impulso:meta_create')

            # A pessoa decide se a atividade dela precisa passar pelo gestor.
            # Sem autorização, ela entra no Kanban na hora.
            #
            # O gestor continua registrado mesmo quando a autorização é
            # dispensada: é ele quem avalia a meta no fim, e sem esse vínculo a
            # atividade não teria como ser concluída nem valer nota.
            precisa_aprovacao = (request.POST.get('precisa_aprovacao') or 'sim') != 'nao'

        if not (colaborador and titulo and descricao and prazo):
            campo = 'colaborador, título, descrição e prazo' if sou_gestor else 'título, descrição e prazo'
            messages.error(request, f'Preencha {campo}.')
            return redirect('impulso:meta_create')

        # O `min` do campo de data é só sugestão do navegador — um POST direto
        # passa por cima dele. Meta que nasce vencida entraria no Kanban já na
        # coluna de atrasadas, sem ninguém ter como cumpri-la.
        hoje = timezone.localdate()
        if prazo < hoje:
            messages.error(request, 'O prazo não pode ser anterior a hoje.')
            return redirect('impulso:meta_create')

        # Gestor criando para a própria área: aprovada. Para outra área, vai
        # para o gestor de lá. Colaborador: depende do que ele escolheu.
        ja_aprovada = (sou_gestor and not fora_da_area) or (not sou_gestor and not precisa_aprovacao)
        meta = Meta.objects.create(
            gestor=gestor, colaborador=colaborador, titulo=titulo,
            descricao=descricao, recorrencia=recorrencia, prazo=prazo,
            aprovacao=Meta.Aprovacao.APROVADA if ja_aprovada else Meta.Aprovacao.PENDENTE,
            solicitada_por=request.user if (fora_da_area or not sou_gestor) else None,
            created_by=request.user,
        )

        # Outros responsáveis pela mesma meta (só o gestor escolhe).
        if sou_gestor:
            outros = get_colaboradores().filter(
                id__in=request.POST.getlist('participantes')).exclude(id=colaborador.id)
            if outros:
                meta.participantes.set(outros)
                _notify(outros, 'Você foi incluído em uma meta',
                        f'"{meta.titulo}" também é sua responsabilidade.',
                        f'/impulso/metas/{meta.id}/')

        # To-do da meta: uma linha por passo, na ordem em que foram digitados.
        passos = [t.strip() for t in request.POST.getlist('itens') if t.strip()]
        if passos:
            MetaItem.objects.bulk_create([
                MetaItem(meta=meta, texto=p[:300], ordem=n, criado_por=request.user)
                for n, p in enumerate(passos)
            ])

        quem = request.user.get_full_name() or request.user.email
        if sou_gestor and fora_da_area:
            # A área de destino decide; quem pediu fica sabendo que está parado.
            _notify(list(get_gestores_do_setor(colaborador).exclude(id=request.user.id)),
                    'Demanda de outra área para aprovar',
                    f'{quem} pediu a meta "{meta.titulo}" para '
                    f'{colaborador.get_full_name() or colaborador.email}, da sua área. '
                    f'Aprove ou recuse.',
                    f'/impulso/metas/{meta.id}/')
            messages.success(
                request,
                f'{colaborador.get_full_name() or colaborador.email} é de outra área — '
                f'a demanda foi enviada para o gestor da área dele aprovar. '
                f'Ela entra no Kanban depois disso.')
        elif sou_gestor:
            _notify([colaborador], 'Nova meta atribuída',
                    f'"{meta.titulo}" foi atribuída a você.',
                    f'/impulso/metas/{meta.id}/')
            if gestor.id != request.user.id:
                # Ele vai avaliar esta meta no fim: não pode descobrir isso
                # só quando ela aparecer no acompanhamento dele.
                _notify([gestor], 'Meta criada no seu nome',
                        f'{request.user.get_full_name() or request.user.email} criou '
                        f'"{meta.titulo}" com você como gestor responsável. '
                        f'A avaliação no fim é sua.',
                        f'/impulso/metas/{meta.id}/')
            messages.success(
                request,
                'Meta criada com sucesso.' if gestor.id == request.user.id else
                f'Meta criada com {gestor.get_full_name() or gestor.email} como gestor responsável.')
        elif precisa_aprovacao:
            _notify([gestor], 'Nova solicitação de meta',
                    f'{quem} pediu a meta "{meta.titulo}". Aprove ou recuse.',
                    f'/impulso/metas/{meta.id}/')
            messages.success(
                request, 'Solicitação enviada. Ela entra no seu Kanban assim que o gestor aprovar.')
        else:
            # Avisa mesmo sem pedir nada: o gestor vai avaliar esta meta no fim
            # e não pode ser pego de surpresa por uma atividade que apareceu
            # sozinha no acompanhamento dele.
            _notify([gestor], 'Nova atividade criada pelo colaborador',
                    f'{quem} criou "{meta.titulo}" sem pedir autorização. '
                    f'A avaliação no fim continua sendo sua.',
                    f'/impulso/metas/{meta.id}/')
            messages.success(request, 'Atividade criada e já disponível no seu Kanban.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    # Para o gestor, a tela precisa saber quem é de outra área: essa demanda
    # não entra na fila direto, vai para aprovação de lá. Mandamos o mapa
    # pronto para o formulário avisar na hora da escolha, em vez de a pessoa
    # descobrir depois de salvar.
    fora_da_area = {}
    if sou_gestor:
        meus = set(get_colaboradores_do_gestor(request.user).values_list('id', flat=True))
        for c in get_colaboradores().exclude(id__in=meus).select_related('sector'):
            aprovadores = [
                {'id': g.id, 'nome': g.get_full_name() or g.email}
                for g in get_gestores_do_setor(c).exclude(id=request.user.id)
            ]
            fora_da_area[str(c.id)] = {
                'area': c.sector.name if c.sector_id else 'sem setor',
                'gestores': aprovadores,
            }

    context = {
        'sou_gestor': sou_gestor,
        'colaboradores': get_colaboradores() if sou_gestor else None,
        'gestores_do_setor': gestores_do_setor,
        'gestores': get_gestores() if sou_gestor else None,
        'fora_da_area': fora_da_area,
        'setor': getattr(request.user, 'sector', None),
        'recorrencias': Meta.Recorrencia.choices,
        'hoje': timezone.localdate(),
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
def meta_item_toggle(request, item_id):
    """Marca/desmarca um passo do to-do da meta."""
    item = get_object_or_404(MetaItem.objects.select_related('meta'), id=item_id)
    meta = item.meta
    responsaveis = {u.id for u in meta.responsaveis}
    if not (request.user.id in responsaveis or meta.gestor_id == request.user.id
            or request.user.is_superuser):
        return JsonResponse({'ok': False, 'error': 'sem permissão'}, status=403)

    item.concluido = not item.concluido
    item.concluido_em = timezone.now() if item.concluido else None
    item.concluido_por = request.user if item.concluido else None
    item.save(update_fields=['concluido', 'concluido_em', 'concluido_por'])

    feitos, total = meta.progresso_itens
    return JsonResponse({'ok': True, 'concluido': item.concluido,
                         'feitos': feitos, 'total': total})


@require_POST
@impulso_member_required
def meta_item_add(request, meta_id):
    """Acrescenta um passo ao to-do de uma meta já criada."""
    meta = get_object_or_404(Meta, id=meta_id)
    if not (meta.gestor_id == request.user.id or meta.colaborador_id == request.user.id
            or request.user.is_superuser):
        messages.error(request, 'Sem permissão.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    texto = (request.POST.get('texto') or '').strip()
    if texto:
        ultima = meta.itens.order_by('-ordem').first()
        MetaItem.objects.create(meta=meta, texto=texto[:300], criado_por=request.user,
                                ordem=(ultima.ordem + 1) if ultima else 0)
        messages.success(request, 'Item adicionado ao to-do.')
    return redirect('impulso:meta_detail', meta_id=meta.id)


def _pode_mexer_no_item(user, item):
    """Quem edita ou apaga um passo do to-do.

    O to-do é o combinado da meta: quem responde por ela (gestor, colaborador,
    participante) mexe nos passos. Quem só olha, não — apagar um passo some com
    o registro de quem o marcou e quando.
    """
    meta = item.meta
    return (user.is_superuser
            or meta.gestor_id == user.id
            or meta.colaborador_id == user.id
            or meta.participantes.filter(id=user.id).exists())


@require_POST
@impulso_member_required
def meta_item_editar(request, item_id):
    """Corrige o texto de um passo do to-do."""
    item = get_object_or_404(MetaItem.objects.select_related('meta'), id=item_id)
    if not _pode_mexer_no_item(request.user, item):
        messages.error(request, 'Você não pode editar este passo.')
        return redirect('impulso:meta_detail', meta_id=item.meta_id)

    texto = (request.POST.get('texto') or '').strip()
    if not texto:
        messages.error(request, 'O passo não pode ficar sem texto.')
        return redirect('impulso:meta_detail', meta_id=item.meta_id)

    item.texto = texto[:300]
    item.save(update_fields=['texto'])
    messages.success(request, 'Passo atualizado.')
    return redirect('impulso:meta_detail', meta_id=item.meta_id)


@require_POST
@impulso_member_required
def meta_item_excluir(request, item_id):
    """Remove um passo do to-do."""
    item = get_object_or_404(MetaItem.objects.select_related('meta'), id=item_id)
    if not _pode_mexer_no_item(request.user, item):
        messages.error(request, 'Você não pode excluir este passo.')
        return redirect('impulso:meta_detail', meta_id=item.meta_id)

    meta_id = item.meta_id
    texto = item.texto
    item.delete()
    messages.success(request, f'Passo “{texto}” removido.')
    return redirect('impulso:meta_detail', meta_id=meta_id)


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

    # Abriu a meta = viu o que havia até agora. É o marco que apaga o aviso
    # de novidade no card do Kanban.
    MetaVisualizacao.objects.update_or_create(
        meta=meta, user=request.user, defaults={'visto_em': timezone.now()})

    # O template não consegue chamar método com argumento, então cada anexo já
    # chega sabendo se esta pessoa pode mexer nele.
    anexos = list(meta.anexos.select_related('enviado_por'))
    for anexo in anexos:
        anexo.meta = meta  # a meta já está carregada; evita uma consulta por anexo
        anexo.pode_mexer = _pode_mexer_no_anexo(request.user, anexo)

    itens = list(meta.itens.select_related('concluido_por', 'criado_por'))
    pode_mexer_itens = (request.user.is_superuser
                        or meta.gestor_id == request.user.id
                        or meta.colaborador_id == request.user.id
                        or meta.participantes.filter(id=request.user.id).exists())

    pode_editar_participantes = (meta.gestor_id == request.user.id
                                 or request.user.is_superuser)

    context = {
        'meta': meta,
        'itens': itens,
        'pode_mexer_itens': pode_mexer_itens,
        'participantes': meta.participantes.all(),
        'pode_editar_participantes': pode_editar_participantes,
        # Só o gestor precisa da lista inteira de gente para o seletor.
        'candidatos': (get_colaboradores().exclude(id=meta.colaborador_id)
                       if pode_editar_participantes else None),
        'participantes_ids': list(meta.participantes.values_list('id', flat=True)),
        'anexos': anexos,
        'comentarios': meta.comentarios.select_related('autor'),
        'is_gestor_da_meta': meta.gestor_id == request.user.id or request.user.is_superuser,
        'is_colaborador_da_meta': meta.colaborador_id == request.user.id,
        'pode_decidir': meta.pode_decidir(request.user),
        'pode_editar_meta': meta.pode_editar(request.user),
        'proxima_ocorrencia': meta.ocorrencias.first(),
        'notas_range': range(0, 6),
        'url_voltar': _url_kanban(request),
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


@impulso_member_required
def meta_editar(request, meta_id):
    """Edita título, descrição, prazo e recorrência de uma atividade do Confiar."""
    meta = get_object_or_404(Meta, id=meta_id)
    if not meta.pode_editar(request.user):
        messages.error(request, 'Você não pode editar esta atividade.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    if request.method == 'POST':
        titulo = (request.POST.get('titulo') or '').strip()
        descricao = (request.POST.get('descricao') or '').strip()
        prazo = parse_date(request.POST.get('prazo') or '')
        recorrencia = request.POST.get('recorrencia') or meta.recorrencia

        if not titulo or not prazo:
            messages.error(request, 'Título e prazo são obrigatórios.')
            return redirect('impulso:meta_editar', meta_id=meta.id)
        if recorrencia not in Meta.Recorrencia.values:
            recorrencia = meta.recorrencia

        # O prazo pode ir para trás numa edição — a atividade já existe e às
        # vezes o combinado mudou. O que não pode é nascer vencida, e isso a
        # tela de criação já barra.
        antes = {'titulo': meta.titulo, 'prazo': meta.prazo}
        meta.titulo = titulo[:200]
        meta.descricao = descricao
        meta.prazo = prazo
        meta.recorrencia = recorrencia
        meta.save(update_fields=['titulo', 'descricao', 'prazo', 'recorrencia'])

        # Mudança de meta alheia vira comentário: quem toca a atividade não pode
        # descobrir por acaso que o prazo mudou.
        mudou = []
        if antes['titulo'] != meta.titulo:
            mudou.append(f'título: "{antes["titulo"]}" → "{meta.titulo}"')
        if antes['prazo'] != meta.prazo:
            mudou.append(f'prazo: {antes["prazo"]:%d/%m/%Y} → {meta.prazo:%d/%m/%Y}')
        if mudou:
            quem = request.user.get_full_name() or request.user.email
            MetaComentario.objects.create(
                meta=meta, autor=request.user,
                mensagem=f'{quem} editou a atividade — ' + '; '.join(mudou))
            _notify([u for u in meta.responsaveis if u.id != request.user.id],
                    'Atividade alterada',
                    f'"{meta.titulo}" foi editada por {quem}.',
                    f'/impulso/metas/{meta.id}/')

        messages.success(request, 'Atividade atualizada.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    return render(request, 'impulso/meta_editar.html', {
        'meta': meta,
        'recorrencias': Meta.Recorrencia.choices,
        'active_tab': 'confiar',
    })


@require_POST
@impulso_member_required
def meta_duplicar(request, meta_id):
    """Cria uma cópia da atividade com os passos e a descrição.

    Copia o que descreve o trabalho — título, descrição, prazo, recorrência,
    responsáveis e o to-do inteiro. Não copia o que é histórico daquela
    execução: status, entrega, avaliação, nota, comentários e anexos. A cópia
    nasce "A fazer" e já aprovada, e a tela abre na edição — duplicar existe
    justamente para mudar alguma coisa antes de valer.
    """
    original = get_object_or_404(Meta, id=meta_id)
    if not original.pode_editar(request.user):
        messages.error(request, 'Você não pode duplicar esta atividade.')
        return redirect('impulso:meta_detail', meta_id=original.id)

    titulo = f'Cópia de {original.titulo}'[:200]

    # Prazo no passado viraria card nascendo atrasado; puxa para hoje.
    hoje = timezone.localdate()
    prazo = original.prazo if original.prazo and original.prazo >= hoje else hoje

    copia = Meta.objects.create(
        gestor=original.gestor,
        colaborador=original.colaborador,
        titulo=titulo,
        descricao=original.descricao,
        recorrencia=original.recorrencia,
        prazo=prazo,
        aprovacao=Meta.Aprovacao.APROVADA,
        created_by=request.user,
    )
    copia.participantes.set(original.participantes.all())

    passos = list(original.itens.order_by('ordem', 'id'))
    if passos:
        MetaItem.objects.bulk_create([
            MetaItem(meta=copia, texto=p.texto, ordem=n, criado_por=request.user)
            for n, p in enumerate(passos)
        ])

    messages.success(
        request,
        f'Atividade duplicada com {len(passos)} passo(s). Ajuste o que precisar e salve.')
    return redirect('impulso:meta_editar', meta_id=copia.id)


@impulso_member_required
def conteudo_editar(request, conteudo_id):
    """Edita um curso, vídeo ou POP do Conectar. Mesmo público do excluir."""
    conteudo = get_object_or_404(ConteudoConectar, id=conteudo_id)
    if not conteudo.pode_excluir(request.user):
        messages.error(request, 'Só o SUPERADMIN ou um gestor do Impulso pode editar conteúdo.')
        return redirect('impulso:conteudo_detail', conteudo_id=conteudo.id)

    if request.method == 'POST':
        titulo = (request.POST.get('titulo') or '').strip()
        if not titulo:
            messages.error(request, 'Informe o título.')
            return redirect('impulso:conteudo_editar', conteudo_id=conteudo.id)

        tipo = request.POST.get('tipo') or conteudo.tipo
        if tipo not in ConteudoConectar.Tipo.values:
            tipo = conteudo.tipo

        conteudo.tipo = tipo
        conteudo.titulo = titulo[:200]
        conteudo.descricao = (request.POST.get('descricao') or '').strip()
        conteudo.url = (request.POST.get('url') or '').strip()
        conteudo.obrigatorio = bool(request.POST.get('obrigatorio'))
        conteudo.inicio = parse_date(request.POST.get('inicio') or '') or None
        conteudo.fim = parse_date(request.POST.get('fim') or '') or None

        novo_arquivo = request.FILES.get('arquivo')
        if novo_arquivo:
            # Troca o arquivo e apaga o antigo — deixar os dois ocupa espaço e
            # ninguém volta para o anterior.
            antigo = conteudo.arquivo
            conteudo.arquivo = novo_arquivo
            conteudo.save()
            if antigo:
                antigo.delete(save=False)
        else:
            conteudo.save()

        antes = set(conteudo.obrigatorio_para.values_list('id', flat=True))
        ids = request.POST.getlist('obrigatorio_para')
        escolhidos = get_colaboradores().filter(id__in=ids)
        conteudo.obrigatorio_para.set(escolhidos)
        novos = [u for u in escolhidos if u.id not in antes]
        if novos:
            _notify(novos, f'Novo {conteudo.get_tipo_display().lower()} obrigatório',
                    f'"{conteudo.titulo}" foi atribuído a você.',
                    f'/impulso/conectar/{conteudo.id}/')

        messages.success(request, 'Conteúdo atualizado.')
        return redirect('impulso:conteudo_detail', conteudo_id=conteudo.id)

    return render(request, 'impulso/conteudo_form.html', {
        'conteudo': conteudo,
        'is_gestor': is_impulso_manager(request.user),
        'tipos': ConteudoConectar.Tipo.choices,
        'colaboradores': get_colaboradores(),
        'marcados': set(conteudo.obrigatorio_para.values_list('id', flat=True)),
        'active_tab': 'conectar',
    })


def _pode_mexer_no_anexo(user, anexo):
    """Quem edita ou apaga um anexo.

    Quem anexou mexe no que é seu; o gestor da meta e o superusuário mexem em
    qualquer um, porque respondem pela meta. Colega participante não apaga
    anexo de outro — remover arquivo é irreversível.
    """
    return (user.is_superuser
            or anexo.enviado_por_id == user.id
            or anexo.meta.gestor_id == user.id)


@require_POST
@impulso_member_required
def meta_participantes_editar(request, meta_id):
    """Troca quem mais responde pela meta. Só o gestor dela (ou superusuário).

    A troca vira comentário na meta: entrar e sair de uma responsabilidade é o
    tipo de mudança que ninguém deve descobrir por acaso.
    """
    meta = get_object_or_404(Meta, id=meta_id)
    if not (meta.gestor_id == request.user.id or request.user.is_superuser):
        messages.error(request, 'Apenas o gestor da meta muda os responsáveis.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    antes = set(meta.participantes.values_list('id', flat=True))
    escolhidos = get_colaboradores().filter(
        id__in=request.POST.getlist('participantes')).exclude(id=meta.colaborador_id)
    depois = {u.id for u in escolhidos}

    if antes == depois:
        messages.info(request, 'Nada mudou nos responsáveis.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    meta.participantes.set(escolhidos)

    User = get_user_model()
    entraram = list(User.objects.filter(id__in=depois - antes))
    sairam = list(User.objects.filter(id__in=antes - depois))

    if entraram:
        _notify(entraram, 'Você foi incluído em uma meta',
                f'"{meta.titulo}" também é sua responsabilidade.',
                f'/impulso/metas/{meta.id}/')
    if sairam:
        _notify(sairam, 'Você saiu de uma meta',
                f'Você não é mais responsável por "{meta.titulo}".',
                '/impulso/metas/')

    partes = []
    if entraram:
        partes.append('incluiu ' + ', '.join(u.get_full_name() or u.email for u in entraram))
    if sairam:
        partes.append('removeu ' + ', '.join(u.get_full_name() or u.email for u in sairam))
    MetaComentario.objects.create(
        meta=meta, autor=request.user,
        mensagem='Responsáveis: ' + ' e '.join(partes) + '.')

    messages.success(request, 'Responsáveis atualizados.')
    return redirect('impulso:meta_detail', meta_id=meta.id)


@require_POST
@impulso_member_required
def meta_anexo_editar(request, meta_id, anexo_id):
    """Renomeia o anexo e, se for link, permite corrigir a URL."""
    meta = get_object_or_404(Meta, id=meta_id)
    anexo = get_object_or_404(MetaAnexo, id=anexo_id, meta=meta)

    if not _pode_ver_meta(request.user, meta) or not _pode_mexer_no_anexo(request.user, anexo):
        messages.error(request, 'Você não pode editar este anexo.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    anexo.titulo = (request.POST.get('titulo') or '').strip()[:200]

    if anexo.tipo == MetaAnexo.Tipo.LINK:
        nova_url = (request.POST.get('url') or '').strip()
        if not nova_url:
            messages.error(request, 'O link não pode ficar vazio.')
            return redirect('impulso:meta_detail', meta_id=meta.id)
        anexo.url = nova_url

    # Troca do arquivo: o antigo sai do storage para não virar lixo.
    novo_arquivo = request.FILES.get('arquivo')
    if novo_arquivo and anexo.tipo == MetaAnexo.Tipo.ARQUIVO:
        antigo = anexo.arquivo
        anexo.arquivo = novo_arquivo
        anexo.save()
        if antigo:
            antigo.delete(save=False)
        messages.success(request, 'Arquivo substituído.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    anexo.save()
    messages.success(request, 'Anexo atualizado.')
    return redirect('impulso:meta_detail', meta_id=meta.id)


@require_POST
@impulso_member_required
def meta_anexo_excluir(request, meta_id, anexo_id):
    """Remove o anexo e o arquivo correspondente do storage."""
    meta = get_object_or_404(Meta, id=meta_id)
    anexo = get_object_or_404(MetaAnexo, id=anexo_id, meta=meta)

    if not _pode_ver_meta(request.user, meta) or not _pode_mexer_no_anexo(request.user, anexo):
        messages.error(request, 'Você não pode excluir este anexo.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    nome = anexo.nome_exibicao
    arquivo = anexo.arquivo if anexo.tipo == MetaAnexo.Tipo.ARQUIVO else None
    anexo.delete()
    if arquivo:
        # Só depois de apagar a linha: se o delete do banco falhar, o arquivo
        # continua lá e nada fica órfão pelo contrário.
        arquivo.delete(save=False)

    messages.success(request, f'“{nome}” foi excluído.')
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


@require_POST
@impulso_member_required
def meta_excluir_comentario(request, comentario_id):
    """Apaga um comentário da tarefa.

    Quem apaga: o autor do comentário, o gestor da meta ou um superusuário.
    Colega nenhum apaga o comentário do outro — num histórico de tarefa isso
    seria reescrever o que a outra pessoa disse.
    """
    comentario = get_object_or_404(
        MetaComentario.objects.select_related('meta'), id=comentario_id)
    meta = comentario.meta

    pode = (request.user.is_superuser
            or comentario.autor_id == request.user.id
            or meta.gestor_id == request.user.id)
    if not pode:
        messages.error(request, 'Você só pode excluir os seus próprios comentários.')
        return redirect('impulso:meta_detail', meta_id=meta.id)

    comentario.delete()
    messages.success(request, 'Comentário excluído.')
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
def assiduidade(request):
    """A assiduidade do mês, lida do ponto eletrônico.

    Mostra a conta inteira — dias completos, ajustes usados e o que ainda dá
    para corrigir. O colaborador vê a dele; o gestor vê a equipe, porque
    ajuste vencendo é coisa que alguém precisa cobrar antes de virar perda.
    """
    from .assiduidade_ponto import (LIMITE_AJUSTES_MES, PONTOS_ASSIDUIDADE,
                                    nota_assiduidade_ponto)

    hoje = timezone.localdate()
    mes = _int_or_none(request.GET.get('mes'), 1, 12) or hoje.month
    ano = _int_or_none(request.GET.get('ano'), 2000, 2100) or hoje.year

    def _linha(pessoa):
        resposta = nota_assiduidade_ponto(pessoa, ano, mes)
        if resposta is None:
            return {'pessoa': pessoa, 'sem_ponto': True}
        pontos, maximo, detalhe = resposta
        return {'pessoa': pessoa, 'pontos': pontos, 'maximo': maximo,
                'detalhe': detalhe, 'sem_ponto': False}

    minha = _linha(request.user)

    equipe = None
    if is_impulso_manager(request.user) or request.user.is_superuser:
        # A equipe do gestor são os colaboradores dos setores atrelados a ele —
        # todos eles, não só o setor principal. Gestor sem setor cadastrado (e o
        # superadmin) continua vendo a rede inteira, senão a tela nasceria vazia
        # para quem administra o módulo.
        equipe = [_linha(u) for u in get_colaboradores_do_gestor(request.user)
                  if u.id != request.user.id and u.tangerino_employee_id]
        equipe = [l for l in equipe if not l['sem_ponto']]
        # Quem perdeu ponto aparece primeiro; depois quem está no limite.
        equipe.sort(key=lambda l: (l['pontos'] > 0,
                                   -(l['detalhe'].get('total_ajustes') or 0),
                                   l['pessoa'].first_name))

    return render(request, 'impulso/assiduidade.html', {
        'minha': minha,
        'equipe': equipe,
        'mes': mes, 'ano': ano,
        'hoje': hoje,
        'primeiro_do_mes': date(ano, mes, 1),
        'meses': list(enumerate(
            ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 'Julho',
             'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro'], start=1)),
        'anos': list(range(hoje.year - 1, hoje.year + 1)),
        'limite_ajustes': LIMITE_AJUSTES_MES,
        'pontos_max': PONTOS_ASSIDUIDADE,
        'is_gestor': is_impulso_manager(request.user),
        'pode_excecao': request.user.is_superuser,
        'excecoes': ExcecaoAssiduidade.objects.filter(
            data__year=ano, data__month=mes).select_related('criado_por'),
        'active_tab': 'confiar',
    })


@impulso_member_required
@require_POST
def assiduidade_excecao_add(request):
    """Libera o ajuste de um dia. Só o SUPERADMIN.

    Mexe na nota de todo mundo naquele mês, então não passa de superadmin nem
    para gestor do módulo.
    """
    if not request.user.is_superuser:
        messages.error(request, 'Apenas o superadmin cria exceção de assiduidade.')
        return redirect('impulso:assiduidade')

    dia = parse_date((request.POST.get('data') or '').strip())
    motivo = (request.POST.get('motivo') or '').strip()

    if not dia:
        messages.error(request, 'Escolha o dia da exceção.')
        return redirect(_url_assiduidade(request))
    if not motivo:
        # Sem motivo, em três meses ninguém lembra por que o dia foi liberado.
        messages.error(request, 'Escreva o motivo — ele aparece na tela para todo mundo.')
        return redirect(_url_assiduidade(request))

    _, criada = ExcecaoAssiduidade.objects.get_or_create(
        data=dia, defaults={'motivo': motivo[:200], 'criado_por': request.user})
    if criada:
        messages.success(
            request, f'{dia:%d/%m/%Y} virou exceção: o ajuste desse dia deixa de contar.')
    else:
        messages.info(request, f'{dia:%d/%m/%Y} já era uma exceção.')
    return redirect(_url_assiduidade(request, dia))


@impulso_member_required
@require_POST
def assiduidade_excecao_excluir(request, excecao_id):
    """Desfaz a exceção — o ajuste daquele dia volta a contar."""
    if not request.user.is_superuser:
        messages.error(request, 'Apenas o superadmin mexe nas exceções de assiduidade.')
        return redirect('impulso:assiduidade')

    excecao = get_object_or_404(ExcecaoAssiduidade, id=excecao_id)
    dia = excecao.data
    excecao.delete()
    messages.success(request, f'{dia:%d/%m/%Y} deixou de ser exceção — '
                              'o ajuste desse dia volta a contar.')
    return redirect(_url_assiduidade(request, dia))


def _url_assiduidade(request, dia=None):
    """Volta para o mês que a pessoa estava vendo, não para o mês corrente."""
    mes = _int_or_none(request.POST.get('mes') or request.GET.get('mes'), 1, 12)
    ano = _int_or_none(request.POST.get('ano') or request.GET.get('ano'), 2000, 2100)
    if dia is not None and not (mes and ano):
        mes, ano = dia.month, dia.year
    base = reverse('impulso:assiduidade')
    return f'{base}?mes={mes}&ano={ano}' if (mes and ano) else base


@impulso_member_required
def feedback_list(request):
    """Lista de feedbacks, com filtros e o panorama das notas da IA.

    Quem vê o quê: superadmin enxerga a empresa inteira; gestor vê os que
    aplicou; colaborador vê os que recebeu.
    """
    user = request.user
    gestor = is_impulso_manager(user)
    tudo = user.is_superuser

    if tudo:
        feedbacks = ImpulsoFeedback.objects.all()
    elif gestor:
        # Mesma regra do Kanban: os setores do gestor, não só os feedbacks que
        # ele mesmo aplicou.
        equipe = list(get_colaboradores_do_gestor(user).values_list('id', flat=True))
        feedbacks = ImpulsoFeedback.objects.filter(
            Q(gestor=user) | Q(colaborador_id__in=equipe)).distinct()
    else:
        feedbacks = ImpulsoFeedback.objects.filter(colaborador=user)
    feedbacks = feedbacks.select_related('colaborador', 'gestor')

    # ── Filtros ─────────────────────────────────────────────────────────────
    colaborador_id = _int_or_none(request.GET.get('colaborador'))
    gestor_id = _int_or_none(request.GET.get('gestor'))
    mes = (request.GET.get('mes') or '').strip()          # 'YYYY-MM'
    situacao = (request.GET.get('situacao') or '').strip()
    busca = (request.GET.get('q') or '').strip()

    if colaborador_id:
        feedbacks = feedbacks.filter(colaborador_id=colaborador_id)
    if gestor_id:
        feedbacks = feedbacks.filter(gestor_id=gestor_id)
    if mes:
        try:
            ano_f, mes_f = mes.split('-')
            feedbacks = feedbacks.filter(referencia_mes__year=int(ano_f),
                                         referencia_mes__month=int(mes_f))
        except (ValueError, TypeError):
            mes = ''
    if situacao == 'sem_analise':
        feedbacks = feedbacks.filter(ai_summary='')
    elif situacao == 'com_analise':
        feedbacks = feedbacks.exclude(ai_summary='')
    elif situacao == 'atencao':
        feedbacks = feedbacks.filter(nota_ia__lt=5)
    if busca:
        feedbacks = feedbacks.filter(
            Q(colaborador__first_name__icontains=busca)
            | Q(colaborador__last_name__icontains=busca)
            | Q(colaborador__email__icontains=busca)
            | Q(pontos_fortes__icontains=busca)
            | Q(pontos_melhoria__icontains=busca))

    lista = list(feedbacks)

    # ── Panorama ────────────────────────────────────────────────────────────
    notas = [float(f.nota_ia) for f in lista if f.nota_ia is not None]
    resumo = {
        'total': len(lista),
        'com_analise': sum(1 for f in lista if f.ai_summary),
        'sem_analise': sum(1 for f in lista if not f.ai_summary),
        'media': round(sum(notas) / len(notas), 1) if notas else None,
        'abaixo': sum(1 for n in notas if n < 5),
        'pessoas': len({f.colaborador_id for f in lista}),
    }
    resumo['media_percentual'] = int(resumo['media'] * 10) if resumo['media'] else 0

    # Distribuição por faixa, para a barra do topo.
    contagens = {
        'abaixo': sum(1 for n in notas if n < 5),
        'parcial': sum(1 for n in notas if 5 <= n < 7),
        'esperado': sum(1 for n in notas if 7 <= n < 9),
        'acima': sum(1 for n in notas if n >= 9),
    }
    total_notas = len(notas) or 1
    resumo['faixas'] = [dict(FAIXAS_DA_NOTA[chave], n=n,
                             pct=round(n / total_notas * 100))
                        for chave, n in contagens.items()]
    resumo['faixa_media'] = (
        FAIXAS_DA_NOTA['abaixo'] if resumo['media'] and resumo['media'] < 5 else
        FAIXAS_DA_NOTA['parcial'] if resumo['media'] and resumo['media'] < 7 else
        FAIXAS_DA_NOTA['esperado'] if resumo['media'] and resumo['media'] < 9 else
        FAIXAS_DA_NOTA['acima'] if resumo['media'] else None)

    # ── Feedback formal do mês (módulo /feedback/) ──────────────────────────
    # São dois registros da mesma conversa: aqui fica o texto do gestor, e no
    # /feedback/ fica o formulário FM-005, que tem as notas. É a nota de lá que
    # vira ponto de feedback no Impulso — mostrá-la aqui evita abrir outro
    # módulo só para entender de onde saiu a pontuação.
    from .scoring import (FEEDBACK_NOTA_MINIMA, PT_FEEDBACK, avaliar_feedback,
                          periodo_do_mes)

    inicio_mes, fim_mes = periodo_do_mes()

    formais = {}
    try:
        from feedback.models import Feedback as FeedbackFormal
        do_mes = FeedbackFormal.objects.filter(data__gte=inicio_mes, data__lte=fim_mes)
        if not tudo:
            # Gestor vê a nota de quem ele acompanha; colaborador, só a sua.
            alvos = {f.colaborador_id for f in lista}
            if gestor:
                alvos |= {u.id for u in get_colaboradores()}
            alvos.add(user.id)
            do_mes = do_mes.filter(evaluatee_id__in=alvos)

        for fb in (do_mes
                   .select_related('evaluatee', 'evaluator')
                   .order_by('-data', '-created_at')):
            dados = avaliar_feedback(fb)
            if dados is None:
                continue
            escolhido = formais.get(fb.evaluatee_id)
            # A mesma regra da pontuação, na mesma função: vale o que garante o
            # ponto e, entre dois que garantem, a nota maior. Duas contas
            # diferentes para o mesmo número foi erro que já aconteceu aqui.
            if escolhido and not (
                    (dados['atingiu'] and not escolhido['atingiu'])
                    or (dados['atingiu'] == escolhido['atingiu']
                        and dados['nota'] > escolhido['nota'])):
                continue
            formais[fb.evaluatee_id] = dict(
                dados, colaborador=fb.evaluatee, feedback=fb,
                pontos=PT_FEEDBACK if dados['atingiu'] else 0)
    except Exception:                                       # módulo indisponível
        formais = {}

    for f in lista:
        f.formal = formais.get(f.colaborador_id)

    com_nota = list(formais.values())
    resumo_formal = {
        'com_nota': len(com_nota),
        'atingiram': sum(1 for d in com_nota if d['atingiu']),
        'media': round(sum(d['nota'] for d in com_nota) / len(com_nota), 1)
        if com_nota else None,
        'minimo': FEEDBACK_NOTA_MINIMA,
        'pontos': PT_FEEDBACK,
        'mes': inicio_mes,
        # Tem nota no mês e ainda não tem feedback do Impulso registrado.
        'sem_registro': sorted(
            (d for uid, d in formais.items()
             if uid not in {f.colaborador_id for f in lista}),
            key=lambda d: -d['nota'])[:12],
    }

    # Opções dos filtros saem do universo visível, não da lista já filtrada —
    # senão, ao escolher alguém, os outros sumiriam do próprio seletor.
    if tudo:
        universo = ImpulsoFeedback.objects.all()
    elif gestor:
        universo = ImpulsoFeedback.objects.filter(
            Q(gestor=user) | Q(colaborador_id__in=list(
                get_colaboradores_do_gestor(user).values_list('id', flat=True)))).distinct()
    else:
        universo = ImpulsoFeedback.objects.filter(colaborador=user)
    context = {
        'feedbacks': lista,
        'resumo': resumo,
        'formal': resumo_formal,
        'is_gestor': gestor,
        've_tudo': tudo,
        'colaboradores': (User.objects.filter(
            id__in=universo.values_list('colaborador_id', flat=True))
            .order_by('first_name', 'last_name')),
        'gestores': (User.objects.filter(
            id__in=universo.values_list('gestor_id', flat=True))
            .order_by('first_name', 'last_name')) if (tudo or gestor) else None,
        'meses': sorted({f.referencia_mes.strftime('%Y-%m')
                         for f in universo.only('referencia_mes')}, reverse=True),
        'f_colaborador': colaborador_id, 'f_gestor': gestor_id,
        'f_mes': mes, 'f_situacao': situacao, 'f_busca': busca,
        'tem_filtro': any([colaborador_id, gestor_id, mes, situacao, busca]),
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

    # Gera a análise sob demanda sempre que ainda não houver uma.
    #
    # Antes a condição também exigia `not fb.ai_summary_error`: bastava uma
    # falha para o feedback ficar sem análise para sempre, porque toda visita
    # seguinte via o erro gravado e desistia. Agora abrir o feedback é uma nova
    # chance — e a própria geração já tenta várias vezes por dentro.
    ai.garantir_resumo(fb)

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
        if generate_feedback_summary(fb, force=True):
            fb.refresh_from_db()
            nota = f' Nota da IA: {fb.nota_ia}.' if fb.nota_ia is not None else ''
            messages.success(request, f'Análise da IA atualizada.{nota}')
        else:
            messages.error(
                request,
                f'A IA não respondeu depois de {ai.TENTATIVAS} tentativas. '
                f'Último erro: {fb.ai_summary_error or "desconhecido"}')
    except Exception as exc:
        messages.error(request, f'Não foi possível gerar a análise agora: {exc}')
    return redirect('impulso:feedback_detail', fb_id=fb.id)


# ---------------------------------------------------------------------------
# CONECTAR — Conteúdos (cursos/vídeos/POPs)
# ---------------------------------------------------------------------------
def conteudos_para(user, gestor=False):
    """Conteúdos do CONECTAR que esta pessoa precisa fazer.

    A regra vem do próprio cadastro do conteúdo:

    * ``obrigatorio_para`` com pessoas → aparece só para elas;
    * ``obrigatorio_para`` vazio → vale para toda a equipe, como já dizia o
      texto de ajuda do campo (por isso o vazio continua aparecendo para todos:
      mudar isso sumiria com todo o conteúdo já cadastrado).

    Gestor vê tudo — é quem sobe e acompanha o material dos outros.
    """
    base = ConteudoConectar.objects.filter(ativo=True)
    if gestor:
        return base
    # `criado_por` entra na conta para quem sobe um POP não perder o próprio
    # material de vista quando ele é direcionado a outra pessoa.
    return base.filter(
        Q(obrigatorio_para__isnull=True)
        | Q(obrigatorio_para=user)
        | Q(criado_por=user)
    ).distinct()


def pode_ver_conteudo(user, conteudo):
    """A pessoa foi designada para este conteúdo (ou é gestor)?"""
    if is_impulso_manager(user):
        return True
    if not conteudo.ativo:
        return False
    if conteudo.criado_por_id == user.pk:
        return True
    designados = conteudo.obrigatorio_para.all()
    if not designados:
        return True
    return any(u.pk == user.pk for u in designados)


@impulso_member_required
def conectar_list(request):
    user = request.user
    gestor = is_impulso_manager(user)
    conteudos = (conteudos_para(user, gestor)
                 .prefetch_related('conclusoes', 'obrigatorio_para'))

    # status de conclusão do usuário atual
    minhas = {c.conteudo_id: c for c in
              ConclusaoConteudo.objects.filter(user=user)}

    # A permissão de excluir é resolvida aqui, uma vez por card: o template não
    # chama método com argumento, e repetir a regra no HTML criaria uma segunda
    # verdade sobre quem pode o quê.
    grupos = {'CURSO': [], 'VIDEO': [], 'POP': []}
    for c in conteudos:
        c.minha_conclusao = minhas.get(c.id)
        c.pode_apagar = c.pode_excluir(user)
        c.impacto = c.impacto_da_exclusao if c.pode_apagar else None
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
    # Sumir da lista sem fechar o detalhe seria esconder, não restringir: o
    # endereço do conteúdo é sequencial e fácil de adivinhar.
    if not pode_ver_conteudo(request.user, conteudo):
        messages.error(request, 'Este conteúdo não foi direcionado para você.')
        return redirect('impulso:conectar_list')

    conclusao = ConclusaoConteudo.objects.filter(
        conteudo=conteudo, user=request.user).first()
    context = {
        'conteudo': conteudo,
        'conclusao': conclusao,
        'is_gestor': is_impulso_manager(request.user),
        'conclusoes': conteudo.conclusoes.select_related('user') if is_impulso_manager(request.user) else None,
        'pode_apagar': conteudo.pode_excluir(request.user),
        'impacto': conteudo.impacto_da_exclusao,
        'active_tab': 'conectar',
    }
    return render(request, 'impulso/conteudo_detail.html', context)


@require_POST
@impulso_member_required
def conteudo_excluir(request, conteudo_id):
    """Tira um curso/vídeo/POP do ar de vez.

    Some com as conclusões junto (CASCADE) — e conclusão é ponto do mês de
    quem fez. Por isso a tela mostra o tamanho do estrago antes de perguntar,
    e o aviso vai para quem já tinha concluído.
    """
    conteudo = get_object_or_404(ConteudoConectar, id=conteudo_id)
    if not conteudo.pode_excluir(request.user):
        messages.error(request, 'Só o SUPERADMIN ou um gestor do Impulso pode excluir conteúdo.')
        return redirect('impulso:conteudo_detail', conteudo_id=conteudo.id)

    titulo = conteudo.titulo
    impacto = conteudo.impacto_da_exclusao
    concluintes = list(User.objects.filter(
        id__in=conteudo.conclusoes.filter(concluido=True).values_list('user_id', flat=True)))

    conteudo.delete()

    if concluintes:
        _notify(concluintes, 'Conteúdo removido do Conectar',
                f'"{titulo}" foi removido pelo gestor. A conclusão que você tinha '
                f'nele deixa de contar na pontuação do mês.',
                '/impulso/conectar/')

    if impacto['concluidos']:
        messages.warning(
            request,
            f'"{titulo}" excluído. {impacto["concluidos"]} pessoa(s) já tinham concluído — '
            f'a pontuação do mês delas foi recalculada.')
    else:
        messages.success(request, f'"{titulo}" excluído.')
    return redirect('impulso:conectar_list')


@require_POST
@impulso_member_required
def conteudo_progresso_video(request, conteudo_id):
    """Recebe o avanço da reprodução e guarda até onde a pessoa assistiu.

    O navegador avisa a cada poucos segundos. O servidor só aceita avanço
    compatível com o tempo real decorrido: sem isso, bastaria mandar
    "assisti tudo" de uma vez e o vídeo obrigatório viraria enfeite.
    """
    conteudo = get_object_or_404(ConteudoConectar, id=conteudo_id)
    if not pode_ver_conteudo(request.user, conteudo):
        return JsonResponse({'ok': False, 'erro': 'Conteúdo não direcionado para você.'}, status=403)
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
    if not pode_ver_conteudo(request.user, conteudo):
        messages.error(request, 'Este conteúdo não foi direcionado para você.')
        return redirect('impulso:conectar_list')

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

    anexos = list(projeto.anexos.select_related('enviado_por'))
    for a in anexos:
        a.pode_apagar = a.pode_mexer(request.user)

    context = {
        'projeto': projeto,
        'tarefas': tarefas,
        'is_gestor': gestor,
        'membros': projeto.membros.all(),
        'anexos': anexos,
        'status_choices': TarefaProjeto.Status.choices,
        'active_tab': 'conectar',
    }
    return render(request, 'impulso/projeto_detail.html', context)


def _pode_ver_projeto(user, projeto):
    """Quem abre o projeto: gestor do Impulso ou membro dele."""
    return (is_impulso_manager(user)
            or projeto.membros.filter(id=user.id).exists())


@require_POST
@impulso_member_required
def projeto_anexo_add(request, projeto_id):
    """Anexa arquivo ou link ao projeto foco.

    Quem participa do projeto anexa: o material do projeto é de quem toca o
    projeto, não só de quem o criou.
    """
    projeto = get_object_or_404(ProjetoFoco, id=projeto_id)
    if not _pode_ver_projeto(request.user, projeto):
        messages.error(request, 'Você não faz parte deste projeto.')
        return redirect('impulso:projeto_foco_list')

    titulo = (request.POST.get('titulo') or '').strip()[:200]
    url = (request.POST.get('url') or '').strip()
    arquivo = request.FILES.get('arquivo')

    if arquivo:
        ProjetoAnexo.objects.create(
            projeto=projeto, tipo=ProjetoAnexo.Tipo.ARQUIVO, titulo=titulo,
            arquivo=arquivo, enviado_por=request.user)
        messages.success(request, 'Arquivo anexado ao projeto.')
    elif url:
        ProjetoAnexo.objects.create(
            projeto=projeto, tipo=ProjetoAnexo.Tipo.LINK, titulo=titulo,
            url=url[:200], enviado_por=request.user)
        messages.success(request, 'Link anexado ao projeto.')
    else:
        messages.error(request, 'Envie um arquivo ou informe um link.')
    return redirect('impulso:projeto_foco_detail', projeto_id=projeto.id)


@require_POST
@impulso_member_required
def projeto_anexo_excluir(request, projeto_id, anexo_id):
    """Remove o anexo e o arquivo correspondente do storage."""
    projeto = get_object_or_404(ProjetoFoco, id=projeto_id)
    anexo = get_object_or_404(ProjetoAnexo, id=anexo_id, projeto=projeto)

    if not _pode_ver_projeto(request.user, projeto) or not anexo.pode_mexer(request.user):
        messages.error(request, 'Você não pode excluir este anexo.')
        return redirect('impulso:projeto_foco_detail', projeto_id=projeto.id)

    nome = anexo.nome_exibicao
    arquivo = anexo.arquivo if anexo.tipo == ProjetoAnexo.Tipo.ARQUIVO else None
    anexo.delete()
    if arquivo:
        # Só depois de apagar a linha: se o banco falhar, o arquivo continua
        # lá e nada fica órfão pelo contrário.
        arquivo.delete(save=False)

    messages.success(request, f'“{nome}” foi excluído.')
    return redirect('impulso:projeto_foco_detail', projeto_id=projeto.id)


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
    if gestor:
        ideias = Ideia.objects.all()
    else:
        # Quem foi incluído na ideia também precisa vê-la: é dela que vêm os
        # pontos dele. Sem isso, a pessoa ganharia a nota sem saber por quê.
        ideias = Ideia.objects.filter(Q(autor=user) | Q(participantes=user)).distinct()

    lista = list(ideias.select_related('autor').prefetch_related('participantes'))
    ids_participando = set()
    for ideia in lista:
        equipe = list(ideia.participantes.all())
        # Único caso em que o nome aparece: a ideia é de quem está vendo.
        ideia.mostrar_autor = (ideia.autor_id == user.id)
        ideia.sou_participante = any(p.id == user.id for p in equipe)
        # A autoria fica escondida do gestor de propósito — os nomes de quem
        # participou seguem a mesma regra, senão a anonimidade cairia por aí.
        ideia.equipe_visivel = equipe if ideia.mostrar_autor else []
        if ideia.sou_participante:
            ids_participando.add(ideia.id)

    context = {
        'ideias': lista,
        'is_gestor': gestor,
        'status_choices': Ideia.Status.choices,
        'active_tab': 'inovar',
    }
    return render(request, 'impulso/inovar_list.html', context)


def _participantes_da_ideia(request, autor):
    """Quem mais assina a ideia, dentro do limite.

    Devolve (pessoas, erro). O autor sai da lista mesmo se vier marcado: ele já
    pontua como autor, e contá-lo de novo gastaria uma das três vagas à toa.
    """
    escolhidos = get_colaboradores().filter(
        id__in=request.POST.getlist('participantes')).exclude(id=autor.id)

    if escolhidos.count() > Ideia.MAX_PARTICIPANTES:
        return None, (f'Escolha no máximo {Ideia.MAX_PARTICIPANTES} pessoas além de você. '
                      'O limite existe para a ideia ter donos claros.')
    return list(escolhidos), ''


@impulso_member_required
def ideia_create(request):
    if request.method == 'POST':
        descricao = (request.POST.get('descricao') or '').strip()
        setor_impacto = (request.POST.get('setor_impacto') or '').strip()
        motivo = (request.POST.get('motivo') or '').strip()
        if not (descricao and setor_impacto and motivo):
            messages.error(request, 'Preencha a ideia, o setor de impacto e o motivo.')
            return redirect('impulso:ideia_create')

        # O limite é conferido no servidor, não só escondendo caixas na tela:
        # um POST direto passaria por cima do contador do formulário.
        participantes, erro = _participantes_da_ideia(request, request.user)
        if erro:
            messages.error(request, erro)
            return redirect('impulso:ideia_create')

        ideia = Ideia.objects.create(
            autor=request.user, descricao=descricao,
            setor_impacto=setor_impacto, motivo=motivo)

        if participantes:
            ideia.participantes.set(participantes)
            _notify(participantes, 'Você entrou em uma ideia',
                    f'{request.user.get_full_name() or request.user.email} incluiu você na ideia '
                    f'sobre "{ideia.setor_impacto}". Ela conta pontos para você também.',
                    '/impulso/inovar/')

        messages.success(
            request,
            'Ideia enviada. Obrigado por inovar!'
            + (f' {len(participantes)} pessoa(s) incluída(s).' if participantes else ''))
        return redirect('impulso:inovar_list')

    return render(request, 'impulso/ideia_form.html', {
        'active_tab': 'inovar',
        'candidatos': get_colaboradores().exclude(id=request.user.id),
        'escolhidos_ids': [],
        'max_participantes': Ideia.MAX_PARTICIPANTES,
    })


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

        participantes, erro = _participantes_da_ideia(request, ideia.autor)
        if erro:
            messages.error(request, erro)
            return redirect('impulso:ideia_edit', ideia_id=ideia.id)

        ideia.descricao = descricao
        ideia.setor_impacto = setor_impacto
        ideia.motivo = motivo
        ideia.save(update_fields=['descricao', 'setor_impacto', 'motivo', 'atualizado_em'])

        # Avisa só quem entrou agora: quem já estava não precisa de outro aviso.
        antes = set(ideia.participantes.values_list('id', flat=True))
        ideia.participantes.set(participantes)
        novos = [p for p in participantes if p.id not in antes]
        if novos:
            _notify(novos, 'Você entrou em uma ideia',
                    f'{ideia.autor.get_full_name() or ideia.autor.email} incluiu você na ideia '
                    f'sobre "{ideia.setor_impacto}". Ela conta pontos para você também.',
                    '/impulso/inovar/')

        messages.success(request, 'Ideia atualizada.')
        return redirect('impulso:inovar_list')

    return render(request, 'impulso/ideia_form.html', {
        'ideia': ideia,
        'active_tab': 'inovar',
        'candidatos': get_colaboradores().exclude(id=ideia.autor_id),
        'escolhidos_ids': list(ideia.participantes.values_list('id', flat=True)),
        'max_participantes': Ideia.MAX_PARTICIPANTES,
    })


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


@require_POST
@impulso_member_required
def meta_solicitacao_cancelar(request, meta_id):
    """Quem pediu a meta desiste dela, enquanto ainda está pendente."""
    meta = get_object_or_404(Meta, id=meta_id)

    if not meta.pode_cancelar_solicitacao(request.user):
        # Mensagem diferente para cada caso: "não pode" sem dizer por quê é o
        # tipo de resposta que faz a pessoa tentar de novo achando que é bug.
        if meta.solicitada_por_id != request.user.id:
            messages.error(request, 'Só quem fez a solicitação pode cancelá-la.')
        else:
            messages.error(request, 'Esta solicitação já foi decidida pelo gestor — '
                                    'fale com ele para remover a meta.')
        return redirect('impulso:meta_solicitacoes')

    titulo = meta.titulo
    gestor = meta.gestor
    meta.delete()

    if gestor:
        # O gestor tem essa solicitação na fila dele; sumir sem aviso o deixaria
        # procurando algo que não existe mais.
        _notify([gestor], 'Solicitação cancelada',
                f'{request.user.get_full_name() or request.user.email} cancelou o pedido '
                f'da meta "{titulo}".',
                '/impulso/metas/solicitacoes/')

    messages.success(request, f'Solicitação "{titulo}" cancelada.')
    return redirect('impulso:meta_solicitacoes')


@require_POST
@impulso_member_required
def meta_itens_reordenar(request, meta_id):
    """Grava a nova ordem do to-do.

    Recebe a lista completa de ids na ordem desejada, em vez de "sobe um" /
    "desce um": arrastar já produz a lista pronta, e gravar tudo de uma vez
    evita ficar com metade da ordem aplicada se algo falhar no meio.

    Ids de outra meta são descartados em silêncio — não é erro do usuário, é
    tentativa de mexer no que não é dele.
    """
    meta = get_object_or_404(Meta, id=meta_id)

    dono = (request.user.is_superuser
            or meta.gestor_id == request.user.id
            or meta.colaborador_id == request.user.id
            or meta.participantes.filter(id=request.user.id).exists())
    if not dono:
        return JsonResponse({'ok': False, 'erro': 'Você não pode reordenar este to-do.'},
                            status=403)

    try:
        pedidos = [int(x) for x in request.POST.getlist('ordem')]
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'erro': 'Ordem inválida.'}, status=400)

    itens = {i.id: i for i in MetaItem.objects.filter(meta=meta)}
    if not itens:
        return JsonResponse({'ok': True, 'itens': 0})

    # A ordem final é a pedida, seguida do que não veio na lista — assim nenhum
    # passo desaparece se a tela mandar uma lista incompleta.
    vistos, sequencia = set(), []
    for item_id in pedidos:
        if item_id in itens and item_id not in vistos:
            vistos.add(item_id)
            sequencia.append(itens[item_id])
    sequencia.extend(i for i in
                     sorted(itens.values(), key=lambda x: (x.ordem, x.id))
                     if i.id not in vistos)

    for posicao, item in enumerate(sequencia):
        item.ordem = posicao
    MetaItem.objects.bulk_update(sequencia, ['ordem'])

    return JsonResponse({'ok': True, 'itens': len(sequencia)})
