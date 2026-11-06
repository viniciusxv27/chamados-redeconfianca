"""Os 3 minutos em volta de cada alerta: 1min30 antes e 1min30 depois, em MP4, com quadros-chave.

Como funciona, por câmera:
- O vídeo que chega do gravador (o mesmo RTSP da análise: uma conexão só) é guardado em memória em
  pedaços de ~10 s, já em MP4, copiando os pacotes como vieram — sem recomprimir, quase sem gastar
  processador. Ficam só os últimos ~2 minutos (~8 MB na qualidade menor do gravador).
- Um quadro a cada 2 s vira JPEG pequeno, para os quadros-chave da IA de visão no portal.
- Quando a análise dispara um alerta e o portal quer o vídeo, o pedido espera 1min30 e um
  montador (outra thread, para não atrasar a leitura da câmera) junta os pedaços num MP4 só.
  Câmera em H.265 é convertida para H.264, que é o que o navegador toca.
- O MP4 e os quadros vão para a pasta fila_clipes e sobem ao portal no próximo contato. Sem
  internet, esperam (até 3 dias / 3 GB).
"""
import io
import json
import logging
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta

log = logging.getLogger('agente')

SEGUNDOS_POR_PEDACO = 10
SEGUNDOS_GUARDADOS = 120
BYTES_GUARDADOS_MAX = 80 * 1024 * 1024
SEGUNDOS_ENTRE_QUADROS = 2
LARGURA_DO_QUADRO = 640
ESPERA_EXTRA = 25             # depois do "depois", espera o pedaço fechar; passou disso, monta com o que tem
FILA_BYTES_MAX = 3 * 1024 ** 3
FILA_DIAS_MAX = 3
ENVIOS_POR_VEZ = 2


def _trilha_como(saida, modelo):
    """Trilha de saída igual à de entrada (a API do PyAV mudou na versão 14)."""
    if hasattr(saida, 'add_stream_from_template'):
        return saida.add_stream_from_template(modelo)
    return saida.add_stream(template=modelo)


def momentos_dos_quadros(antes, depois, acao=0):
    """Segundos (em relação ao alerta) dos quadros-chave: o começo, a ação de perto e o depois."""
    acao = max(-antes, min(0, int(acao or 0)))
    pontos = {-min(80, antes), -min(45, antes), acao - 10, acao - 5, acao - 2, acao, acao + 3, 0, min(15, depois), min(60, depois)}
    return sorted(p for p in pontos if -antes <= p <= depois)


@dataclass
class Pedaco:
    inicio: float          # relógio do agente (time.monotonic) do 1º pacote
    fim: float
    dados: bytes


@dataclass
class Pedido:
    alerta: int
    canal: int
    evento: float          # time.monotonic do alerta
    momento: object        # datetime do alerta (com fuso)
    antes: int
    depois: int
    acao: int = 0


