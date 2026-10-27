from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Fibra, FibraIncidente, FibraChat, FibraChatMessage
from .permissoes import exige, fibras_visiveis, pode
from .services import change_status, import_planilha_xlsx, sync_fibras


# ---------------------------------------------------------------------------
# Filtro de período (data da venda): ?de=AAAA-MM-DD&ate=AAAA-MM-DD
# Vale para kanban, lista, relatório e exportações. Data inválida é ignorada.
# ---------------------------------------------------------------------------

def _data_param(valor):
    valor = (valor or '').strip()
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return None


def _periodo(request):
    """(de, ate) válidos do GET; se vierem invertidos, desinverte."""
    de = _data_param(request.GET.get('de'))
    ate = _data_param(request.GET.get('ate'))
    if de and ate and de > ate:
        de, ate = ate, de
    return de, ate


MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro',
         'novembro', 'dezembro']


def _fim_do_mes(dia):
    from calendar import monthrange
    return dia.replace(day=monthrange(dia.year, dia.month)[1])


def _periodo_ou_mes(request):
    """O kanban abre no mês corrente. Quem limpou as datas (de/ate vazios) ou pediu
    ?periodo=todos vê tudo."""
    de, ate = _periodo(request)
    if de or ate or 'de' in request.GET or 'ate' in request.GET or request.GET.get('periodo') == 'todos':
        return de, ate
    hoje = timezone.localdate()
    return hoje.replace(day=1), _fim_do_mes(hoje)


def _filtrar_periodo(qs, de, ate):
    if de:
        qs = qs.filter(data_da_venda__gte=de)
    if ate:
        qs = qs.filter(data_da_venda__lte=ate)
    return qs


def _ctx_periodo(request, de, ate):
    """Contexto do período para os templates: campos, chip e links que o preservam."""
    from urllib.parse import urlencode

    par = {}
    if de:
        par['de'] = de.isoformat()
    if ate:
        par['ate'] = ate.isoformat()
    if de and ate and de.day == 1 and ate == _fim_do_mes(de):
        rotulo = f"{MESES[de.month - 1]} de {de.year}"
    elif de and ate:
        rotulo = de.strftime('%d/%m/%Y') if de == ate else f"{de.strftime('%d/%m/%Y')} a {ate.strftime('%d/%m/%Y')}"
    elif de:
        rotulo = f"a partir de {de.strftime('%d/%m/%Y')}"
    elif ate:
        rotulo = f"até {ate.strftime('%d/%m/%Y')}"
    else:
        rotulo = ''
    # Link do "x" do chip: a mesma página com os demais filtros, sem o período.
    resto = request.GET.copy()
    resto.pop('de', None)
    resto.pop('ate', None)
    return {
        'filtro_de': par.get('de', ''),
        'filtro_ate': par.get('ate', ''),
        'periodo_ativo': bool(par),
        'periodo_rotulo': rotulo,
        'periodo_qs': urlencode(par),
        'sem_periodo_url': request.path + (f'?{resto.urlencode()}' if resto else ''),
    }


