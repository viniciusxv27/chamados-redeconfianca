"""Aba Orçamentos: o vendedor preenche e o cliente recebe no WhatsApp."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import orcamentos as svc
from .models import Orcamento
from .permissions import exige, pode, ve_rede


def _visiveis(user):
    qs = Orcamento.objects.select_related('loja')
    return qs if ve_rede(user) else qs.filter(vendedor=user)


@login_required
@exige('vendas.lancar', 'rede.ver')
def orcamentos(request):
    user = request.user
    pode_enviar = pode(user, 'vendas.lancar')
    preenchido = {}
    erros = []
    if request.method == 'POST':
        if not pode_enviar:
            messages.error(request, 'Enviar orçamento não está liberado para o seu usuário.')
            return redirect('vendas:orcamentos')
        campos, erros = svc.ler_campos(request.POST)
        if not erros and svc.ja_enviado(user, campos):
            messages.info(request, 'Este orçamento acabou de ser enviado para este número. Não mandei de novo.')
            return redirect('vendas:orcamentos')
        if not erros:
            orcamento = svc.criar(user, campos)
            if svc.enviar(orcamento):
                messages.success(request, f'Orçamento enviado para {orcamento.cliente_nome} no WhatsApp {orcamento.numero_formatado}.')
            else:
                messages.error(request, f'Não consegui enviar para {orcamento.cliente_nome}: {orcamento.erro} O orçamento ficou na lista para tentar de novo.')
            return redirect('vendas:orcamentos')
        preenchido = {c: request.POST.get(c, '') for c in ('cliente_nome', 'pilar', 'numero', 'texto')}

    q = (request.GET.get('q') or '').strip()
    lista = _visiveis(user)
    if q:
        digitos = ''.join(c for c in q if c.isdigit())
        filtro = Q(cliente_nome__icontains=q) | Q(texto__icontains=q) | Q(vendedor_nome__icontains=q)
        lista = lista.filter(filtro | Q(numero__contains=digitos) if digitos else filtro)
    return render(request, 'vendas/orcamentos.html', {
        'aba': 'orcamentos', 'pode_enviar': pode_enviar, 'pilares': Orcamento.PILARES, 'q': q,
        'pagina': Paginator(lista, 25).get_page(request.GET.get('pagina')),
        'modelo': svc.modelo_do_vendedor(user), 'limite': svc.LIMITE_TEXTO, 'preenchido': preenchido, 'erros': erros,
    })


@login_required
@exige('vendas.lancar')
@require_POST
def orcamento_reenviar(request, pk):
    """Tenta de novo só o que não saiu, e só quem mandou (ou quem vê a rede)."""
    orcamento = get_object_or_404(_visiveis(request.user), pk=pk)
    if orcamento.status != 'FALHA':
        messages.info(request, 'Este orçamento já foi enviado.')
    elif svc.enviar(orcamento):
        messages.success(request, f'Orçamento enviado para {orcamento.cliente_nome}.')
    else:
        messages.error(request, f'Ainda não consegui enviar: {orcamento.erro}')
    return redirect('vendas:orcamentos')
