"""O mapa base servido pelo próprio portal.

Quando a rede da empresa bloqueia ``tile.openstreetmap.org``, o quadro do mapa
fica cinza e não há nada que o JavaScript possa fazer: o navegador nem chega ao
servidor de imagens. Aqui o portal busca o tile, guarda no cache e devolve — o
navegador só precisa conversar com o portal, que ele já acessa.

É o **último** recurso da tela: ela tenta primeiro os provedores públicos e só
cai para cá quando nenhum carrega. O volume é baixo (o mapa é restrito à
administração) e cada imagem fica no cache por uma semana, para não repetir a
busca — a política de uso do OpenStreetMap pede exatamente isso, além do
User-Agent que identifica quem está pedindo.
"""
import logging

from django.core.cache import caches

logger = logging.getLogger(__name__)

ZOOM_MAXIMO = 19
SEGUNDOS_NO_CACHE = 7 * 24 * 3600
TEMPO_LIMITE = 6
AGENTE = 'PortalRedeConfianca/1.0 (mapa interno de gestão; contato pelo portal)'

# Na ordem em que são tentados. Dois domínios diferentes: se um estiver fora
# (ou bloqueado), o outro ainda responde.
FONTES = (
    'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
)


def coordenada_valida(z, x, y):
    """Protege contra pedido fora do mundo — e contra virar proxy de qualquer URL."""
    if not (0 <= z <= ZOOM_MAXIMO):
        return False
    limite = 2 ** z
    return 0 <= x < limite and 0 <= y < limite


def chave(z, x, y):
    return f'maps:tile:{z}:{x}:{y}'


def buscar(z, x, y):
    """Os bytes do tile (PNG), ou None se nenhuma fonte respondeu.

    Nunca levanta: o mapa perder um quadradinho é melhor do que a tela cair.
    """
    import requests

    cache = caches['default']
    guardado = cache.get(chave(z, x, y))
    if guardado is not None:
        return guardado

    for modelo in FONTES:
        url = modelo.format(z=z, x=x, y=y)
        try:
            resposta = requests.get(url, timeout=TEMPO_LIMITE,
                                    headers={'User-Agent': AGENTE})
            if resposta.status_code == 200 and resposta.content:
                cache.set(chave(z, x, y), resposta.content, SEGUNDOS_NO_CACHE)
                return resposta.content
            logger.info('Tile %s/%s/%s recusado por %s: HTTP %s',
                        z, x, y, url, resposta.status_code)
        except Exception as exc:                          # noqa: BLE001
            logger.info('Tile %s/%s/%s falhou em %s: %s', z, x, y, url, exc)
    return None