@login_required
@exige('modulo.ver')
def kanban(request):
    from django.db.models import Q as _Q
    from django.db.models.functions import Length
    qs = fibras_visiveis(request.user)
    # filtros simples
    pdv = (request.GET.get('pdv') or '').strip()
    vendedor = (request.GET.get('vendedor') or '').strip()
    venda = (request.GET.get('venda') or '').strip()
    ordem = (request.GET.get('ordem') or '').strip()
    # B2B: documento (CPF/CNPJ) com mais de 11 caracteres = empresa.
    b2b = (request.GET.get('b2b') or '').strip() in ('1', 'true', 'on', 'sim')
    if b2b:
        qs = qs.annotate(_doc_len=Length('cpf')).filter(_doc_len__gt=11)
    if pdv:
        qs = qs.filter(pdv__icontains=pdv)
    if vendedor:
        qs = qs.filter(vendedor__icontains=vendedor)
    if venda:
        qs = qs.filter(numero_da_venda__icontains=venda)
    if ordem:
        # Aceita o nº de ORDEM da planilha (== numero_protocolo) ou a posição
        # numérica (ordem_planilha). Tenta os dois para ser flexível.
        cond = _Q(numero_protocolo__icontains=ordem) | _Q(ordem_vinculada__icontains=ordem)
        if ordem.isdigit():
            cond |= _Q(ordem_planilha=int(ordem))
        qs = qs.filter(cond)
    # Período (data da venda) — vale para todas as colunas, inclusive "Não localizado".
    # Sem período na URL, abre no mês corrente.
    de, ate = _periodo_ou_mes(request)
    qs = _filtrar_periodo(qs, de, ate)

    # Coluna "Não localizado": vendas de fibra cujo protocolo não aparece na
    # planilha atual (last_planilha_at antes do último import OU nulo). Só
    # considera vendas FIXA não canceladas/instaladas para não poluir a tela.
    # IMPORTANTE: calculada ANTES das colunas por status para que possamos
    # excluir esses IDs das outras colunas e evitar duplicação no kanban.
    from django.db.models import Max, Q
    from .models import PlanilhaOrdemInconsistente
    latest_import = Fibra.objects.aggregate(m=Max('last_planilha_at'))['m']
    nao_qs = qs.filter(
        Q(pilar__iexact='fixa') | Q(pilar__icontains='fixa') | Q(pilar=''),
    ).exclude(status__in=[Fibra.STATUS_CANCELADO, Fibra.STATUS_INSTALADO])
    if latest_import:
        nao_qs = nao_qs.filter(
            Q(last_planilha_at__isnull=True) | Q(last_planilha_at__lt=latest_import)
        )
    else:
        nao_qs = nao_qs.filter(last_planilha_at__isnull=True)
    nao_qs = nao_qs.order_by('-data_da_venda', '-id')
    nao_items = list(nao_qs)
    nao_ids = {f.id for f in nao_items}

    colunas = []
    for key, label in Fibra.STATUS_CHOICES:
        # Ordena pela posição na planilha (quando disponível) e depois pela data.
        # Exclui fibras que estão na coluna "Não localizado" para não duplicar.
        col_qs = (
            qs.filter(status=key)
              .exclude(id__in=nao_ids)
              .order_by('ordem_planilha', '-data_da_venda')
        )
        colunas.append({
            'key': key,
            'label': label,
            'items': list(col_qs),
            'count': col_qs.count(),
            'total': col_qs.aggregate(t=Sum('valor'))['t'] or Decimal('0'),
        })

    colunas.append({
        'key': 'nao_localizado',
        'label': 'Não localizado',
        'items': nao_items,
        'count': len(nao_items),
        'total': nao_qs.aggregate(t=Sum('valor'))['t'] or Decimal('0'),
    })

    inconsistencias = list(
        PlanilhaOrdemInconsistente.objects.order_by('-last_seen_at')[:200]
    )

    ctx_periodo = _ctx_periodo(request, de, ate)
    if ctx_periodo['periodo_ativo']:
        # O "x" do período mostra tudo (sem ele, o kanban voltaria para o mês).
        resto = request.GET.copy()
        resto.pop('de', None)
        resto.pop('ate', None)
        resto['periodo'] = 'todos'
        ctx_periodo['sem_periodo_url'] = f'{request.path}?{resto.urlencode()}'

    # O botão "Cruzar" usa o período do quadro; sem período (ou com uma ponta só), fecha no mês.
    hoje = timezone.localdate()
    cruzar_ini = de or (ate or hoje).replace(day=1)
    cruzar_fim = ate or (hoje if cruzar_ini <= hoje <= _fim_do_mes(cruzar_ini) else _fim_do_mes(cruzar_ini))

    return render(request, 'fibras/kanban.html', {
        'cruzar_ini': cruzar_ini,
        'cruzar_fim': cruzar_fim,
        'validacao': _validacao_do_quadro(qs, de, ate, pode(request.user, 'validacao.usar')),
        'colunas': colunas,
        'filtro_pdv': pdv,
        'filtro_vendedor': vendedor,
        'filtro_venda': venda,
        'filtro_ordem': ordem,
        'filtro_b2b': b2b,
        'status_choices': Fibra.STATUS_CHOICES,
        'inconsistencias': inconsistencias,
        'inconsistencias_count': len(inconsistencias),
        **ctx_periodo,
    })


