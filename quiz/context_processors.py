"""Menu do Quiz: o item aparece para todo mundo (as salas de cada um ficam lá dentro).

O número no item é o de salas abertas para a pessoa — marcadas ou acontecendo
agora —, e o item pisca quando uma delas está ao vivo. Guardado por 30 s por
pessoa: o menu roda em toda página.
"""
import logging

from django.core.cache import caches

logger = logging.getLogger(__name__)

CACHE_SEGUNDOS = 30


def quiz_menu(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}
    chave = f'quiz:menu:{user.pk}'
    try:
        guardado = caches['local'].get(chave)
        if guardado is None:
            from .models import Participante, Sala

            abertas = list(Participante.objects.filter(user=user, sala__fase__in=Sala.ABERTAS)
                           .values_list('sala__fase', flat=True))
            guardado = {'quiz_salas_abertas': len(abertas),
                        'quiz_ao_vivo': any(fase in Sala.AO_VIVO for fase in abertas)}
            caches['local'].set(chave, guardado, CACHE_SEGUNDOS)
        return guardado
    except Exception as exc:                                    # noqa: BLE001 — o menu nunca derruba a página
        logger.warning('Menu do quiz não carregou: %s', exc)
        return {}
