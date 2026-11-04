"""Vídeo ao vivo de ponta a ponta: RTSP de verdade (mediamtx + ffmpeg) → agente → WebRTC → quadros recebidos.

Precisa de aiortc, ffmpeg e mediamtx; roda só na máquina, sem sair para a rede:
    python -m venv /tmp/ve && /tmp/ve/bin/pip install aiortc requests && brew install mediamtx
    /tmp/ve/bin/python teste_cameras_ao_vivo.py
"""
import asyncio
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent / 'agente_cameras'))
try:
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.mediastreams import MediaStreamError
except ImportError:
    sys.exit('Sem aiortc: veja o começo deste arquivo.')
if not shutil.which('mediamtx') or not shutil.which('ffmpeg'):
    sys.exit('Sem mediamtx ou ffmpeg: veja o começo deste arquivo.')

import ao_vivo  # noqa: E402

ok = fail = 0
PORTA = 18554


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


class Agente(ao_vivo.AoVivo):
    """O agente de verdade, trocando só a conversa com o portal."""

    def __init__(self):
        super().__init__('http://x', 't', self._url)
        self.respostas = {}

    def _url(self, canal, qualidade):
        return f'rtsp://127.0.0.1:{PORTA}/{canal}' if canal != 'sem' else ''

    async def responder(self, sessao_id, **dados):
        self.respostas[sessao_id] = dados


async def assistir(agente, sessao_id, canal, qualidade='BAIXA', quadros=30, limite=30):
    pc = RTCPeerConnection()
    pc.addTransceiver('video', direction='recvonly')
    await pc.setLocalDescription(await pc.createOffer())
    recebidos, primeiro = [], []
    pronto = asyncio.Event()

    @pc.on('track')
    def chegou(trilha):
        async def ler():
            try:
                while len(recebidos) < quadros:
                    recebidos.append(await trilha.recv())
                    primeiro.append(time.monotonic()) if not primeiro else None
            except MediaStreamError:
                pass
            finally:
                pronto.set()
        asyncio.ensure_future(ler())

    inicio = time.monotonic()
    await agente.atender({'id': sessao_id, 'canal': canal, 'qualidade': qualidade, 'sdp': pc.localDescription.sdp})
    resposta = agente.respostas[sessao_id]
    if resposta.get('erro'):
        await pc.close()
        return resposta, [], 0
    await pc.setRemoteDescription(RTCSessionDescription(sdp=resposta['sdp'], type='answer'))
    try:
        await asyncio.wait_for(pronto.wait(), limite)
    except asyncio.TimeoutError:
        pass
    decorrido = (primeiro[0] if primeiro else time.monotonic()) - inicio
    await pc.close()
    return resposta, recebidos, decorrido