def _validacao_do_quadro(qs, de, ate, is_ilha):
    """Os indicadores da /fibras/validacao contados nas vendas do quadro (mesmo período e recorte)."""
    from django.db.models import Q

    from . import cruzamento as cz
    from .models import EsteiraVenda
    from .validacao import ultima_importacao, ultimo_cruzamento

    k = qs.aggregate(
        pedidos=Count('id'),
        alta=Count('id', filter=Q(assertividade=cz.ALTA)),
        media=Count('id', filter=Q(assertividade=cz.MEDIA)),
        muito_baixa=Count('id', filter=Q(assertividade=cz.MUITO_BAIXA)),
        manual=Count('id', filter=Q(assertividade=cz.MANUAL)),
        nao_localizada=Count('id', filter=Q(status_cruzamento=cz.NAO_LOCALIZADA)),
        b2b=Count('id', filter=Q(status_cruzamento=cz.B2B)),
        end_diverge=Count('id', filter=Q(conferencia_endereco__in=[cz.END_DIVERGE, cz.END_PARCIAL])),
        sem_cruzamento=Count('id', filter=Q(status_cruzamento='')),
    )
    imp = ultima_importacao()
    nao_lancada = None
    if is_ilha and imp:
        esteira = EsteiraVenda.objects.filter(importacao=imp, no_escopo=True, fibra__isnull=True)
        if de:
            esteira = esteira.filter(data_criacao__gte=de)
        if ate:
            esteira = esteira.filter(data_criacao__lte=ate)
        nao_lancada = esteira.count()
    cards = [
        ('alta', 'Alta', 'fa-circle-check', 'k-alta', 'assert', cz.ALTA, 'protocolo exato'),
        ('media', 'Média', 'fa-circle-half-stroke', 'k-media', 'assert', cz.MEDIA, 'protocolo aproximado'),
        ('muito_baixa', 'Muito baixa', 'fa-circle-exclamation', 'k-baixa', 'assert', cz.MUITO_BAIXA,
         'loja/data divergente ou só loja e dia'),
        ('manual', 'Manual', 'fa-hand-pointer', 'k-manual', 'assert', cz.MANUAL, 'vínculos confirmados'),
        ('nao_localizada', 'Não localizadas', 'fa-circle-xmark', 'k-nl', 'cruz', cz.NAO_LOCALIZADA,
         'sem par na Esteira Vivo'),
        ('b2b', 'Vendas B2B', 'fa-building', 'k-b2b', 'cruz', cz.B2B, 'CNPJ + protocolo alfanumérico'),
        ('end_diverge', 'Endereço divergente', 'fa-map-location-dot', 'k-media', 'end', 'div',
         'endereço da venda × Esteira'),
        ('sem_cruzamento', 'Sem cruzamento', 'fa-hourglass', '', 'cruz', '', 'ainda não validadas'),
    ]
    return {
        'k': k,
        'nao_lancada': nao_lancada,
        'importacao': imp,
        'cruzamento': ultimo_cruzamento(),
        'cards': [{'chave': c, 'rotulo': r, 'icone': i, 'classe': cl, 'tipo': t, 'valor': v, 'detalhe': d, 'n': k[c]}
                  for c, r, i, cl, t, v, d in cards],
    }


@login_required
@exige('modulo.ver')
def lista(request):
    """Visão em lista (tabela) alternativa ao kanban."""
    from django.db.models import Q as _Q
    qs = fibras_visiveis(request.user)
    pdv = (request.GET.get('pdv') or '').strip()
    vendedor = (request.GET.get('vendedor') or '').strip()
    status = (request.GET.get('status') or '').strip()
    venda = (request.GET.get('venda') or '').strip()
    ordem = (request.GET.get('ordem') or '').strip()
    if pdv:
        qs = qs.filter(pdv__icontains=pdv)
    if vendedor:
        qs = qs.filter(vendedor__icontains=vendedor)
    if status:
        qs = qs.filter(status=status)
    if venda:
        qs = qs.filter(numero_da_venda__icontains=venda)
    if ordem:
        cond = _Q(numero_protocolo__icontains=ordem)
        if ordem.isdigit():
            cond |= _Q(ordem_planilha=int(ordem))
        qs = qs.filter(cond)
    de, ate = _periodo(request)
    qs = _filtrar_periodo(qs, de, ate)

    return render(request, 'fibras/lista.html', {
        'fibras': qs.order_by('ordem_planilha', '-data_da_venda', '-id'),
        'filtro_pdv': pdv,
        'filtro_vendedor': vendedor,
        'filtro_status': status,
        'filtro_venda': venda,
        'filtro_ordem': ordem,
        'status_choices': Fibra.STATUS_CHOICES,
        **_ctx_periodo(request, de, ate),
    })


@login_required
@exige('modulo.ver')
def detail(request, pk: int):
    fibra = get_object_or_404(fibras_visiveis(request.user), pk=pk)
    incidentes = fibra.incidentes.select_related('aberto_por').all()
    historico = fibra.status_history.select_related('alterado_por')[:50]
    return render(request, 'fibras/detail.html', {
        'fibra': fibra,
        'incidentes': incidentes,
        'historico': historico,
        'status_choices': Fibra.STATUS_CHOICES,
    })


