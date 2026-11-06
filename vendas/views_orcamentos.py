"""Aba Orçamentos: o vendedor preenche e abre a conversa com o cliente no WhatsApp Web dele."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
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
        # A tela manda por fetch e abre a aba do WhatsApp com o link que volta daqui;
        # sem JavaScript, o post comum grava e a lista oferece "Abrir no WhatsApp".
        por_fetch = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        if not pode_enviar:
            if por_fetch:
                return JsonResponse({'ok': False, 'erros': ['Enviar orçamento não está liberado para o seu usuário.']}, status=403)
            messages.error(request, 'Enviar orçamento não está liberado para o seu usuário.')
            return redirect('vendas:orcamentos')
        campos, erros = svc.ler_campos(request.POST)
        if not erros:
            # Clique duplo: reabre a mesma conversa em vez de gravar outro orçamento igual.
            orcamento = svc.ja_enviado(user, campos) or svc.criar(user, campos)
            if por_fetch:
                return JsonResponse({'ok': True, 'id': orcamento.pk, 'web': orcamento.link_whatsapp_web,
                                     'app': orcamento.link_whatsapp_app})
            messages.success(request, f'Orçamento de {orcamento.cliente_nome} salvo. Use "Abrir no WhatsApp" na lista para mandar.')
            return redirect('vendas:orcamentos')
        if por_fetch:
            return JsonResponse({'ok': False, 'erros': erros}, status=400)
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
    """Não manda mais pelo número do portal (06/11/2026): leva à conversa no WhatsApp Web."""
    orcamento = get_object_or_404(_visiveis(request.user), pk=pk)
    return redirect(orcamento.link_whatsapp_web)
