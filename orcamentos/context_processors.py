from django.db import transaction


def orcamentos_menu(request):
    """Mostra "Orçamentos" no menu para quem usa o módulo."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    try:
        from .permissoes import pode_usar
        # Savepoint: com a tabela ainda não migrada, o erro não pode deixar a transação
        # da página abortada (dentro de um atomic o Postgres recusaria todo o resto).
        with transaction.atomic():
            return {'orcamentos_liberado': pode_usar(user)}
    except Exception:  # noqa: BLE001 — o menu nunca derruba a página
        return {'orcamentos_liberado': False}