@login_required
@exige('status.alterar')
@require_POST
def change_status_view(request, pk: int):
    fibra = get_object_or_404(fibras_visiveis(request.user), pk=pk)
    new_status = (request.POST.get('status') or '').strip()
    retorno = (request.POST.get('retorno') or '').strip()
    try:
        change_status(fibra, new_status, changed_by=request.user, retorno=retorno)
        messages.success(request, 'Status atualizado.')
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('fibras:detail', pk=pk)


@login_required
@exige('status.alterar')
@require_POST
def change_status_ajax(request, pk: int):
    """Endpoint usado pelo drag&drop do Kanban — retorna JSON.

    Mantido para compatibilidade, porém o status passa a ser ditado pela
    planilha diária: por isso só a Ilha (Supervisor/Admin) pode mudar manualmente.
    """
    fibra = get_object_or_404(fibras_visiveis(request.user), pk=pk)
    new_status = (request.POST.get('status') or '').strip()
    try:
        change_status(fibra, new_status, changed_by=request.user, retorno='')
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    return JsonResponse({
        'ok': True,
        'pk': fibra.pk,
        'status': fibra.status,
        'status_label': fibra.get_status_display(),
    })


@login_required
@exige('planilha.importar')
@require_POST
def importar_planilha(request):
    """Upload manual da planilha diária (.xlsx). Atualiza o Kanban/Lista."""
    from .validacao import ErroValidacao, checar_arquivo
    file_obj = request.FILES.get('planilha')
    voltar = request.POST.get('voltar') or ''
    destino = voltar if voltar.startswith('/fibras/') else reverse('fibras:kanban')
    try:
        checar_arquivo(file_obj)
        stats = import_planilha_xlsx(file_obj, by_user=request.user)
    except ErroValidacao as e:
        messages.error(request, str(e))
        return redirect(destino)
    except Exception as e:
        messages.error(request, f'Falha ao importar planilha: {e}')
        return redirect(destino)
    messages.success(
        request,
        f"{stats.get('formato') or 'Esteira'} importada ({stats['rows']} ORDEM(s)) e cruzada com a Base de Vendas: "
        f"{stats['matched']} localizadas, {stats['nao_localizadas']} não localizadas, "
        f"{stats['not_found_count']} vendas não lançadas, "
        f"{stats['updated_status']} com status atualizado. "
        f"Removidas (sumiram da planilha e sem tratativa): {stats.get('removidas', 0)}."
    )
    return redirect(destino)


@login_required
@exige('tratativa.abrir')
@require_POST
def abrir_incidente(request, pk: int):
    fibra = get_object_or_404(fibras_visiveis(request.user), pk=pk)
    observacao = (request.POST.get('observacao') or '').strip()
    if not observacao:
        messages.error(request, 'Descreva a observação do incidente.')
        return redirect('fibras:detail', pk=pk)

    incidente = FibraIncidente.objects.create(
        fibra=fibra, aberto_por=request.user, observacao=observacao,
    )

    # Cria/vincula um SupportChat na categoria "SUPORTE FIXA" para que a
    # tratativa apareça também em /projects/support/admin/template/ e o chat
    # seja único entre vendedor/gerente e a atendente do suporte.
    try:
        from projects.models_chat import SupportCategory, SupportChat, SupportChatMessage

        sector = getattr(request.user, 'sector', None)
        category = SupportCategory.objects.filter(name__iexact='SUPORTE FIXA').first()
        if not category and sector:
            category = SupportCategory.objects.create(
                name='SUPORTE FIXA',
                sector=sector,
                description='Tratativas de incidentes de Fibra (módulo /fibras/).',
            )
        if category:
            chat = SupportChat.objects.create(
                user=request.user,
                category=category,
                sector=category.sector or sector,
                title=f"Fibra {fibra.numero_da_venda} — {fibra.cliente or 'cliente'}",
                priority='ALTA',
                status='ABERTO',
            )
            SupportChatMessage.objects.create(
                chat=chat, user=request.user, message=observacao,
            )
            incidente.support_chat = chat
            incidente.save(update_fields=['support_chat'])
    except Exception:
        # Não bloqueia o fluxo se o módulo de suporte estiver indisponível.
        pass

    messages.success(request, 'Tratativa aberta para a Ilha (visível também em Suporte → SUPORTE FIXA).')
    return redirect('fibras:detail', pk=pk)


