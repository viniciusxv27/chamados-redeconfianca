"""Orçamentos saiu de Vendas (06/11/2026) e virou o módulo /orcamentos/.

Os endereços antigos continuam funcionando: levam para o módulo novo. O modelo
(``vendas.Orcamento``) e as regras da mensagem (``vendas/orcamentos.py``)
continuam aqui — o histórico não muda de lugar.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def orcamentos(request):
    return redirect('orcamentos:inicio')


@login_required
def orcamento_reenviar(request, pk):
    return redirect('orcamentos:inicio')
