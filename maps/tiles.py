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

# Na ordem em que são tentados, em domínios diferentes: se um estiver fora (ou
# bloqueado), o outro ainda responde.
#
# Duas recusas, pelo mesmo truque, levaram a estas escolhas:
#
# - **tile.openstreetmap.org** recusa uso de aplicação ("Access blocked — not
#   following the tile usage policy");
# - **basemaps.cartocdn.com** exige chave e carimba "API KEY REQUIRED" no mapa.
#
# Os dois respondem **HTTP 200 com uma imagem escrita**, então o navegador acha
# que carregou e nada avisa que o mapa está errado — foi exatamente o que se viu
# na tela. O Esri (ArcGIS Online) serve estes mapas base sem chave, e é ele que
# ficou; a atribuição obrigatória vai na própria tela.
FONTES = (
    ('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map'
     '/MapServer/tile/{z}/{y}/{x}', 'image/jpeg'),
    ('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map'
     '/MapServer/tile/{z}/{y}/{x}', 'image/jpeg'),
)

# Tamanho abaixo do qual a imagem não é mapa coisa nenhuma (quadrado vazio ou
# aviso minúsculo). Serve de rede: se uma fonte começar a devolver lixo com
# HTTP 200, a próxima é tentada em vez de pintar a tela de cinza. Não pega
# carimbo grande ("API KEY REQUIRED" tem 2 KB) — para isso existe o comando
# `manage.py conferir_mapa`, que pede dois quadradinhos de lugares distantes:
# se vierem iguais, é aviso, não mapa.
MINIMO_DE_IMAGEM = 300


def coordenada_valida(z, x, y):
    """Protege contra pedido fora do mundo — e contra virar proxy de qualquer URL."""
    if not (0 <= z <= ZOOM_MAXIMO):
        return False
    limite = 2 ** z
    return 0 <= x < limite and 0 <= y < limite


def chave(z, x, y):
    return f'maps:tile:{z}:{x}:{y}'


def buscar(z, x, y):
    """(bytes, tipo) do tile, ou (None, None) se nenhuma fonte respondeu.

    Nunca levanta: o mapa perder um quadradinho é melhor do que a tela cair.
    """
    import requests

    cache = caches['default']
    guardado = cache.get(chave(z, x, y))
    if guardado is not None:
        return guardado

    for modelo, tipo_padrao in FONTES:
        url = modelo.format(z=z, x=x, y=y)
        try:
            resposta = requests.get(url, timeout=TEMPO_LIMITE,
                                    headers={'User-Agent': AGENTE})
            tipo = (resposta.headers.get('Content-Type') or tipo_padrao).split(';')[0].strip()
            if (resposta.status_code == 200 and tipo.startswith('image/')
                    and len(resposta.content or b'') >= MINIMO_DE_IMAGEM):
                cache.set(chave(z, x, y), (resposta.content, tipo), SEGUNDOS_NO_CACHE)
                return resposta.content, tipo
            logger.info('Tile %s/%s/%s recusado por %s: HTTP %s, %s, %s bytes',
                        z, x, y, url, resposta.status_code, tipo,
                        len(resposta.content or b''))
        except Exception as exc:                          # noqa: BLE001
            logger.info('Tile %s/%s/%s falhou em %s: %s', z, x, y, url, exc)
    return None, None