@login_required
@exige('tratativa.abrir')
def chat_view(request, pk: int):
    """Chat da tratativa. Se houver SupportChat vinculado, usa-o como fonte única."""
    fibra = get_object_or_404(fibras_visiveis(request.user), pk=pk)
    incidente = fibra.incidentes.filter(support_chat__isnull=False).order_by('-aberto_em').first()

    if incidente and incidente.support_chat_id:
        support_chat = incidente.support_chat
        mensagens_support = support_chat.messages.select_related('user').all()
        # Marca como visualizado para o autor (some o badge de "resposta nova").
        if incidente.aberto_por_id == request.user.id:
            incidente.last_opener_view_at = timezone.now()
            incidente.save(update_fields=['last_opener_view_at'])
        return render(request, 'fibras/chat.html', {
            'fibra': fibra,
            'support_chat': support_chat,
            'mensagens_support': mensagens_support,
            'incidente': incidente,
        })

    # Fallback: chat legado (FibraChat).
    chat, _ = FibraChat.objects.get_or_create(fibra=fibra)
    mensagens = chat.mensagens.select_related('autor').all()
    chat.mensagens.filter(lida_em__isnull=True).exclude(autor=request.user).update(
        lida_em=timezone.now()
    )
    return render(request, 'fibras/chat.html', {
        'fibra': fibra, 'chat': chat, 'mensagens': mensagens,
    })


@login_required
@exige('tratativa.abrir')
@require_POST
def chat_post(request, pk: int):
    fibra = get_object_or_404(fibras_visiveis(request.user), pk=pk)
    texto = (request.POST.get('texto') or '').strip()
    if not texto:
        return redirect('fibras:chat', pk=pk)

    # Se houver incidente com SupportChat, escreve lá (chat unificado).
    incidente = fibra.incidentes.filter(support_chat__isnull=False).order_by('-aberto_em').first()
    if incidente and incidente.support_chat_id:
        try:
            from projects.models_chat import SupportChatMessage
            SupportChatMessage.objects.create(
                chat=incidente.support_chat, user=request.user, message=texto,
            )
            return redirect('fibras:chat', pk=pk)
        except Exception:
            pass

    chat, _ = FibraChat.objects.get_or_create(fibra=fibra)
    FibraChatMessage.objects.create(chat=chat, autor=request.user, texto=texto)
    return redirect('fibras:chat', pk=pk)


@login_required
@exige('validacao.usar')
@require_POST
def sync_now(request):
    stats = sync_fibras()
    messages.success(
        request,
        f"Sincronização concluída: {stats['created']} novas, {stats['updated']} atualizadas "
        f"(de {stats['total_in_source']} no MySQL).",
    )
    return redirect('fibras:kanban')


