"""O cartão do Impulso da home, pedido pela página depois que ela carrega (ver impulso/resumo_home.py)."""
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from .utils import is_impulso_member

logger = logging.getLogger(__name__)


@login_required
def resumo_home(request):
    if not is_impulso_member(request.user):
        return HttpResponse(status=204)
    try:
        from .resumo_home import contexto_do_cartao
        cartao = contexto_do_cartao(request.user)
    except Exception:  # noqa: BLE001 — a home segue sem o cartão
        logger.exception('Impulso: cartão da home não montado para %s', request.user.pk)
        return HttpResponse(status=204)
    return render(request, 'impulso/_home_card.html', {'cartao': cartao})
