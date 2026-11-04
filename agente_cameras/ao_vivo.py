"""Vídeo ao vivo: do gravador direto para o navegador, por WebRTC, sem passar pelo servidor do portal.

O portal só apresenta os dois lados. Vídeo H.264 segue como veio da câmera, sem recompressão: é a
imagem mais nítida possível e quase não gasta processador. H.265 o navegador não abre, então esse
caso é convertido para H.264 no computador da loja, o que custa bem mais.
"""
import asyncio
import faulthandler
import logging
import os
import re
import threading
import time
from fractions import Fraction

import aioice.ice
import av
import requests
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

log = logging.getLogger('agente')

# Uma interface de rede sem resposta (VPN, por exemplo) fazia cada vídeo esperar 5 s para abrir.
ESPERA_DOS_ENDERECOS = 2
aioice.ice.Connection.get_component_candidates.__defaults__ = (ESPERA_DOS_ENDERECOS,)

STUN = 'stun:stun.l.google.com:19302'
MAX_SESSOES = 24
MAX_ALTA_QUALIDADE = 4       # upload da loja e conexões do gravador são limitados
LARGURA_MAXIMA_CONVERTIDA = 1280
TEMPO_MAXIMO_SESSAO = 4 * 3600
ESPERA_PADRAO = 4
ABERTURAS_AO_MESMO_TEMPO = 4  # computador de loja é fraco: abrir 16 câmeras de uma vez travava tudo
MAX_ESPERA_DA_CAMERA = 20
SEM_SINAL_DE_VIDA = 45        # segundos sem o laço andar: travou
BASE_DE_TEMPO = Fraction(1, 90000)
FILA_MAXIMA = 90

# Da qualidade menor para a principal, em cada família de gravador.
TROCAS_PRINCIPAL = (('subtype=1', 'subtype=0'), ('{canal}02', '{canal}01'), ('/sub/', '/main/'),
                    ('streamType=sub', 'streamType=main'), ('_1', '_0'))


def caminho_da_qualidade(caminho, qualidade):
    if qualidade != 'ALTA':
        return caminho
    for menor, principal in TROCAS_PRINCIPAL:
        if menor in caminho:
            return caminho.replace(menor, principal)
    return caminho


class TrilhaDaCamera(MediaStreamTrack):
    """Lê o RTSP numa thread e entrega ao WebRTC. Se o envio atrasar, descarta até o próximo quadro completo."""
    kind = 'video'

    def __init__(self, url, abrir=av.open):
        super().__init__()
        self.url, self._abrir = url, abrir
        self.laco = asyncio.get_event_loop()
        self.fila = asyncio.Queue()
        self.codec = ''
        self.pronta = threading.Event()
        self.erro = ''
        self.parar_leitura = threading.Event()
        self._inicio = time.monotonic()
        self._enviando = False
        self._esperando_chave = False
        threading.Thread(target=self._ler, daemon=True).start()

    def _colocar(self, item, chave=True):
        def inserir():
            if not self._enviando and chave:
                # Antes de conectar, guarda só a partir do último quadro completo: a imagem abre na hora.
                while not self.fila.empty():
                    self.fila.get_nowait()
            elif self.fila.qsize() >= FILA_MAXIMA:
                while not self.fila.empty():
                    self.fila.get_nowait()
                self._esperando_chave = True
            if self._esperando_chave:
                if not chave:
                    return
                self._esperando_chave = False
            self.fila.put_nowait(item)
        self.laco.call_soon_threadsafe(inserir)

    def _carimbar(self, item):
        # O relógio do gravador não é confiável; vale a hora em que o quadro chegou.
        item.pts = int((time.monotonic() - self._inicio) * 90000)
        item.time_base = BASE_DE_TEMPO
        return item

    def _ler(self):
        try:
            conteiner = self._abrir(self.url, options={'rtsp_transport': 'tcp', 'fflags': 'nobuffer', 'flags': 'low_delay',
                                                       'max_delay': '500000', 'timeout': '10000000',
                                                       # Por padrão o leitor analisa 5 s de vídeo antes de começar.
                                                       'analyzeduration': '500000', 'probesize': '500000'}, timeout=15)
        except Exception as erro:  # noqa: BLE001 — câmera fora do ar, senha trocada: o navegador precisa saber
            self.erro = type(erro).__name__
            self.pronta.set()
            self.laco.call_soon_threadsafe(self.fila.put_nowait, None)
            return
        try:
            entrada = conteiner.streams.video[0]
            self.codec = entrada.codec_context.name
            self.pronta.set()
            if self.codec == 'h264':
                self._repassar(conteiner, entrada)
            else:
                self._converter(conteiner, entrada)
        except Exception as erro:  # noqa: BLE001
            log.warning('Vídeo ao vivo parou: %s', type(erro).__name__)
            self.erro = self.erro or type(erro).__name__
            self.pronta.set()
        finally:
            conteiner.close()
            self.laco.call_soon_threadsafe(self.fila.put_nowait, None)

    def _repassar(self, conteiner, entrada):
        # Parte dos gravadores só manda as configurações do vídeo no começo: sem repeti-las, quem entra depois não abre.
        filtro = av.bitstream.BitStreamFilterContext('dump_extra=freq=keyframe', entrada)
        for pacote in conteiner.demux(entrada):
            if self.parar_leitura.is_set():
                return
            if pacote.dts is None and pacote.size == 0:
                continue
            for saida in filtro.filter(pacote):
                self._colocar(self._carimbar(saida), saida.is_keyframe)

    def _converter(self, conteiner, entrada):
        for pacote in conteiner.demux(entrada):
            if self.parar_leitura.is_set():
                return
            for quadro in pacote.decode():
                if quadro.width > LARGURA_MAXIMA_CONVERTIDA:
                    quadro = quadro.reformat(width=LARGURA_MAXIMA_CONVERTIDA, height=int(quadro.height * LARGURA_MAXIMA_CONVERTIDA / quadro.width) & ~1, format='yuv420p')
                else:
                    quadro = quadro.reformat(format='yuv420p')
                self._colocar(self._carimbar(quadro), True)

    async def recv(self):
        self._enviando = True
        item = await self.fila.get()
        if item is None:
            self.stop()
            raise MediaStreamError
        return item

    def stop(self):
        self.parar_leitura.set()
        super().stop()