@login_required
@exige('modulo.ver')
def relatorio(request):
    de, ate = _periodo(request)
    qs = _filtrar_periodo(fibras_visiveis(request.user), de, ate)
    total = qs.count() or 1  # evita divisão por zero
    receita_total = qs.aggregate(t=Sum('valor'))['t'] or Decimal('0')

    # Bloco "Não localizada": vendas FIXA (não canceladas/instaladas) cujo
    # protocolo não bateu com nenhuma ORDEM da planilha mais recente.
    # Calculado ANTES dos blocos por status para podermos excluir esses IDs
    # das outras categorias e evitar dupla contagem.
    from django.db.models import Max, Q
    latest_import = Fibra.objects.aggregate(m=Max('last_planilha_at'))['m']
    nao_qs = qs.filter(
        Q(pilar__iexact='fixa') | Q(pilar__icontains='fixa') | Q(pilar=''),
    ).exclude(status__in=[Fibra.STATUS_CANCELADO, Fibra.STATUS_INSTALADO])
    if latest_import:
        nao_qs = nao_qs.filter(
            Q(last_planilha_at__isnull=True) | Q(last_planilha_at__lt=latest_import)
        )
    else:
        nao_qs = nao_qs.filter(last_planilha_at__isnull=True)
    qtd_nao_loc = nao_qs.count()
    receita_nao_loc = nao_qs.aggregate(t=Sum('valor'))['t'] or Decimal('0')
    nao_ids = set(nao_qs.values_list('id', flat=True))

    # Detalhe "Não localizado": uma linha por número da venda (numero_da_venda
    # é único no modelo), com PDV e Vendedor para o card do relatório.
    nao_loc_detalhes = list(
        nao_qs.order_by('-data_da_venda', '-id')
        .values('numero_da_venda', 'pdv', 'vendedor')
    )

    blocos = []
    for key, label in Fibra.STATUS_CHOICES:
        sub = qs.filter(status=key).exclude(id__in=nao_ids)
        cnt = sub.count()
        receita = sub.aggregate(t=Sum('valor'))['t'] or Decimal('0')
        blocos.append({
            'key': key,
            'label': label,
            'qtd': cnt,
            'receita': receita,
            'percent': round((cnt / total) * 100, 1),
        })

    blocos.append({
        'key': 'nao_localizado',
        'label': 'Não localizada',
        'qtd': qtd_nao_loc,
        'receita': receita_nao_loc,
        'percent': round((qtd_nao_loc / total) * 100, 1),
    })

    # principal motivo de cancelamento (texto do retorno_myrella mais frequente)
    motivos = (
        qs.filter(status=Fibra.STATUS_CANCELADO)
        .exclude(retorno_myrella='')
        .values('retorno_myrella')
        .annotate(n=Count('id'))
        .order_by('-n')[:5]
    )

    # KPIs principais
    qtd_cancel = qs.filter(status=Fibra.STATUS_CANCELADO).count()
    qtd_install = qs.filter(status=Fibra.STATUS_INSTALADO).count()
    receita_cancel = qs.filter(status=Fibra.STATUS_CANCELADO).aggregate(
        t=Sum('valor'))['t'] or Decimal('0')
    receita_install = qs.filter(status=Fibra.STATUS_INSTALADO).aggregate(
        t=Sum('valor'))['t'] or Decimal('0')
    real_total = qs.count() or 1

    # Top 5 vendedores por receita
    top_vendedores = list(
        qs.exclude(vendedor='')
        .values('vendedor')
        .annotate(qtd=Count('id'), receita=Sum('valor'))
        .order_by('-receita')[:5]
    )

    # Dataset para os gráficos (Chart.js) — serializado em JSON no template.
    chart_labels = [b['label'] for b in blocos]
    chart_qtd = [b['qtd'] for b in blocos]
    chart_receita = [float(b['receita']) for b in blocos]
    chart_colors = ['#3B82F6', '#F59E0B', '#EF4444', '#10B981', '#6B7280', '#A855F7']

    return render(request, 'fibras/relatorio.html', {
        'blocos': blocos,
        'total': qs.count(),
        'receita_total': receita_total,
        'motivos': motivos,
        'qtd_cancel': qtd_cancel,
        'qtd_install': qtd_install,
        'receita_cancel': receita_cancel,
        'receita_install': receita_install,
        'percent_cancel': round((qtd_cancel / real_total) * 100, 1),
        'percent_install': round((qtd_install / real_total) * 100, 1),
        'qtd_nao_loc': qtd_nao_loc,
        'receita_nao_loc': receita_nao_loc,
        'percent_nao_loc': round((qtd_nao_loc / real_total) * 100, 1),
        'nao_loc_detalhes': nao_loc_detalhes,
        'top_vendedores': top_vendedores,
        'chart_labels': chart_labels,
        'chart_qtd': chart_qtd,
        'chart_receita': chart_receita,
        'chart_colors': chart_colors,
        **_ctx_periodo(request, de, ate),
    })


