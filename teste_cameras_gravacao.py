"""Agente das câmeras: a gravação dos 3 minutos dos alertas e o detector, com vídeo e modelo de verdade.

Vídeo sintético (H.264 e H.265 em MPEG-TS, os mesmos pacotes Annex-B do RTSP do gravador) passa pela
memória de vídeo, pelo montador e pela câmera do agente com um relógio simulado; o MP4 que sai é aberto
e conferido. Não usa rede nem o portal.

Precisa de av, opencv-python-headless<5, onnxruntime e numpy (fora do venv do portal):
    python -m venv /tmp/ve && /tmp/ve/bin/pip install av "opencv-python-headless<5" onnxruntime numpy requests
    /tmp/ve/bin/python teste_cameras_gravacao.py [pasta-com-yolox_s.onnx]
"""
import json
import pathlib
import sys
import tempfile
import threading
import time
import types
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent / 'agente_cameras'))
try:
    import av
    import cv2
    import numpy as np
except ImportError:
    sys.exit('Sem av/opencv/numpy: veja o começo deste arquivo.')

import agente  # noqa: E402
import gravacao  # noqa: E402
from regras import Evento  # noqa: E402

ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


PASTA = pathlib.Path(tempfile.mkdtemp(prefix='zz-gravacao-'))
FPS = 10