async def rodar():
    agente = Agente()
    print('qualidade')
    t('Intelbras: principal é subtype=0', ao_vivo.caminho_da_qualidade('/cam/realmonitor?channel={canal}&subtype=1', 'ALTA') == '/cam/realmonitor?channel={canal}&subtype=0')
    t('qualidade baixa fica como está', ao_vivo.caminho_da_qualidade('/cam/realmonitor?channel={canal}&subtype=1', 'BAIXA').endswith('subtype=1'))
    t('outras famílias', ao_vivo.caminho_da_qualidade('/Streaming/Channels/{canal}02', 'ALTA') == '/Streaming/Channels/{canal}01' and ao_vivo.caminho_da_qualidade('/h264/ch{canal}/sub/av_stream', 'ALTA') == '/h264/ch{canal}/main/av_stream')
    t('caminho desconhecido não quebra', ao_vivo.caminho_da_qualidade('/stream{canal}', 'ALTA') == '/stream{canal}')

    print('H.264: repassa sem recomprimir')
    resposta, quadros, gasto = await assistir(agente, 's1', 'h264')
    t('responde sem erro, avisando o codec', resposta.get('codec') == 'h264' and 'sdp' in resposta, resposta.get('erro'))
    t('resposta negocia H.264', 'H264' in resposta.get('sdp', ''))
    t('quadros chegam decodificados', len(quadros) == 30, len(quadros))
    t('na resolução original (nítido)', quadros and quadros[-1].width == 1920 and quadros[-1].height == 1080, quadros and (quadros[-1].width, quadros[-1].height))
    t('primeira imagem em poucos segundos', gasto < 12, f'{gasto:.1f}s')
    print(f'       primeira imagem em {gasto:.1f}s')
    await agente.encerrar('s1')
    t('encerrar libera a sessão', 's1' not in agente.conexoes)

    print('H.265: o navegador não abre, então converte')
    resposta, quadros, gasto = await assistir(agente, 's2', 'h265', limite=45, quadros=15)
    t('avisa que é H.265', resposta.get('codec') == 'hevc', resposta)
    t('converte e entrega', len(quadros) == 15, len(quadros))
    t('limita a 1280 de largura', quadros and quadros[-1].width <= 1280, quadros and quadros[-1].width)
    await agente.encerrar('s2')

    print('entra depois de a câmera já estar transmitindo')
    await asyncio.sleep(3)
    resposta, quadros, gasto = await assistir(agente, 's3', 'h264', quadros=10)
    t('segunda pessoa também abre', len(quadros) == 10, len(quadros))
    await agente.encerrar('s3')

    print('erros')
    resposta, _q, _g = await assistir(agente, 's4', 'nao-existe', limite=5)
    t('câmera que não abre: avisa o navegador', 'não abriu' in resposta.get('erro', ''), resposta)
    t('e não fica pendurada', 's4' not in agente.conexoes)
    resposta, _q, _g = await assistir(agente, 's5', 'sem')
    t('sem gravador achado: avisa', 'ainda não achou' in resposta.get('erro', ''), resposta)
    await agente.atender({'id': 's6', 'canal': 'h264', 'qualidade': 'BAIXA', 'sdp': 'isto nao e sdp'})
    t('pedido estranho vira erro, sem derrubar', 'erro' in agente.respostas['s6'] and 's6' not in agente.conexoes, agente.respostas['s6'])

    print('limites')
    falso = {f'x{i}': {'qualidade': 'ALTA', 'trilha': None, 'pc': None} for i in range(ao_vivo.MAX_ALTA_QUALIDADE)}
    agente.conexoes.update(falso)
    t('limite de alta qualidade', agente._lotado('ALTA') and not agente._lotado('BAIXA'))
    await agente.atender({'id': 's7', 'canal': 'h264', 'qualidade': 'ALTA', 'sdp': 'x'})
    t('passou do limite: avisa', 'Limite' in agente.respostas['s7'].get('erro', ''))
    for chave in falso:
        del agente.conexoes[chave]


def main():
    pasta = tempfile.mkdtemp()
    config = pathlib.Path(pasta) / 'mediamtx.yml'
    config.write_text(f'logLevel: error\nrtspAddress: :{PORTA}\nrtmp: no\nhls: no\nwebrtc: no\nsrt: no\napi: no\nmetrics: no\npprof: no\nprotocols: [tcp]\npaths:\n  all_others:\n')
    servidor = subprocess.Popen(['mediamtx', str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    base = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-re', '-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=15']
    fontes = [
        subprocess.Popen(base + ['-c:v', 'libx264', '-profile:v', 'high', '-preset', 'ultrafast', '-tune', 'zerolatency', '-g', '30', '-f', 'rtsp', '-rtsp_transport', 'tcp', f'rtsp://127.0.0.1:{PORTA}/h264']),
        subprocess.Popen(base + ['-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', 'log-level=error:keyint=30', '-f', 'rtsp', '-rtsp_transport', 'tcp', f'rtsp://127.0.0.1:{PORTA}/h265']),
    ]
    time.sleep(4)
    try:
        asyncio.run(rodar())
    finally:
        for processo in fontes + [servidor]:
            processo.terminate()
        shutil.rmtree(pasta, ignore_errors=True)
    print(f'\n{ok} OK, {fail} falha(s)')
    os._exit(1 if fail else 0)


if __name__ == '__main__':
    main()