def limpar_candidatos_locais(sdp):
    """O Chrome esconde o IP local atrás de nomes .local. Resolver esses nomes exige multicast, que no Windows pode
    travar; sem eles a conexão segue pelos outros caminhos, e na mesma rede o endereço é descoberto na conexão."""
    return '\r\n'.join(linha for linha in sdp.splitlines() if not (linha.startswith('a=candidate') and '.local ' in linha)) + '\r\n'


def sem_senha(texto):
    """Erro do leitor de vídeo pode trazer o endereço inteiro, com usuário e senha."""
    return re.sub(r'rtsp://[^\s\'"]*@', 'rtsp://***@', str(texto))


def novo_laco():
    # No Windows o laço padrão (Proactor) derruba o UDP com erros de porta inalcançável, comuns em WebRTC.
    return asyncio.SelectorEventLoop() if os.name == 'nt' else asyncio.new_event_loop()


class AoVivo(threading.Thread):
    """Fica perguntando ao portal se alguém quer ver uma câmera, e atende cada pedido."""

    def __init__(self, portal, token, endereco, abrir=av.open):
        super().__init__(daemon=True)
        self.base = portal.rstrip('/') + '/cameras/agente/aovivo/'
        self.endereco, self.abrir = endereco, abrir
        self.sessao = requests.Session()
        self.sessao.headers['Authorization'] = f'Bearer {token}'
        self.conexoes = {}
        self.vivo_em = time.monotonic()
        self.atraso = 0.0
        self.erros = 0
        self.ultimo_erro = ''
        self.parar = False
        self.abertura = asyncio.Semaphore(ABERTURAS_AO_MESMO_TEMPO)

    def run(self):
        laco = novo_laco()
        asyncio.set_event_loop(laco)
        laco.run_until_complete(self.laco())

    def travado(self):
        return not self.is_alive() or time.monotonic() - self.vivo_em > SEM_SINAL_DE_VIDA

    def resumo(self):
        """Para o portal: dá para saber de longe se esta parte está funcionando."""
        return (f'{"parado" if self.travado() else "ok"} sessoes={len(self.conexoes)} atraso={self.atraso:.1f}s '
                f'erros={self.erros} {self.ultimo_erro}')[:200]

    def _erro(self, onde, erro):
        self.erros += 1
        self.ultimo_erro = sem_senha(f'{onde}: {type(erro).__name__} {erro}')[:120]
        log.warning('Ao vivo, %s', self.ultimo_erro)

    async def laco(self):
        espera = ESPERA_PADRAO
        while not self.parar:
            inicio = time.monotonic()
            self.vivo_em = inicio
            try:
                resposta = await asyncio.to_thread(self.sessao.post, self.base, json={'ativas': list(self.conexoes)}, timeout=15)
                resposta.raise_for_status()
                dados = resposta.json()
                espera = dados.get('proximo', ESPERA_PADRAO)
                for fechar in dados.get('fechar', []):
                    asyncio.ensure_future(self.encerrar(fechar))
                for oferta in dados.get('ofertas', []):
                    asyncio.ensure_future(self.atender(oferta))
            except asyncio.CancelledError:
                raise
            except Exception as erro:  # noqa: BLE001 — nada aqui pode parar o vídeo da loja inteira
                self._erro('pergunta ao portal', erro)
                espera = ESPERA_PADRAO * 2
            antes = time.monotonic()
            await asyncio.sleep(espera)
            self.atraso = max(0.0, time.monotonic() - antes - espera)

    async def responder(self, sessao_id, **dados):
        try:
            await asyncio.to_thread(self.sessao.post, self.base + 'resposta/', json={'id': sessao_id, **dados}, timeout=15)
        except Exception as erro:  # noqa: BLE001
            self._erro('resposta ao portal', erro)

    def _lotado(self, qualidade):
        if len(self.conexoes) >= MAX_SESSOES:
            return True
        return qualidade == 'ALTA' and sum(1 for c in self.conexoes.values() if c['qualidade'] == 'ALTA') >= MAX_ALTA_QUALIDADE

    async def encerrar(self, sessao_id):
        conexao = self.conexoes.pop(sessao_id, None)
        if not conexao:
            return
        try:
            conexao['trilha'].stop()
            await conexao['pc'].close()
        except Exception as erro:  # noqa: BLE001 — fechar não pode derrubar os outros vídeos
            self._erro('fechar vídeo', erro)

    async def atender(self, oferta):
        sessao_id, qualidade = oferta.get('id'), oferta.get('qualidade', 'BAIXA')
        try:
            if self._lotado(qualidade):
                return await self.responder(sessao_id, erro='Limite de vídeos ao vivo deste computador.')
            async with self.abertura:
                await self._abrir_sessao(sessao_id, qualidade, oferta)
        except Exception as erro:  # noqa: BLE001 — um pedido estranho não derruba os outros vídeos
            if sessao_id not in self.conexoes and 'canal' in oferta and 'sdp' in oferta:
                return   # A pessoa saiu da tela enquanto o vídeo abria: é fechamento normal, não erro.
            self._erro('abrir vídeo', erro)
            await self.encerrar(sessao_id)
            await self.responder(sessao_id, erro=f'Falha ao abrir o vídeo ({type(erro).__name__}).')

    async def _abrir_sessao(self, sessao_id, qualidade, oferta):
        url = self.endereco(oferta['canal'], qualidade)
        if not url:
            return await self.responder(sessao_id, erro='O agente ainda não achou o gravador.')
        pc = RTCPeerConnection(RTCConfiguration([RTCIceServer(STUN)]))
        trilha = TrilhaDaCamera(url, self.abrir)
        self.conexoes[sessao_id] = {'pc': pc, 'trilha': trilha, 'qualidade': qualidade}

        @pc.on('connectionstatechange')
        async def mudou():
            if pc.connectionState in ('failed', 'closed'):
                await self.encerrar(sessao_id)

        fim = time.monotonic() + MAX_ESPERA_DA_CAMERA
        while not trilha.pronta.is_set() and time.monotonic() < fim:
            await asyncio.sleep(0.05)
        if sessao_id not in self.conexoes:
            return
        if trilha.erro or not trilha.codec:
            await self.encerrar(sessao_id)
            return await self.responder(sessao_id, erro=f'A câmera não abriu ({trilha.erro or "sem resposta"}).')
        # Só H.264, e antes de ler a oferta: depois disso o aiortc já escolheu o VP8, que não sabe levar o vídeo pronto.
        pc.addTrack(trilha)
        codecs = [c for c in RTCRtpSender.getCapabilities('video').codecs if c.mimeType.lower() in ('video/h264', 'video/rtx')]
        pc.getTransceivers()[0].setCodecPreferences(codecs)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=limpar_candidatos_locais(oferta['sdp']), type='offer'))
        await pc.setLocalDescription(await pc.createAnswer())
        await self.responder(sessao_id, sdp=pc.localDescription.sdp, codec=trilha.codec)
        asyncio.get_running_loop().call_later(TEMPO_MAXIMO_SESSAO, lambda: asyncio.ensure_future(self.encerrar(sessao_id)))


class Vigia:
    """Se a parte do vídeo cair ou travar, registra onde parou e sobe outra. O resto do agente não depende dela."""

    def __init__(self, criar, destino_do_log=None):
        self.criar, self.destino_do_log = criar, destino_do_log
        self.atual = criar()
        self.atual.start()
        self.reinicios = 0

    def conferir(self):
        if not self.atual.travado():
            return
        if self.destino_do_log:
            try:
                with open(self.destino_do_log, 'a', encoding='utf-8') as arquivo:
                    arquivo.write('--- ao vivo travou, situação das threads:\n')
                    faulthandler.dump_traceback(file=arquivo, all_threads=True)
            except OSError:
                pass
        log.warning('Ao vivo parado (%s); subindo de novo', self.atual.resumo())
        self.atual.parar = True
        self.atual = self.criar()
        self.atual.start()
        self.reinicios += 1

    def resumo(self):
        return f'{self.atual.resumo()} reinicios={self.reinicios}'[:200]
