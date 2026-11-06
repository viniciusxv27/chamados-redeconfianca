from django.db import transaction


def solicitacoes_dp_menu(request):
    """Aba e contador da caixa do DP (só para quem analisa)."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    try:
        from .models import Solicitacao
        from .permissoes import e_analista
        # Savepoint: tabela ainda não migrada não pode deixar a transação da página abortada.
        with transaction.atomic():
            if not e_analista(user):
                return {'dp_analista': False}
            return {'dp_analista': True, 'dp_pendentes': Solicitacao.objects.filter(
                status__in=(Solicitacao.ENVIADA, Solicitacao.EM_ANALISE)).count()}
    except Exception:  # noqa: BLE001 — o menu nunca derruba a página
        return {}
