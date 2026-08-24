"""Expõe o estado da coleta de posição para o template base.

Roda em toda página do portal, então precisa ser barato e nunca derrubar a
requisição: se a consulta falhar, a coleta fica desligada — errar para o lado
de não coletar é o lado seguro.
"""
from django.core.cache import caches

CACHE_SEGUNDOS = 60
CHAVE = 'maps:coleta'


def coleta_posicao(request):
    usuario = getattr(request, 'user', None)
    if not usuario or not usuario.is_authenticated:
        return {}

    cache = caches['local']
    dados = cache.get(CHAVE)
    if dados is None:
        try:
            from .models import ConfiguracaoMapa
            config = ConfiguracaoMapa.carregar()
            dados = {
                'ativa': bool(config.coleta_ativa),
                'intervalo': max(1, int(config.intervalo_minutos or 5)),
                'aviso': config.aviso or '',
            }
        except Exception:
            dados = {'ativa': False, 'intervalo': 5, 'aviso': ''}
        cache.set(CHAVE, dados, CACHE_SEGUNDOS)

    return {'mapa_coleta': dados}
