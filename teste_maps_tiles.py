"""/maps/: o mapa base quando a rede bloqueia o provedor de imagens.

Pedido: "o mapa está dando bloqueio" — a tela abria com os pontos, mas o quadro
do mapa ficava cinza. O servidor estava certo o tempo todo (200 e as posições
na página); quem bloqueava era a rede, entre o navegador e o
tile.openstreetmap.org.

O que este teste cobre:

- a tela tenta OpenStreetMap, depois Carto e, por último, o próprio portal;
- o portal serve o quadradinho (`/maps/tiles/z/x/y.png`), guarda no cache e não
  sai de novo para buscar o mesmo;
- isto não é um proxy aberto: só quem pode ver o mapa baixa tile, coordenada
  fora do mundo é 404 e fonte fora do ar é 502 (a tela para de insistir);
- quando nada carrega, a tela diz o que houve em vez de ficar cinza.

Não sai para a internet: o `requests.get` é dublado. A conferência contra o
OpenStreetMap de verdade é feita pelo harness do scratchpad.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'redeconfianca.settings')
os.environ.setdefault('RC_VARREDURA_ROTINA', '0')

from django.conf import settings

settings.CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-maps-tiles'},
    'local': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'zz-maps-tiles2'},
}
django.setup()

from django.test.utils import setup_test_environment

setup_test_environment()
if 'testserver' not in settings.ALLOWED_HOSTS:
    settings.ALLOWED_HOSTS.append('testserver')

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.db import transaction
from django.test import Client

from maps import tiles
from users.models import Sector

User = get_user_model()
ok = fail = 0
# Imagem de mentira, mas do tamanho de um tile de verdade: o portal descarta
# resposta pequena demais (é assim que um aviso de bloqueio se disfarça).
PNG = b'\x89PNG\r\n\x1a\n' + b'zz-tile-de-teste' * 40


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


class RespostaFalsa:
    def __init__(self, status=200, conteudo=PNG, tipo='image/png'):
        self.status_code = status
        self.content = conteudo
        self.headers = {'Content-Type': tipo}


marcador = transaction.atomic()
marcador.__enter__()
original = None
try:
    import requests
    original = requests.get
    pedidos = []

    def get_falso(url, **kwargs):
        pedidos.append((url, kwargs.get('headers', {}).get('User-Agent', '')))
        for regra, resposta in list(get_falso.regras.items()):
            if regra in url:
                if isinstance(resposta, Exception):
                    raise resposta
                return resposta
        return RespostaFalsa()
    get_falso.regras = {}
    requests.get = get_falso

    setor = Sector.objects.create(name='ZZ Setor Mapa')
    chefe = User.objects.create_user(
        username='zzmp.chefe', email='zzmp.chefe@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Chefe', hierarchy='SUPERADMIN', is_superuser=True,
        is_staff=True, sector=setor)
    comum = User.objects.create_user(
        username='zzmp.comum', email='zzmp.comum@exemplo-teste.local', password='S3nha!teste',
        first_name='ZZ', last_name='Comum', hierarchy='PADRAO', sector=setor)

    c = Client(); c.force_login(chefe)
    cc = Client(); cc.force_login(comum)

    print('== O PORTAL SERVE O MAPA BASE ==')
    caches['default'].clear(); pedidos.clear()
    r = c.get('/maps/tiles/12/1234/2345.png')
    t('o tile responde', r.status_code == 200 and r['Content-Type'] == 'image/png',
      (r.status_code, r.get('Content-Type')))
    t('com a imagem que veio da fonte', r.content == PNG)
    t('e pediu ao Esri', len(pedidos) == 1 and 'arcgisonline.com' in pedidos[0][0], pedidos)
    t('não usa quem recusa aplicação (OSM) nem quem exige chave (Carto)',
      not any('tile.openstreetmap.org' in u or 'cartocdn' in u for u, _ in pedidos), pedidos)
    t('identificando o portal no User-Agent',
      'PortalRedeConfianca' in pedidos[0][1], pedidos[0][1])

    pedidos.clear()
    r2 = c.get('/maps/tiles/12/1234/2345.png')
    t('o mesmo quadradinho sai do cache, sem nova busca',
      r2.status_code == 200 and r2.content == PNG and not pedidos, pedidos)
    t('e o navegador é mandado guardar também',
      'max-age=604800' in r2['Cache-Control'] and 'private' in r2['Cache-Control'],
      r2.get('Cache-Control'))

    print('\n== QUANDO A PRIMEIRA FONTE NÃO RESPONDE ==')
    caches['default'].clear(); pedidos.clear()
    get_falso.regras = {'World_Street_Map': RespostaFalsa(status=403, conteudo=b'')}
    r = c.get('/maps/tiles/10/500/500.png')
    t('cai para a segunda fonte sozinho', r.status_code == 200 and r.content == PNG)
    t('tendo tentado as duas, nessa ordem',
      len(pedidos) == 2 and 'World_Street_Map' in pedidos[0][0]
      and 'World_Topo_Map' in pedidos[1][0], [p[0] for p in pedidos])

    caches['default'].clear(); pedidos.clear()
    get_falso.regras = {'arcgisonline': RuntimeError('rede fora')}
    r = c.get('/maps/tiles/10/500/500.png')
    t('nenhuma fonte respondendo vira 502 (a tela para de insistir)', r.status_code == 502,
      r.status_code)
    t('e o erro de rede não derruba a página', len(pedidos) == 2)
    get_falso.regras = {}

    print('\n== NÃO É UM PROXY ABERTO ==')
    t('quem não pode ver o mapa não baixa tile',
      cc.get('/maps/tiles/12/1234/2345.png').status_code == 403)
    t('deslogado também não',
      Client().get('/maps/tiles/12/1234/2345.png').status_code in (302, 403))
    for z, x, y in ((25, 1, 1), (12, 999999, 1), (12, 1, 999999), (3, 8, 0)):
        if c.get(f'/maps/tiles/{z}/{x}/{y}.png').status_code != 404:
            t(f'coordenada fora do mundo ({z}/{x}/{y}) é recusada', False)
            break
    else:
        t('coordenada fora do mundo é recusada (4 casos)', True)
    print('\n== IMAGEM QUE NÃO É MAPA ==')
    caches['default'].clear(); pedidos.clear()
    # O caso real: a fonte responde 200 com um avisozinho escrito em vez do
    # mapa ("Access blocked"). Pequeno demais para ser tile — passa adiante.
    get_falso.regras = {'World_Street_Map': RespostaFalsa(conteudo=b'\x89PNG bloqueado')}
    r = c.get('/maps/tiles/11/700/700.png')
    t('fonte que devolve um aviso no lugar do mapa é descartada',
      r.status_code == 200 and r.content == PNG and len(pedidos) == 2, (r.status_code, pedidos))
    caches['default'].clear(); pedidos.clear()
    get_falso.regras = {'World_Street_Map': RespostaFalsa(conteudo=b'<html>bloqueado</html>' * 30,
                                                          tipo='text/html')}
    r = c.get('/maps/tiles/11/701/701.png')
    t('página de bloqueio (HTML) também não vira mapa',
      r.status_code == 200 and r.content == PNG, r.status_code)
    get_falso.regras = {}

    print('\n== NÃO É UM PROXY ABERTO (continuação) ==')
    t('a conta do zoom confere',
      tiles.coordenada_valida(0, 0, 0) and tiles.coordenada_valida(19, 524287, 524287)
      and not tiles.coordenada_valida(19, 524288, 0))

    print('\n== A CONFERÊNCIA DAS FONTES ==')
    from io import StringIO
    from django.core.management import call_command
    # Mapa de verdade: imagens diferentes para lugares diferentes.
    chamadas = {'n': 0}

    def get_variado(url, **kwargs):
        chamadas['n'] += 1
        return RespostaFalsa(conteudo=PNG + bytes([chamadas['n'] % 251]) * 400)
    requests.get = get_variado
    saida = StringIO(); call_command('conferir_mapa', stdout=saida)
    t('o comando diz quando a fonte está servindo mapa',
      saida.getvalue().count('servindo mapa') == len(tiles.FONTES), saida.getvalue()[-200:])

    # Aviso carimbado: a mesma imagem para qualquer lugar do mundo.
    requests.get = lambda url, **kwargs: RespostaFalsa(conteudo=PNG)
    saida = StringIO(); call_command('conferir_mapa', stdout=saida)
    t('e acusa quem devolve a mesma imagem para lugares diferentes',
      'isto é um aviso, não um mapa' in saida.getvalue(), saida.getvalue()[-200:])
    requests.get = get_falso

    print('\n== A TELA ==')
    html = c.get('/maps/').content.decode()
    t('a tela abre com o mapa', 'mpMapa' in html)
    t('e conhece as duas fontes, nessa ordem',
      html.index('arcgisonline') < html.index('/maps/tiles/{z}/{x}/{y}.png'), '')
    t('sem pedir nada a quem recusou: OSM (bloqueia aplicação) e Carto (exige chave)',
      'tile.openstreetmap.org' not in html and 'cartocdn' not in html)
    t('a última fonte é o próprio portal', '/maps/tiles/{z}/{x}/{y}.png' in html)
    t('troca de fonte quando nenhuma imagem chega',
      'tileerror' in html and 'usarFonte(fonteAtual + 1)' in html)
    t('e avisa na tela em vez de ficar cinza',
      'mpFonte' in html and 'O mapa base não' in html and 'os pontos continuam marcados' in html)
    t('quando usa a fonte reserva, diz qual é', 'Mapa base pelo' in html)
    t('o sinal de que a fonte funcionou é o tile que chegou, não o fim da leva',
      "camadaBase.on('tileload'" in html and "camadaBase.on('load'" not in html)
    t('a biblioteca do mapa tem um segundo CDN',
      'unpkg.com/leaflet' in html and 'jsdelivr.net/npm/leaflet' in html
      and 'onerror="mpReserva()"' in html)
    t('e, sem nenhum dos dois, a tela explica em vez de ficar quebrada',
      'mpSemBiblioteca' in html and 'A biblioteca do mapa' in html)
finally:
    if original is not None:
        requests.get = original
    transaction.set_rollback(True)
    marcador.__exit__(None, None, None)
    print('\nrollback: nada gravado no banco.')

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