@login_required
@exige('exportar')
def export_excel(request):
    """Exporta as fibras do mês corrente para um arquivo .xlsx.

    Aceita ``?status=<status>`` para exportar apenas um status específico
    (ex.: ``cancelado`` para exportar todas as fibras canceladas).
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    de, ate = _periodo(request)
    qs = _filtrar_periodo(fibras_visiveis(request.user), de, ate).order_by('-data_da_venda', '-id')

    status = (request.GET.get('status') or '').strip()
    status_valido = dict(Fibra.STATUS_CHOICES)
    nome_arquivo = 'fibras'
    if status in status_valido:
        qs = qs.filter(status=status)
        nome_arquivo = f'fibras_{status}'

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Fibras'

    headers = [
        'Nº Venda', 'CPF', 'Cliente', 'Endereço', 'Nº Acesso', 'Plano',
        'Valor', 'PDV', 'Vendedor', 'Data da Venda', 'Pilar',
        'Serviço Técnico', 'Status', 'Retorno Myrella',
        'Primeira sincronização', 'Última sincronização',
    ]
    ws.append(headers)

    header_fill = PatternFill('solid', fgColor='FF6B35')
    header_font = Font(bold=True, color='FFFFFF', name='Calibri', size=11)
    for col_idx, _ in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')

    for f in qs:
        ws.append([
            f.numero_da_venda, f.cpf, f.cliente, f.endereco, f.numero_acesso,
            f.plano, float(f.valor or 0), f.pdv, f.vendedor,
            f.data_da_venda.strftime('%d/%m/%Y') if f.data_da_venda else '',
            f.pilar, f.servico_tecnico, f.get_status_display(),
            f.retorno_myrella,
            timezone.localtime(f.first_seen_at).strftime('%d/%m/%Y %H:%M') if f.first_seen_at else '',
            timezone.localtime(f.last_synced_at).strftime('%d/%m/%Y %H:%M') if f.last_synced_at else '',
        ])

    widths = [16, 16, 28, 36, 16, 28, 12, 22, 24, 14, 14, 18, 16, 40, 20, 20]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'

    filename = f'{nome_arquivo}_{timezone.now().strftime("%Y%m%d_%H%M")}.xlsx'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


@login_required
@exige('exportar')
def export_canceladas_csv(request):
    """Exporta as fibras *canceladas* no formato da planilha de Gestão
    Documental (a mesma usada em /validad1), com o status atualizado e a
    Receita zerada (cancelamento não gera receita)."""
    import csv
    from validad1.views import GESTAO_DOCUMENTAL_HEADER
    from validad1.models import format_numero_acesso

    de, ate = _periodo(request)
    qs = (
        _filtrar_periodo(fibras_visiveis(request.user), de, ate)
        .filter(status=Fibra.STATUS_CANCELADO)
        .order_by('-data_da_venda', '-id')
    )

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    filename = f'fibras_canceladas_{timezone.now().strftime("%Y%m%d_%H%M")}.csv'
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response.write('\ufeff')  # BOM para abrir corretamente no Excel

    writer = csv.writer(response, delimiter=';')
    writer.writerow(GESTAO_DOCUMENTAL_HEADER)

    def _money(value):
        # Mantém o formato da planilha modelo: decimais com vírgula (ex.: 59,90).
        return f'{float(value or 0):.2f}'.replace('.', ',')

    for f in qs:
        status_label = f.get_status_display()                  # "Cancelado"
        motivo_cancelamento = (f.motivo_planilha or '').replace('\n', ' ').strip()
        data_venda = f.data_da_venda.strftime('%d/%m/%Y') if f.data_da_venda else ''
        row = [
            data_venda,                          # Data Venda
            '',                                  # Data Ativação
            status_label,                        # Status BKO
            f.servico_tecnico,                   # Serviço
            _money(f.valor),                     # Valor Plano
            '',                                  # Valor Plano Antigo
            '',                                  # Vencimento Fatura
            format_numero_acesso(f.numero_acesso),  # Nº Acesso
            status_label,                        # Situação do serviço
            f.numero_protocolo,                  # Nº Protocolo GED
            '',                                  # Motivo Reprovação
            motivo_cancelamento,                 # Motivo Cancelamento
            '',                                  # Comissão de Aparelho do Vendedor
            '',                                  # Data Última Ativação
            '',                                  # Status Primeira Fatura
            '',                                  # Data Primeira Fatura
            '',                                  # Status Segunda Fatura
            '',                                  # Data Segunda Fatura
            '',                                  # Status Terceira Fatura
            '',                                  # Data Terceira Fatura
        ]
        writer.writerow(row)

    return response


# ---------------------------------------------------------------------------
# Validação: Base de Vendas x Esteira de Vendas Vivo (acompanhamento completo)
# ---------------------------------------------------------------------------

def _validacao_api(fn):
    """Só a Ilha; erros viram JSON {erro} para a tela mostrar."""
    import functools
    import json
    import logging

    from .validacao import ErroValidacao

    @functools.wraps(fn)
    @login_required
    def envolto(request, *args, **kwargs):
        if not pode(request.user, 'validacao.usar'):
            return JsonResponse({'erro': 'Sem permissão para a validação de fibras.'}, status=403)
        try:
            corpo = {}
            if request.method == 'POST' and request.content_type == 'application/json':
                corpo = json.loads(request.body or b'{}')
            return fn(request, corpo, *args, **kwargs)
        except ErroValidacao as e:
            return JsonResponse({'erro': str(e)}, status=400)
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).exception('Erro na validação de fibras')
            return JsonResponse({'erro': f'{type(e).__name__}: {e}'}, status=500)
    return envolto


@login_required
@exige('validacao.usar')
def validacao(request):
    return render(request, 'fibras/validacao.html', {})


@_validacao_api
def validacao_estado(request, corpo):
    from .validacao import estado_json
    return JsonResponse(estado_json())


@require_POST
@_validacao_api
def validacao_cruzar(request, corpo):
    from datetime import date as _date, timedelta as _td

    from .services import sync_fibras_periodo
    from .validacao import _param_limpo, executar, resultado_json
    # A Base de Vendas do período tem de estar no portal antes de cruzar: mês
    # que não foi sincronizado vira "venda não lançada" na Esteira inteira.
    p = _param_limpo(corpo)
    try:
        sync_fibras_periodo(_date.fromisoformat(p['ini']) - _td(days=1), _date.fromisoformat(p['fim']) + _td(days=1))
    except Exception:  # noqa: BLE001 — Vivo GO fora do ar: cruza com o que já está no portal
        pass
    cruz = executar(corpo, by_user=request.user, origem='manual')
    return JsonResponse(resultado_json(cruz))


@_validacao_api
def validacao_pedido(request, corpo):
    from .validacao import ErroValidacao, detalhe_json
    fibra = Fibra.objects.filter(pk=request.GET.get('pid') or 0).first()
    if not fibra:
        raise ErroValidacao('Pedido não encontrado.')
    return JsonResponse(detalhe_json(fibra))


@require_POST
@_validacao_api
def validacao_ajuste(request, corpo):
    from .validacao import (ErroValidacao, aplicar_ajuste, executar, parametros_atuais, resultado_json,
                            ultimo_cruzamento)
    fibra = Fibra.objects.filter(pk=corpo.get('pid') or 0).first()
    if not fibra:
        raise ErroValidacao('Pedido não encontrado.')
    acao = corpo.get('acao') or ''
    if acao not in ('confirmar', 'rejeitar', 'vincular', 'desfazer', 'comentar'):
        raise ErroValidacao('Ação inválida.')
    aplicar_ajuste(fibra, acao, corpo.get('ordem') or '', corpo.get('comentario') if 'comentario' in corpo else None)
    # Comentário não muda o pareamento; o resto refaz o cruzamento com os mesmos parâmetros.
    if acao == 'comentar' and ultimo_cruzamento():
        cruz = ultimo_cruzamento()
    else:
        cruz = executar(parametros_atuais(), by_user=request.user, origem='ajuste')
    return JsonResponse(resultado_json(cruz))


@require_POST
@_validacao_api
def validacao_ajustes_limpar(request, corpo):
    from .validacao import estado_json, executar, limpar_ajustes, parametros_atuais, ultimo_cruzamento
    limpar_ajustes()
    if ultimo_cruzamento():
        executar(parametros_atuais(), by_user=request.user, origem='ajuste')
    return JsonResponse(estado_json())


@require_POST
@_validacao_api
def validacao_lojas(request, corpo):
    from .validacao import estado_json, executar, parametros_atuais, salvar_lojas, ultimo_cruzamento
    mapa = corpo.get('mapa') or {}
    if not isinstance(mapa, dict):
        mapa = {}
    salvar_lojas(mapa, by_user=request.user)
    if ultimo_cruzamento():
        p = parametros_atuais()
        # Lojas que passaram a ter FILIAL entram no escopo.
        p['lojas'] = sorted(set(p['lojas']) | {str(a) for a in mapa.values() if a})
        executar(p, by_user=request.user, origem='lojas')
    return JsonResponse(estado_json())


@require_POST
@_validacao_api
def validacao_importar(request, corpo):
    from .validacao import checar_arquivo, estado_json
    arquivo = request.FILES.get('arquivo')
    checar_arquivo(arquivo)
    stats = import_planilha_xlsx(arquivo, by_user=request.user)
    estado = estado_json()
    removidas = f"; {stats['removidas']} fibras antigas removidas" if stats.get('removidas') else ''
    estado['msg'] = (f"{arquivo.name} ({stats.get('formato') or 'Esteira'}): {stats['rows']} ORDEM(s). "
                     f"Cruzamento feito: {stats['matched']} localizadas, "
                     f"{stats['nao_localizadas']} não localizadas, {stats['not_found_count']} não lançadas"
                     f"{removidas}.")
    return JsonResponse(estado)


@require_POST
@_validacao_api
def validacao_sincronizar(request, corpo):
    from datetime import date as _date, timedelta as _td

    from .services import sync_fibras_periodo
    from .validacao import estado_json, parametros_atuais
    p = parametros_atuais()
    stats = sync_fibras_periodo(_date.fromisoformat(p['ini']) - _td(days=1), _date.fromisoformat(p['fim']) + _td(days=1))
    estado = estado_json()
    estado['msg'] = (f"Base de Vendas sincronizada ({stats['meses']} mês(es) do período): {stats['created']} novas, "
                     f"{stats['updated']} atualizadas (de {stats['total_in_source']} no Vivo GO).")
    return JsonResponse(estado)


@_validacao_api
def validacao_previa(request, corpo):
    from .validacao import previa
    return JsonResponse(previa(request.GET.get('tipo') or ''))


@_validacao_api
def validacao_baixar(request, corpo):
    from urllib.parse import quote

    from .validacao import arquivo
    nome, dados = arquivo(request.GET.get('o') or 'resultado')
    resposta = HttpResponse(dados, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resposta['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(nome)
    return resposta
