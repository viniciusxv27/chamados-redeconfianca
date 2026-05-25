from __future__ import annotations

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
from .services import change_status, fibras_for_user, sync_fibras


def _is_ilha(user) -> bool:
    """Quem pode mexer em status / chat reverso / fechar incidentes."""
    return user.is_superuser or getattr(user, 'hierarchy', '') in (
        'ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO',
    )


@login_required
def kanban(request):
    qs = fibras_for_user(request.user)
    # filtros simples
    pdv = (request.GET.get('pdv') or '').strip()
    vendedor = (request.GET.get('vendedor') or '').strip()
    if pdv:
        qs = qs.filter(pdv__icontains=pdv)
    if vendedor:
        qs = qs.filter(vendedor__icontains=vendedor)

    colunas = []
    for key, label in Fibra.STATUS_CHOICES:
        col_qs = qs.filter(status=key).order_by('-data_da_venda')
        colunas.append({
            'key': key,
            'label': label,
            'items': list(col_qs),
            'count': col_qs.count(),
            'total': col_qs.aggregate(t=Sum('valor'))['t'] or Decimal('0'),
        })

    return render(request, 'fibras/kanban.html', {
        'colunas': colunas,
        'is_ilha': _is_ilha(request.user),
        'filtro_pdv': pdv,
        'filtro_vendedor': vendedor,
        'status_choices': Fibra.STATUS_CHOICES,
    })


@login_required
def lista(request):
    """Visão em lista (tabela) alternativa ao kanban."""
    qs = fibras_for_user(request.user)
    pdv = (request.GET.get('pdv') or '').strip()
    vendedor = (request.GET.get('vendedor') or '').strip()
    status = (request.GET.get('status') or '').strip()
    if pdv:
        qs = qs.filter(pdv__icontains=pdv)
    if vendedor:
        qs = qs.filter(vendedor__icontains=vendedor)
    if status:
        qs = qs.filter(status=status)

    return render(request, 'fibras/lista.html', {
        'fibras': qs.order_by('-data_da_venda', '-id'),
        'is_ilha': _is_ilha(request.user),
        'filtro_pdv': pdv,
        'filtro_vendedor': vendedor,
        'filtro_status': status,
        'status_choices': Fibra.STATUS_CHOICES,
    })


@login_required
def detail(request, pk: int):
    fibra = get_object_or_404(Fibra, pk=pk)
    incidentes = fibra.incidentes.select_related('aberto_por').all()
    historico = fibra.status_history.select_related('alterado_por')[:50]
    return render(request, 'fibras/detail.html', {
        'fibra': fibra,
        'incidentes': incidentes,
        'historico': historico,
        'is_ilha': _is_ilha(request.user),
        'status_choices': Fibra.STATUS_CHOICES,
    })


@login_required
@require_POST
def change_status_view(request, pk: int):
    if not _is_ilha(request.user):
        messages.error(request, 'Apenas a Ilha pode alterar o status da fibra.')
        return redirect('fibras:detail', pk=pk)
    fibra = get_object_or_404(Fibra, pk=pk)
    new_status = (request.POST.get('status') or '').strip()
    retorno = (request.POST.get('retorno') or '').strip()
    try:
        change_status(fibra, new_status, changed_by=request.user, retorno=retorno)
        messages.success(request, 'Status atualizado.')
    except ValueError as e:
        messages.error(request, str(e))
    return redirect('fibras:detail', pk=pk)


@login_required
@require_POST
def change_status_ajax(request, pk: int):
    """Endpoint usado pelo drag&drop do Kanban — retorna JSON."""
    if not _is_ilha(request.user):
        return JsonResponse({'ok': False, 'error': 'permission'}, status=403)
    fibra = get_object_or_404(Fibra, pk=pk)
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
@require_POST
def abrir_incidente(request, pk: int):
    fibra = get_object_or_404(Fibra, pk=pk)
    observacao = (request.POST.get('observacao') or '').strip()
    if not observacao:
        messages.error(request, 'Descreva a observação do incidente.')
        return redirect('fibras:detail', pk=pk)
    FibraIncidente.objects.create(
        fibra=fibra, aberto_por=request.user, observacao=observacao,
    )
    messages.success(request, 'Incidente aberto para a Ilha.')
    return redirect('fibras:detail', pk=pk)


@login_required
def chat_view(request, pk: int):
    fibra = get_object_or_404(Fibra, pk=pk)
    chat, _ = FibraChat.objects.get_or_create(fibra=fibra)
    mensagens = chat.mensagens.select_related('autor').all()
    # marca mensagens não-suas como lidas
    chat.mensagens.filter(lida_em__isnull=True).exclude(autor=request.user).update(
        lida_em=timezone.now()
    )
    return render(request, 'fibras/chat.html', {
        'fibra': fibra, 'chat': chat, 'mensagens': mensagens,
    })


@login_required
@require_POST
def chat_post(request, pk: int):
    fibra = get_object_or_404(Fibra, pk=pk)
    chat, _ = FibraChat.objects.get_or_create(fibra=fibra)
    texto = (request.POST.get('texto') or '').strip()
    if texto:
        FibraChatMessage.objects.create(chat=chat, autor=request.user, texto=texto)
    return redirect('fibras:chat', pk=pk)


@login_required
@require_POST
def sync_now(request):
    if not _is_ilha(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('fibras:kanban')
    stats = sync_fibras()
    messages.success(
        request,
        f"Sincronização concluída: {stats['created']} novas, {stats['updated']} atualizadas "
        f"(de {stats['total_in_source']} no MySQL).",
    )
    return redirect('fibras:kanban')


@login_required
def relatorio(request):
    qs = fibras_for_user(request.user)
    total = qs.count() or 1  # evita divisão por zero
    receita_total = qs.aggregate(t=Sum('valor'))['t'] or Decimal('0')

    blocos = []
    for key, label in Fibra.STATUS_CHOICES:
        sub = qs.filter(status=key)
        cnt = sub.count()
        receita = sub.aggregate(t=Sum('valor'))['t'] or Decimal('0')
        blocos.append({
            'key': key,
            'label': label,
            'qtd': cnt,
            'receita': receita,
            'percent': round((cnt / total) * 100, 1),
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
    chart_colors = ['#3B82F6', '#F59E0B', '#EF4444', '#10B981', '#6B7280']

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
        'top_vendedores': top_vendedores,
        'chart_labels': chart_labels,
        'chart_qtd': chart_qtd,
        'chart_receita': chart_receita,
        'chart_colors': chart_colors,
    })


@login_required
def export_excel(request):
    """Exporta as fibras do mês corrente para um arquivo .xlsx."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    qs = fibras_for_user(request.user).order_by('-data_da_venda', '-id')

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

    filename = f'fibras_{timezone.now().strftime("%Y%m%d_%H%M")}.xlsx'
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response