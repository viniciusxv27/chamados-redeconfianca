"""Menu do Assistente de Apresentações: quem vê o item."""
import logging

from django.core.cache import caches

logger = logging.getLogger(__name__)

CACHE_SEGUNDOS = 60


FORA_DO_ATALHO = {'apresentacoes', 'admin', 'auth', 'contenttypes', 'sessions'}


def _modulo_da_pagina(request):
    """App do portal da página aberta — para o atalho "apresentação deste módulo" no cabeçalho."""
    try:
        from django.apps import apps

        encontrado = getattr(request, 'resolver_match', None)
        if encontrado is None:
            from django.urls import resolve
            encontrado = resolve(request.path_info)
        funcao = encontrado.func
        modulo = getattr(getattr(funcao, 'view_class', funcao), '__module__', '')
        config = apps.get_containing_app_config(modulo)
    except Exception:                                           # noqa: BLE001 — 404, rota sem view
        return ''
    if not config or config.label in FORA_DO_ATALHO or request.path_info in ('/', ''):
        return ''
    return config.label


def apresentacoes_menu(request):
    user = getattr(request, 'user', None)
    vazio = {'apresentacoes_liberado': False, 'apresentacoes_modulo_atual': ''}
    if not (user and user.is_authenticated):
        return vazio
    chave = f'apresentacoes:menu:{user.pk}'
    try:
        guardado = caches['local'].get(chave)
        if guardado is None:
            from .permissoes import pode_usar

            guardado = {'apresentacoes_liberado': pode_usar(user)}
            caches['local'].set(chave, guardado, CACHE_SEGUNDOS)
        return {**guardado, 'apresentacoes_modulo_atual':
                _modulo_da_pagina(request) if guardado['apresentacoes_liberado'] else ''}
    except Exception as exc:                                    # noqa: BLE001 — o menu nunca derruba a página
        logger.warning('Menu das apresentações não carregou: %s', exc)
        return vazio


def limpar_cache_do_menu(user_ids):
    try:
        caches['local'].delete_many([f'apresentacoes:menu:{pk}' for pk in user_ids])
    except Exception:                                           # noqa: BLE001
        pass
