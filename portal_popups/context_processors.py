"""Injeta o popup pendente do usuário em todas as páginas do portal.

Regras (herdadas do gate original da Pesquisa de Clima):
- Enquanto um popup ainda pode ser pulado, ele aparece apenas na home.
- Quando um popup passa a bloquear, ele aparece em qualquer página (menos as
  essenciais), sem opção de pular — travando a navegação de quem não concluiu.
- Nada aparece enquanto a pessoa ainda precisa bater o ponto: a jornada vem
  primeiro, o comunicado depois.
"""

# Prefixos de caminho onde um popup bloqueante nunca pode aparecer, senão o
# usuário não conseguiria concluir a tarefa nem sair do portal.
ESSENTIAL_PREFIXES = ('/admin', '/login', '/logout', '/static', '/media')

# A tela de ponto e a de bloqueio de jornada entram na mesma lista por um
# motivo concreto: sem isso as duas travas se mordiam. O popup exigia o "de
# acordo" para liberar o portal, o bloqueio de jornada exigia a batida do ponto
# para liberar o portal, e a batida ficava atrás do popup. Ninguém entrava.
PONTO_PREFIXES = ('/ponto/', '/api/tangerino/')


def _is_essential(path):
    return any(path.startswith(p) for p in ESSENTIAL_PREFIXES + PONTO_PREFIXES)


def _deve_bater_ponto(user):
    """A pessoa está com a jornada trancada agora (falta bater o ponto)?

    Lê a mesma decisão que o middleware de jornada guardou em cache, então na
    prática não custa consulta nenhuma. Em qualquer erro responde ``False`` —
    é o mesmo lado para o qual `decidir_bloqueio` já falha: sem dado confiável,
    não se inventa bloqueio.
    """
    try:
        from django.core.cache import caches

        from tangerino.middleware import (CACHE_DECISAO_SEGUNDOS, chave_da_decisao,
                                          decidir_bloqueio)

        if user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN':
            return False

        cache = caches['local']
        chave = chave_da_decisao(user)
        decisao = cache.get(chave)
        if decisao is None:
            decisao = decidir_bloqueio(user) or {}
            cache.set(chave, decisao, CACHE_DECISAO_SEGUNDOS)
        return bool(decisao)
    except Exception:
        return False


def portal_popup_gate(request):
    empty = {'portal_popup': None, 'portal_popup_blocking': False}

    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return empty

    path = request.path or ''
    if _is_essential(path):
        return empty

    # Comunicado só depois do ponto batido: primeiro a pessoa regulariza a
    # jornada, aí o portal cobra o "de acordo".
    if _deve_bater_ponto(user):
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