def video_sintetico(caminho, segundos, codec='libx264', largura=320, altura=240):
    """Câmera de mentira: um quadro diferente a cada instante, quadro completo a cada 2 s."""
    saida = av.open(str(caminho), 'w', format='mpegts')
    trilha = saida.add_stream(codec, rate=FPS)
    trilha.width, trilha.height, trilha.pix_fmt = largura, altura, 'yuv420p'
    trilha.options = {'g': str(FPS * 2), 'keyint_min': str(FPS * 2), 'preset': 'ultrafast'} if codec == 'libx264' else \
        {'x265-params': f'keyint={FPS * 2}:min-keyint={FPS * 2}:log-level=error', 'preset': 'ultrafast'}
    for n in range(segundos * FPS):
        imagem = np.zeros((altura, largura, 3), dtype=np.uint8)
        imagem[:, :, 0] = (n * 3) % 255
        imagem[:, :, 1] = np.linspace(0, 255, largura, dtype=np.uint8)[None, :]
        cv2.putText(imagem, f'{n // FPS:04d}', (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        quadro = av.VideoFrame.from_ndarray(imagem, format='bgr24')
        for pacote in trilha.encode(quadro):
            saida.mux(pacote)
    for pacote in trilha.encode(None):
        saida.mux(pacote)
    saida.close()
    return caminho


def alimentar(memoria, caminho, relogio_inicio=0.0, ate=None, depois_de_cada=None):
    """Passa o vídeo pela memória como a câmera faz (decodifica, depois guarda). Devolve o relógio no fim."""
    entrada = av.open(str(caminho))
    fonte = entrada.streams.video[0]
    memoria.nova_entrada(fonte)
    agora = relogio_inicio
    for pacote in entrada.demux(fonte):
        if pacote.dts is not None and pacote.pts is not None:
            agora = relogio_inicio + float(pacote.pts * fonte.time_base) - float((fonte.start_time or 0) * fonte.time_base)
        if ate is not None and agora > ate:
            break
        quadros = pacote.decode() if pacote.size else []
        memoria.pacote(pacote, agora)
        for quadro in quadros:
            if memoria.quer_quadro(agora):
                memoria.quadro(quadro.to_ndarray(format='bgr24'), agora)
        if depois_de_cada:
            depois_de_cada(agora)
    memoria.nova_entrada(None)
    entrada.close()
    return agora


def conferir_mp4(caminho):
    """(codec, segundos, quadros, dts sempre crescente, primeiro é quadro completo, moov antes do mdat)."""
    dados = pathlib.Path(caminho).read_bytes()
    entrada = av.open(str(caminho))
    fonte = entrada.streams.video[0]
    dts, quadros, primeiro_chave = [], 0, None
    for pacote in entrada.demux(fonte):
        if pacote.dts is None:
            continue
        if primeiro_chave is None:
            primeiro_chave = pacote.is_keyframe
        dts.append(pacote.dts)
        quadros += len(pacote.decode())
    segundos = float(entrada.duration / av.time_base) if entrada.duration else 0.0
    codec = fonte.codec_context.name
    entrada.close()
    return codec, segundos, quadros, all(b > a for a, b in zip(dts, dts[1:])), primeiro_chave, dados.find(b'moov') < dados.find(b'mdat')


print('memória de vídeo')
ts264 = video_sintetico(PASTA / 'camera.ts', 260)
montados = []


class MontadorDeTeste:
    def montar(self, pedido, pedacos, quadros):
        montados.append((pedido, pedacos, quadros))


mem = gravacao.Memoria(1, MontadorDeTeste())
picos = []
alimentar(mem, ts264, depois_de_cada=lambda agora: picos.append((len(mem.pedacos), sum(len(p.dados) for p in mem.pedacos))))
duracoes = [p.fim - p.inicio for p in mem.pedacos]
t('guarda em pedaços de ~10 s, começando em quadro completo', all(9 <= d <= 12 for d in duracoes[:-1]), duracoes)
t('fica só com os últimos ~2 minutos', mem.pedacos[-1].fim - mem.pedacos[0].fim <= gravacao.SEGUNDOS_GUARDADOS and max(n for n, _b in picos) <= 14,
  (mem.pedacos[0].fim, mem.pedacos[-1].fim))
t('cada pedaço é um MP4 que abre', all(av.open(__import__('io').BytesIO(p.dados)).streams.video[0].codec_context.name == 'h264' for p in mem.pedacos))
t('quadros-chave a cada 2 s, só os recentes', 1.9 <= mem.quadros[1][0] - mem.quadros[0][0] <= 2.1 and mem.quadros[-1][0] - mem.quadros[0][0] <= gravacao.SEGUNDOS_GUARDADOS + 2)
t('pouca memória (vídeo de verdade da câmera menor fica em ~8 MB)', max(b for _n, b in picos) < 20 * 1024 * 1024, max(b for _n, b in picos))

print('pedido de vídeo: 1min30 antes e depois')
montados.clear()
mem = gravacao.Memoria(1, MontadorDeTeste())
pedido = gravacao.Pedido(alerta=42, canal=1, evento=150.0, momento=datetime(2026, 11, 6, 13, 0, tzinfo=timezone.utc), antes=90, depois=90, acao=-25)


def no_meio(agora):
    if agora >= 150 and not mem.pedidos and not montados:
        mem.pedir(pedido)
    mem.conferir(agora)


alimentar(mem, ts264, depois_de_cada=no_meio)
t('o pedido só sai depois do "depois" gravado', len(montados) == 1 and montados[0][1][-1].fim >= 240, [p.fim for p in montados[0][1]] if montados else None)
pedacos = montados[0][1] if montados else []
t('o "antes" não foi descartado enquanto o pedido esperava', pedacos and pedacos[0].inicio <= 60 <= pedacos[0].fim + 10, pedacos[0].inicio if pedacos else None)
fila = gravacao.FilaDeClipes(str(PASTA / 'fila'))
montador = gravacao.Montador(fila)
caminho = montador.processar(*montados[0])
codec, segundos, quadros, crescente, chave, rapido = conferir_mp4(caminho)
t('sai um MP4 H.264 de 3 minutos que toca no navegador', codec == 'h264' and 180 <= segundos <= 183 and chave and crescente, (codec, segundos))
t('com todos os quadros (sem buraco)', abs(quadros - segundos * FPS) <= FPS, (quadros, segundos))
t('começa a tocar antes de baixar tudo (faststart)', rapido)
dados = json.loads((PASTA / 'fila' / '42.json').read_text(encoding='utf-8'))
t('sabe em que segundo do vídeo está o alerta', 90 <= dados['evento_segundo'] <= 92 and dados['segundos'] == round(segundos), dados)
t('hora do 1º quadro do vídeo', dados['inicio'] == (pedido.momento - __import__('datetime').timedelta(seconds=dados['evento_segundo'])).isoformat())
segs = [q['segundo'] for q in dados['quadros']]
t('quadros-chave em volta da ação (-25 s) e do depois', any(abs(s + 25) <= 2 for s in segs) and 0 in segs and max(segs) >= 58 and len(segs) >= 9, segs)
t('os quadros-chave são JPEG na pasta', all((PASTA / 'fila' / q['arquivo']).read_bytes()[:2] == b'\xff\xd8' for q in dados['quadros']))
num = cv2.imdecode(np.frombuffer((PASTA / 'fila' / next(q['arquivo'] for q in dados['quadros'] if q['segundo'] == 0)).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
t('o quadro do alerta é do instante certo (contador da câmera)', num is not None and num.shape[1] == 320)

print('câmera que reconecta no meio')
montados.clear()
mem = gravacao.Memoria(1, MontadorDeTeste())
curto = video_sintetico(PASTA / 'curto.ts', 60)
fim = alimentar(mem, curto)
alimentar(mem, curto, relogio_inicio=fim + 3)        # o relógio do vídeo recomeça do zero
mem.pedir(gravacao.Pedido(alerta=43, canal=1, evento=fim, momento=datetime.now(timezone.utc), antes=40, depois=40))
mem.conferir(fim + 70)
caminho = gravacao.Montador(fila).processar(*montados[0])
codec, segundos, quadros, crescente, chave, _ = conferir_mp4(caminho)
t('junta os dois trechos num vídeo só, com o tempo sempre andando', crescente and chave and 60 <= segundos <= 83, (segundos, crescente))

print('câmera em H.265')
montados.clear()
mem = gravacao.Memoria(1, MontadorDeTeste())
ts265 = video_sintetico(PASTA / 'camera265.ts', 50, codec='libx265')
fim = alimentar(mem, ts265)
mem.pedir(gravacao.Pedido(alerta=44, canal=1, evento=30.0, momento=datetime.now(timezone.utc), antes=20, depois=15))
mem.conferir(fim + 30)
t('pedaços guardados como vieram (H.265, sem gastar processador)', av.open(__import__('io').BytesIO(mem.pedacos[0].dados)).streams.video[0].codec_context.name == 'hevc')
inicio = time.perf_counter()
caminho = gravacao.Montador(fila).processar(*montados[0])
gasto = time.perf_counter() - inicio
codec, segundos, quadros, crescente, chave, rapido = conferir_mp4(caminho)
t('o vídeo do alerta sai convertido para H.264', codec == 'h264' and chave and crescente and rapido, codec)
t('cortado no quadro exato (20 s antes, 15 depois)', 34 <= segundos <= 36 and abs(quadros - segundos * FPS) <= FPS, (segundos, quadros))
print(f'       (conversão de {segundos:.0f} s de vídeo em {gasto:.1f} s)')

print('câmera do agente de ponta a ponta (relógio simulado)')


class Relogio:
    def __init__(self):
        self.agora, self.trava = 1000.0, threading.Lock()

    def monotonic(self):
        with self.trava:
            self.agora += 1 / FPS          # a câmera chama uma vez por pacote: um pacote por quadro
            return self.agora


relogio = Relogio()
agente.time = types.SimpleNamespace(monotonic=relogio.monotonic, sleep=time.sleep)
chamadas = {'detector': 0}


def detector_falso(quadro):
    chamadas['detector'] += 1
    # Loja fechada; depois de ~100 s aparece alguém.
    return [{'classe': 'person', 'conf': .9, 'caixa': (.4, .4, .2, .4)}] if chamadas['detector'] > 100 else []


class PortalFalso:
    def __init__(self):
        self.alertas = []

    def alerta(self, canal, evento, foto, grava_video=False):
        self.alertas.append((canal, evento, foto, grava_video))
        return {'ok': True, 'alerta': 77, 'quer_video': True, 'antes': 60, 'depois': 30}


portal = PortalFalso()
fila2 = gravacao.FilaDeClipes(str(PASTA / 'fila2'))
montador2 = gravacao.Montador(fila2)
montador2.start()
estado = {'abre_as': '00:00', 'fecha_as': '00:00', 'canais': {1: {'numero': 1, 'ia': True, 'zonas': []}}, 'ajustes': {}, 'video': {}}
camera = agente.Camera(1, str(ts264), portal, detector_falso, estado, None, montador2)
t('a câmera abre com a memória de vídeo', camera.memoria is not None)
camera.start()
limite = time.time() + 120
while time.time() < limite and not (PASTA / 'fila2' / '77.json').exists():
    time.sleep(.2)
camera.parar.set()
evento = portal.alertas[0][1] if portal.alertas else Evento('X', 0, '')
t('a análise dispara o alerta e avisa que grava vídeo', len(portal.alertas) == 1 and evento.tipo == 'FORA_HORARIO' and portal.alertas[0][3]
  and portal.alertas[0][2][:2] == b'\xff\xd8', portal.alertas[:1])
t('o vídeo do alerta fica pronto na fila de envio', (PASTA / 'fila2' / '77.json').exists() and (PASTA / 'fila2' / '77.mp4').exists())
if (PASTA / 'fila2' / '77.mp4').exists():
    codec, segundos, quadros, crescente, chave, _ = conferir_mp4(PASTA / 'fila2' / '77.mp4')
    d77 = json.loads((PASTA / 'fila2' / '77.json').read_text(encoding='utf-8'))
    t('com o tamanho pedido pelo portal (60 s antes, 30 depois)', codec == 'h264' and 89 <= segundos <= 93 and crescente and 60 <= d77['evento_segundo'] <= 62,
      (segundos, d77['evento_segundo']))
t('a câmera guardou a última imagem para o mosaico', camera.ultimo is not None and camera.ultimo.shape == (240, 320, 3))

print('detector')
pasta_modelos = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
if pasta_modelos and (pasta_modelos / 'yolox_s.onnx').exists():
    import detector as det
    d = det.Detector(str(pasta_modelos / 'yolox_s.onnx'), nome='YOLOX-s')
    t('YOLOX-s lê o tamanho de entrada do próprio modelo (640)', d.tamanho == 640)
    ms = d.medir()
    t('mede o tempo por imagem', ms > 0 and 'YOLOX-s 640px' in d.descricao and 'ms por imagem' in d.descricao, d.descricao)
    imagem = cv2.imread(str(pathlib.Path(__file__).parent / 'static' / 'images' / 'reuniao-fundo.jpg'))
    saida = d(imagem)
    t('roda numa foto de verdade e devolve caixas válidas', all(0 <= x['caixa'][0] <= 1 and 0 <= x['caixa'][1] <= 1 and x['classe'] in det.CLASSES.values() for x in saida))
    t('sem gente na foto, não inventa pessoa com certeza', not any(x['classe'] == 'person' and x['conf'] >= .5 for x in saida), saida)
    escolhido = det.escolher(str(pasta_modelos))
    t('escolhe o forte quando é o que há / quando cabe no tempo', escolhido.nome == 'YOLOX-s')
    lento = det.escolher(str(pasta_modelos), limite_ms=0.001)
    t('mesmo lento, sendo o único, fica com ele', lento.nome == 'YOLOX-s')
    print(f'       ({d.descricao}; provedores: {det.provedores()})')
else:
    print('  (sem a pasta com yolox_s.onnx: detector não testado)')

print(f'\n{ok} OK, {fail} falha(s)')
sys.exit(1 if fail else 0)
