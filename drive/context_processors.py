"""Menu do Drive: quem vê o item.

O Drive estava fora do menu (só por link direto). O item aparece para quem tem o
que ver lá: o SUPERADMIN, que enxerga todos os setores, e quem é gestor ou tem
permissão em algum setor mapeado — a mesma régua da tela inicial do Drive. Com
o módulo desligado na configuração do Drive, só o SUPERADMIN vê o item.

Guardado por 60 s por pessoa: ``sectors_visible`` percorre os setores mapeados, e
o menu roda em toda página.
"""
import logging

from django.core.cache import caches

logger = logging.getLogger(__name__)

CACHE_SEGUNDOS = 60


def drive_menu(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'drive_liberado': False}
    chave = f'drive:menu:{user.pk}'
    try:
        guardado = caches['local'].get(chave)
        if guardado is None:
            from .models import DriveConfig
            from .permissions import is_superadmin, sectors_visible

            liberado = is_superadmin(user) or bool(DriveConfig.get().ativo and sectors_visible(user))
            guardado = {'drive_liberado': liberado}
            caches['local'].set(chave, guardado, CACHE_SEGUNDOS)
        return guardado
    except Exception as exc:                                    # noqa: BLE001 — o menu nunca derruba a página
        logger.warning('Menu do Drive não carregou: %s', exc)
        return {'drive_liberado': False}


def limpar_cache_do_menu(user_ids):
    try:
        caches['local'].delete_many([f'drive:menu:{pk}' for pk in user_ids])
    except Exception:                                           # noqa: BLE001
        pass
