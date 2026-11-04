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

    print('muitos pedidos, abrindo e fechando no meio (a grade e a troca de tela)')
    a5 = Agente()
    pcs = []

    async def oferta(i):
        pc = RTCPeerConnection()
        pc.addTransceiver('video', direction='recvonly')
        await pc.setLocalDescription(await pc.createOffer())
        pcs.append(pc)
        return {'id': f'm{i}', 'canal': 'h264', 'qualidade': 'BAIXA', 'sdp': pc.localDescription.sdp}

    ofertas = [await oferta(i) for i in range(12)]
    tarefas = [asyncio.ensure_future(a5.atender(o)) for o in ofertas]
    await asyncio.sleep(0.3)
    for i in range(0, 12, 3):
        await a5.encerrar(f'm{i}')
    inicio_lag = time.monotonic()
    await asyncio.sleep(0.2)
    t('o laço segue andando com as câmeras abrindo', time.monotonic() - inicio_lag < 1.5, time.monotonic() - inicio_lag)
    await asyncio.wait_for(asyncio.gather(*tarefas), 60)
    fechadas = [f'm{i}' for i in range(0, 12, 3)]
    abertas = [o['id'] for o in ofertas if o['id'] not in fechadas]
    t('todos os pedidos têm resposta', set(abertas) <= set(a5.respostas), sorted(a5.respostas))
    t('os que continuaram responderam certo', all('sdp' in a5.respostas[i] for i in abertas), {i: a5.respostas[i].get('erro') for i in abertas})
    t('nenhum erro acumulado', a5.erros == 0, a5.ultimo_erro)
    for chave in list(a5.conexoes):
        await a5.encerrar(chave)
    for pc in pcs:
        await pc.close()
    t('fecha tudo limpo', not a5.conexoes and a5.erros == 0, a5.ultimo_erro)

    print('não trava')
    sdp_ = 'v=0\r\na=candidate:1 1 udp 1 abc.local 5000 typ host\r\na=candidate:2 1 udp 1 1.2.3.4 5000 typ srflx\r\na=mid:0\r\n'
    limpo = ao_vivo.limpar_candidatos_locais(sdp_)
    t('tira os nomes .local, fica o resto', '.local' not in limpo and '1.2.3.4' in limpo and 'a=mid:0' in limpo)
    t('senha some do texto de erro', 'segredo' not in ao_vivo.sem_senha("Connection refused: 'rtsp://admin:segredo@10.0.0.1:554/x'") and '10.0.0.1' in ao_vivo.sem_senha("'rtsp://admin:segredo@10.0.0.1:554/x'"))

    class PortalDoido:
        def __init__(self):
            self.vezes = 0
            self.headers = {}

        def post(self, url, json=None, timeout=None):
            self.vezes += 1
            if self.vezes == 1:
                raise TypeError('bug qualquer')
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {'ofertas': [], 'fechar': ['nao-existe'], 'proximo': 0.01} if self.vezes < 5 else {'ofertas': [{'id': 'ruim'}], 'fechar': [], 'proximo': 0.01}

    a2 = Agente()
    a2.sessao = PortalDoido()
    ao_vivo.ESPERA_PADRAO, guardada = 0.01, ao_vivo.ESPERA_PADRAO
    tarefa = asyncio.ensure_future(a2.laco())
    await asyncio.sleep(0.6)
    ao_vivo.ESPERA_PADRAO = guardada
    t('erro inesperado não mata a parte do vídeo', a2.sessao.vezes > 5 and not tarefa.done(), (a2.sessao.vezes, tarefa.done()))
    t('e fica registrado para o portal', a2.erros >= 1 and a2.ultimo_erro and 'erros=' in a2.resumo(), a2.ultimo_erro)
    t('pedido sem câmera não derruba', 'ruim' in a2.respostas and 'erro' in a2.respostas['ruim'], a2.respostas)
    tarefa.cancel()

    class Quebra:
        def stop(self):
            raise RuntimeError('fechar falhou')

    a3 = Agente()
    a3.conexoes['q'] = {'pc': None, 'trilha': Quebra(), 'qualidade': 'BAIXA'}
    await a3.encerrar('q')
    t('fechar com erro não derruba', 'q' not in a3.conexoes and a3.erros == 1)

    class Falsa:
        def __init__(self):
            self.parar, self.iniciada, self._travada = False, False, False

        def start(self):
            self.iniciada = True

        def travado(self):
            return self._travada

        def resumo(self):
            return 'falsa'

    criadas = []

    def criar():
        criadas.append(Falsa())
        return criadas[-1]

    registro = tempfile.mktemp()
    vigia = ao_vivo.Vigia(criar, registro)
    vigia.conferir()
    t('sem problema, o vigia não mexe', len(criadas) == 1)
    criadas[0]._travada = True
    vigia.conferir()
    t('travou: sobe outra e para a antiga', len(criadas) == 2 and criadas[1].iniciada and criadas[0].parar and vigia.reinicios == 1)
    t('e registra onde travou', 'threads' in open(registro, encoding='utf-8').read())
    a4 = Agente()
    t('thread que nunca rodou conta como parada', a4.travado())

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