class Memoria:
    """O vídeo recente de uma câmera. `pacote()` e `quadro()` vêm da thread da câmera."""

    def __init__(self, canal, montador=None, abrir=None):
        import av
        self.av = av
        self.abrir = abrir or av.open
        self.canal, self.montador = canal, montador
        self.pedacos = deque()
        self.quadros = deque()          # (agora, jpg)
        self.pedidos = []
        self._modelo = None
        self._saida = self._buffer = self._trilha = None
        self._inicio_pedaco = self._ultimo_pacote = 0.0
        self._ultimo_quadro = 0.0
        self.trava = threading.Lock()

    # ── Pedaços de vídeo ─────────────────────────────────────────────────────
    def nova_entrada(self, entrada):
        """A câmera (re)conectou (ou caiu, com None): o pedaço aberto fecha aqui."""
        self._fechar_pedaco()
        self._modelo = entrada

    def _abrir_pedaco(self, agora):
        self._buffer = io.BytesIO()
        self._saida = self.abrir(self._buffer, 'w', format='mp4')
        self._trilha = _trilha_como(self._saida, self._modelo)
        self._inicio_pedaco = agora

    def _fechar_pedaco(self):
        if self._saida is None:
            return
        try:
            self._saida.close()
            dados = self._buffer.getvalue()
        except Exception as erro:  # noqa: BLE001 — pedaço com defeito: perde só ele
            log.warning('Canal %s: pedaço de vídeo perdido (%s)', self.canal, type(erro).__name__)
            dados = b''
        self._saida = self._buffer = self._trilha = None
        if dados:
            with self.trava:
                self.pedacos.append(Pedaco(self._inicio_pedaco, self._ultimo_pacote, dados))
                self._podar()

    def _podar(self):
        if not self.pedacos:
            return
        mais_novo = self.pedacos[-1].fim
        total = sum(len(p.dados) for p in self.pedacos)
        while self.pedacos and (mais_novo - self.pedacos[0].fim > SEGUNDOS_GUARDADOS or total > BYTES_GUARDADOS_MAX):
            total -= len(self.pedacos.popleft().dados)

    def pacote(self, pacote, agora):
        """Guarda o pacote. Chame DEPOIS de decodificar: o mux muda o pacote."""
        if self._modelo is None or pacote.dts is None or pacote.size == 0:
            return
        chave = pacote.is_keyframe
        if self._saida is not None and chave and agora - self._inicio_pedaco >= SEGUNDOS_POR_PEDACO:
            self._fechar_pedaco()
        if self._saida is None:
            if not chave:
                return                      # pedaço sempre começa num quadro completo
            self._abrir_pedaco(agora)
        try:
            pacote.stream = self._trilha
            self._saida.mux(pacote)
            self._ultimo_pacote = agora
        except Exception as erro:  # noqa: BLE001 — pacote torto do gravador: recomeça no próximo quadro completo
            log.info('Canal %s: pacote recusado (%s)', self.canal, type(erro).__name__)
            self._saida = self._buffer = self._trilha = None

    # ── Quadros-chave ────────────────────────────────────────────────────────
    def quer_quadro(self, agora):
        return agora - self._ultimo_quadro >= SEGUNDOS_ENTRE_QUADROS

    def quadro(self, imagem, agora):
        if not self.quer_quadro(agora):
            return
        import cv2
        self._ultimo_quadro = agora
        altura, largura = imagem.shape[:2]
        if largura > LARGURA_DO_QUADRO:
            imagem = cv2.resize(imagem, (LARGURA_DO_QUADRO, int(altura * LARGURA_DO_QUADRO / largura)))
        ok, jpg = cv2.imencode('.jpg', imagem, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if ok:
            with self.trava:
                self.quadros.append((agora, jpg.tobytes()))
                while self.quadros and agora - self.quadros[0][0] > SEGUNDOS_GUARDADOS + 120:
                    self.quadros.popleft()

    # ── Pedidos de vídeo ─────────────────────────────────────────────────────
    def pedir(self, pedido):
        with self.trava:
            if not any(p.alerta == pedido.alerta for p in self.pedidos):
                self.pedidos.append(pedido)

    def conferir(self, agora):
        """Entrega ao montador os pedidos cujo "depois" já foi gravado."""
        prontos = []
        with self.trava:
            fim_gravado = self.pedacos[-1].fim if self.pedacos else 0.0
            for pedido in list(self.pedidos):
                alvo = pedido.evento + pedido.depois
                if fim_gravado >= alvo or agora >= alvo + ESPERA_EXTRA:
                    self.pedidos.remove(pedido)
                    inicio = pedido.evento - pedido.antes
                    pedacos = [p for p in self.pedacos if p.fim >= inicio and p.inicio <= alvo]
                    quadros = [(t, j) for t, j in self.quadros if inicio - 3 <= t <= alvo + 3]
                    prontos.append((pedido, pedacos, quadros))
        for item in prontos:
            if self.montador is not None:
                self.montador.montar(*item)


def escolher_quadros(pedido, quadros):
    """[(segundo relativo, jpg)] mais perto de cada momento-chave, sem repetir."""
    escolhidos, usados = [], set()
    for alvo in momentos_dos_quadros(pedido.antes, pedido.depois, pedido.acao):
        melhor = min(((abs((t - pedido.evento) - alvo), i) for i, (t, _j) in enumerate(quadros) if i not in usados), default=None)
        if melhor is None or melhor[0] > 6:
            continue
        usados.add(melhor[1])
        t, jpg = quadros[melhor[1]]
        escolhidos.append((round(t - pedido.evento), jpg))
    return sorted(escolhidos)


def juntar(pedacos, caminho, abrir):
    """Junta os pedaços num MP4 que toca no navegador. Devolve (segundos, deslocamento do 1º pacote em s)."""
    saida = abrir(caminho, 'w', format='mp4', options={'movflags': '+faststart'})
    trilha = codificador = None
    primeiro = ultimo_dts = None
    deslocamento = 0
    duracao = 0.0
    try:
        for pedaco in pedacos:
            entrada = abrir(io.BytesIO(pedaco.dados), 'r', format='mp4')
            try:
                fonte = entrada.streams.video[0]
                if trilha is None:
                    if fonte.codec_context.name == 'h264':
                        trilha = _trilha_como(saida, fonte)
                    else:
                        # H.265 e outros: o navegador não toca; converte uma vez, só este trecho.
                        trilha = saida.add_stream('libx264', rate=fonte.average_rate or 15)
                        trilha.width, trilha.height = fonte.codec_context.width, fonte.codec_context.height
                        trilha.pix_fmt = 'yuv420p'
                        trilha.options = {'preset': 'veryfast', 'crf': '26'}
                        codificador = trilha
                base = fonte.time_base
                if codificador is not None:
                    for quadro in entrada.decode(fonte):
                        quadro.pts = None
                        for pacote in codificador.encode(quadro):
                            saida.mux(pacote)
                        duracao += 1 / float(fonte.average_rate or 15)
                    continue
                for pacote in entrada.demux(fonte):
                    if pacote.dts is None:
                        continue
                    if primeiro is None:
                        primeiro = pacote.dts
                    # A câmera pode ter reconectado entre dois pedaços: o relógio recomeça; continua em frente.
                    dts = pacote.dts - primeiro + deslocamento
                    if ultimo_dts is not None and dts <= ultimo_dts:
                        deslocamento += ultimo_dts + 1 - dts
                        dts = ultimo_dts + 1
                    pts = (pacote.pts - primeiro + deslocamento) if pacote.pts is not None else dts
                    pacote.dts, pacote.pts = dts, max(pts, dts)
                    ultimo_dts = dts
                    duracao = max(duracao, float((pacote.pts + (pacote.duration or 0)) * base))
                    pacote.stream = trilha
                    saida.mux(pacote)
            finally:
                entrada.close()
        if codificador is not None:
            for pacote in codificador.encode(None):
                saida.mux(pacote)
    finally:
        saida.close()
    return duracao


class Montador(threading.Thread):
    """Monta os vídeos fora da thread da câmera e põe na fila de envio."""

    def __init__(self, fila, abrir=None):
        super().__init__(daemon=True)
        self.fila, self.pedidos = fila, queue.Queue()
        self.abrir = abrir

    def montar(self, pedido, pedacos, quadros):
        self.pedidos.put((pedido, pedacos, quadros))

    def run(self):
        while True:
            pedido, pedacos, quadros = self.pedidos.get()
            try:
                self.processar(pedido, pedacos, quadros)
            except Exception as erro:  # noqa: BLE001 — um vídeo com defeito não derruba o agente
                log.warning('Vídeo do alerta %s não montado: %s', pedido.alerta, erro)

    def processar(self, pedido, pedacos, quadros):
        if not pedacos:
            log.warning('Alerta %s: sem vídeo guardado para montar', pedido.alerta)
            return None
        abrir = self.abrir
        if abrir is None:
            import av
            abrir = av.open
        caminho = self.fila.caminho(pedido.alerta, '.mp4')
        temporario = caminho + '.parcial'
        segundos = juntar(pedacos, temporario, abrir)
        os.replace(temporario, caminho)
        # Em que segundo do vídeo está o alerta, e a hora do 1º quadro.
        evento_segundo = max(0, round(pedido.evento - pedacos[0].inicio))
        inicio = pedido.momento - timedelta(seconds=evento_segundo)
        escolhidos = escolher_quadros(pedido, quadros)
        nomes = []
        for segundo, jpg in escolhidos:
            nome = f'{pedido.alerta}_q{"m" if segundo < 0 else ""}{abs(segundo)}.jpg'
            with open(self.fila.caminho_arquivo(nome), 'wb') as arquivo:
                arquivo.write(jpg)
            nomes.append({'segundo': segundo, 'arquivo': nome})
        self.fila.registrar(pedido.alerta, {'alerta': pedido.alerta, 'canal': pedido.canal, 'inicio': inicio.isoformat(),
                                            'segundos': round(segundos), 'evento_segundo': evento_segundo, 'quadros': nomes})
        log.info('Vídeo do alerta %s pronto: %.0f s, %d quadros', pedido.alerta, segundos, len(nomes))
        return caminho


class FilaDeClipes:
    """Vídeos esperando para subir ao portal. Em disco: sobrevive a queda de internet e de luz."""

    def __init__(self, pasta):
        self.pasta = pasta
        os.makedirs(pasta, exist_ok=True)
        self.trava = threading.Lock()

    def caminho(self, alerta, extensao):
        return os.path.join(self.pasta, f'{int(alerta)}{extensao}')

    def caminho_arquivo(self, nome):
        return os.path.join(self.pasta, os.path.basename(nome))

    def registrar(self, alerta, dados):
        temporario = self.caminho(alerta, '.json.tmp')
        with open(temporario, 'w', encoding='utf-8') as arquivo:
            json.dump(dados, arquivo)
        os.replace(temporario, self.caminho(alerta, '.json'))

    def pendentes(self):
        try:
            nomes = [n for n in os.listdir(self.pasta) if n.endswith('.json')]
        except OSError:
            return []
        return sorted(nomes, key=lambda n: os.path.getmtime(os.path.join(self.pasta, n)))

    def apagar(self, alerta, dados=None):
        for nome in [f'{alerta}.json', f'{alerta}.mp4'] + [q['arquivo'] for q in (dados or {}).get('quadros', [])]:
            try:
                os.remove(self.caminho_arquivo(nome))
            except OSError:
                pass

    def podar(self, agora=None):
        """Velho demais ou pasta cheia: vai embora o mais antigo."""
        agora = agora or time.time()
        try:
            arquivos = sorted((os.path.join(self.pasta, n) for n in os.listdir(self.pasta)), key=os.path.getmtime)
        except OSError:
            return
        total = sum(os.path.getsize(a) for a in arquivos)
        for arquivo in arquivos:
            if total <= FILA_BYTES_MAX and agora - os.path.getmtime(arquivo) <= FILA_DIAS_MAX * 86400:
                break
            total -= os.path.getsize(arquivo)
            try:
                os.remove(arquivo)
            except OSError:
                pass

    def enviar(self, portal, quantos=ENVIOS_POR_VEZ):
        """Sobe os mais antigos. Erro de rede: fica para a próxima. Portal recusou de vez: apaga."""
        import requests
        with self.trava:
            self.podar()
            for nome in self.pendentes()[:quantos]:
                caminho = os.path.join(self.pasta, nome)
                try:
                    with open(caminho, encoding='utf-8') as arquivo:
                        dados = json.load(arquivo)
                except (OSError, ValueError):
                    try:
                        os.remove(caminho)
                    except OSError:
                        pass
                    continue
                video = self.caminho(dados['alerta'], '.mp4')
                if not os.path.exists(video):
                    self.apagar(dados['alerta'], dados)
                    continue
                try:
                    resposta = portal.clipe(dados, video, [(q['segundo'], self.caminho_arquivo(q['arquivo'])) for q in dados.get('quadros', [])])
                except requests.RequestException as erro:
                    log.warning('Vídeo do alerta %s não subiu: %s', dados['alerta'], type(erro).__name__)
                    return
                if resposta.status_code == 200 or (resposta.status_code in (400, 404) and _descartar(resposta)):
                    self.apagar(dados['alerta'], dados)
                    log.info('Vídeo do alerta %s: %s', dados['alerta'], 'enviado' if resposta.status_code == 200 else 'recusado pelo portal')


def _descartar(resposta):
    try:
        return bool(resposta.json().get('descartar'))
    except ValueError:
        return False


@dataclass
class _Estado:
    memorias: dict = field(default_factory=dict)
