"""Injeta o popup pendente do usuário em todas as páginas do portal.

Regras (herdadas do gate original da Pesquisa de Clima):
- Enquanto um popup ainda pode ser pulado, ele aparece apenas na home.
- Quando um popup passa a bloquear, ele aparece em qualquer página (menos as
  essenciais), sem opção de pular — travando a navegação de quem não concluiu.
"""

# Prefixos de caminho onde um popup bloqueante nunca pode aparecer, senão o
# usuário não conseguiria concluir a tarefa nem sair do portal.
ESSENTIAL_PREFIXES = ('/admin', '/login', '/logout', '/static', '/media')


def _is_essential(path):
    return any(path.startswith(p) for p in ESSENTIAL_PREFIXES)


def portal_popup_gate(request):
    empty = {'portal_popup': None, 'portal_popup_blocking': False}

    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return empty

    path = request.path or ''
    if _is_essential(path):
        return empty

    resolver = getattr(request, 'resolver_match', None)
    is_home = bool(resolver) and resolver.url_name == 'home' and not resolver.namespace

    try:
        from .models import PortalPopup

        # Sem prefetch: quando o popup é target_all (caso do clima), applies_to
        # retorna sem tocar nas relações — prefetch aqui só adicionaria queries
        # desperdiçadas em toda página autenticada (DB remoto).
        candidates = PortalPopup.objects.filter(is_active=True).order_by('order', 'id')

        def on_action_page(popup):
            return bool(popup.action_url) and path.startswith(popup.action_url)

        pending = []
        for popup in candidates:
            if not popup.is_within_window():
                continue
            if not popup.applies_to(user):
                continue
            if popup.is_completed_by(user):
                continue
            pending.append(popup)

        if not pending:
            return empty

        # Bloqueantes têm prioridade e são exigidos em sequência (por 'order').
        # Se o usuário está na página de ação do primeiro bloqueante pendente,
        # libera a navegação para ele concluir a tarefa — sem deixar um segundo
        # bloqueante prender justamente essa página.
        for popup in pending:
            if popup.is_blocking_now():
                if on_action_page(popup):
                    return empty
                return {'portal_popup': popup, 'portal_popup_blocking': True}

        # Nenhum bloqueante pendente: mostra o primeiro pulável, apenas na home.
        if is_home:
            first = pending[0]
            if not on_action_page(first):
                return {'portal_popup': first, 'portal_popup_blocking': False}
        return empty
    except Exception:
        # Nunca prende o usuário no portal por erro nesta camada.
        return empty
