from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import HttpResponseRedirect
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

    return render(request, 'fibras/relatorio.html', {
        'blocos': blocos,
        'total': qs.count(),
        'receita_total': receita_total,
        'motivos': motivos,
    })
