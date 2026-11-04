"""Agente da loja: lê as câmeras na rede local, roda a análise e conversa com o portal.

    python agente.py config.json

Só faz chamadas de saída para o portal: não precisa abrir porta no roteador da loja.
"""
import json
import logging
import sys
import threading
import time
import os
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from descoberta import descobrir
from regras import ZONA_MOSTRUARIO, ZONA_PORTA, Analisador, Metricas, zonas_do_tipo

VERSAO = '1.2'
SEGUNDOS_POR_QUADRO_NA_PORTA = 0.33   # a pessoa cruza a porta em 1 a 3 s: um quadro por segundo perde gente
EVENTOS_POR_ENVIO = 200
EVENTOS_NA_FILA = 5000
SEGUNDOS_ENTRE_DESCOBERTAS = 600
SEGUNDOS_SEM_QUADRO = 300
SEGUNDOS_ENTRE_QUADROS = 1.0
TIMEOUT = 15
LARGURA_FOTO = 640
log = logging.getLogger('agente')


class FilaDeEventos:
    """O que foi medido e ainda não chegou ao portal. Fica em arquivo: internet ou luz caem, a medição não some."""

    def __init__(self, caminho):
        self.caminho, self.trava = caminho, threading.Lock()
        try:
            with open(caminho, encoding='utf-8') as arquivo:
                self.eventos = json.load(arquivo)
        except (OSError, ValueError):
            self.eventos = []

    def _gravar(self):
        try:
            temporario = self.caminho + '.tmp'
            with open(temporario, 'w', encoding='utf-8') as arquivo:
                json.dump(self.eventos, arquivo)
            os.replace(temporario, self.caminho)
        except OSError as erro:
            log.warning('Fila de medições não gravada: %s', type(erro).__name__)

    def adicionar(self, eventos):
        with self.trava:
            self.eventos = (self.eventos + eventos)[-EVENTOS_NA_FILA:]
            self._gravar()

    def enviar(self, portal):
        with self.trava:
            lote = self.eventos[:EVENTOS_POR_ENVIO]
        if not lote:
            return
        portal.metricas(lote)   # se falhar, levanta e o lote fica para a próxima
        with self.trava:
            enviados = {e['chave'] for e in lote}
            self.eventos = [e for e in self.eventos if e['chave'] not in enviados]
            self._gravar()


def carimbar(eventos, agora, momento):
    """Troca o relógio interno pela data e hora de verdade, com uma chave para o portal não contar duas vezes."""
    saida = []
    for evento in eventos:
        saida.append({'chave': uuid.uuid4().hex, 'tipo': evento['tipo'], 'zona': evento['zona'],
                      'duracao': evento['duracao'], 'pessoas': evento['pessoas'],
                      'inicio': (momento - timedelta(seconds=agora - evento['inicio'])).isoformat(),
                      'fim': (momento - timedelta(seconds=agora - evento['fim'])).isoformat()})
    return saida


class Portal:
    def __init__(self, endereco, token):
        # A resposta traz a senha do gravador: fora de HTTPS, só para teste na própria máquina.
        if not endereco.startswith('https://') and '://localhost' not in endereco and '://127.0.0.1' not in endereco:
            raise SystemExit('O endereço do portal precisa ser https://')
        self.base = endereco.rstrip('/') + '/cameras/agente/'
        self.sessao = requests.Session()
        self.sessao.headers['Authorization'] = f'Bearer {token}'

    def sinal(self):
        r = self.sessao.post(self.base + 'sinal/', data={'versao': VERSAO}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def metricas(self, eventos):
        self.sessao.post(self.base + 'metricas/', json={'eventos': eventos}, timeout=TIMEOUT).raise_for_status()

    def descoberta(self, resultado):
        dados = {**resultado, 'canais': ','.join(str(c) for c in resultado.get('canais', []))}
        self.sessao.post(self.base + 'descoberta/', data=dados, timeout=TIMEOUT).raise_for_status()

    def fotos(self, fotos):
        arquivos = {f'canal_{n}': ('foto.jpg', jpg, 'image/jpeg') for n, jpg in fotos.items()}
        self.sessao.post(self.base + 'fotos/', files=arquivos, timeout=TIMEOUT).raise_for_status()

    def alerta(self, canal, evento, foto):
        dados = {'canal': canal, 'tipo': evento.tipo, 'confianca': evento.confianca, 'motivo': evento.motivo}
        arquivos = {'foto': ('alerta.jpg', foto, 'image/jpeg')} if foto else None
        self.sessao.post(self.base + 'alerta/', data=dados, files=arquivos, timeout=TIMEOUT).raise_for_status()


def endereco_rtsp(gravador, canal):
    usuario = quote(gravador['usuario'], safe='')
    senha = quote(gravador['senha'], safe='')
    caminho = gravador['caminho'].replace('{canal}', str(canal))
    return f"rtsp://{usuario}:{senha}@{gravador['ip']}:{gravador['porta']}{caminho}"


def loja_aberta(abre, fecha, agora=None):
    atual = (agora or datetime.now()).strftime('%H:%M')
    return abre <= atual < fecha


class Detector:
    """YOLO com só duas classes: pessoa e celular."""
    CLASSES = {0: 'person', 67: 'cell phone'}

    def __init__(self, modelo='yolov8n.pt'):
        from ultralytics import YOLO
        self.modelo = YOLO(modelo)
        self.trava = threading.Lock()   # as câmeras dividem o mesmo modelo

    @classmethod
    def se_instalado(cls, modelo):
        """Sem a parte de IA instalada, o agente segue mandando imagem."""
        try:
            return cls(modelo)
        except ImportError:
            log.warning('Análise desligada: pacote de IA não instalado')
            return None

    def __call__(self, quadro):
        altura, largura = quadro.shape[:2]
        saida = []
        with self.trava:
            caixas = self.modelo.predict(quadro, classes=list(self.CLASSES), verbose=False)[0].boxes
        for caixa in caixas:
            x1, y1, x2, y2 = caixa.xyxy[0].tolist()
            saida.append({'classe': self.CLASSES[int(caixa.cls[0])], 'conf': float(caixa.conf[0]),
                          'caixa': (x1 / largura, y1 / altura, (x2 - x1) / largura, (y2 - y1) / altura)})
        return saida


def _jpg(quadro):
    import cv2
    altura, largura = quadro.shape[:2]
    if largura > LARGURA_FOTO:
        quadro = cv2.resize(quadro, (LARGURA_FOTO, int(altura * LARGURA_FOTO / largura)))
    ok, dados = cv2.imencode('.jpg', quadro, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return dados.tobytes() if ok else None


class Camera(threading.Thread):
    """Uma por canal: guarda o último quadro e analisa um por segundo."""

    def __init__(self, numero, url, portal, detector, estado, fila=None):
        super().__init__(daemon=True)
        self.numero, self.url, self.portal, self.detector, self.estado = numero, url, portal, detector, estado
        self.fila = fila
        self.metricas = Metricas()
        self.ultimo = None
        self.ultimo_quadro_em = time.monotonic()
        self.analisador = Analisador()
        self.parar = threading.Event()

    def run(self):
        import cv2
        while not self.parar.is_set():
            captura = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not captura.isOpened():
                log.warning('Canal %s não abriu; tento de novo em 30 s', self.numero)
                self.parar.wait(30)
                continue
            proxima = 0.0
            while not self.parar.is_set():
                ok, quadro = captura.read()
                if not ok:
                    break
                self.ultimo = quadro
                agora = self.ultimo_quadro_em = time.monotonic()
                if agora >= proxima:
                    zonas = (self.estado.get('canais', {}).get(self.numero) or {}).get('zonas') or []
                    proxima = agora + (SEGUNDOS_POR_QUADRO_NA_PORTA if zonas_do_tipo(zonas, ZONA_PORTA) else SEGUNDOS_ENTRE_QUADROS)
                    self.analisar(quadro, agora)
            captura.release()

    def analisar(self, quadro, agora):
        config = self.estado.get('canais', {}).get(self.numero)
        if not config or not config['ia'] or self.detector is None:
            return
        self.analisador.zonas = zonas_do_tipo(config['zonas'], ZONA_MOSTRUARIO)
        aberta = loja_aberta(self.estado['abre_as'], self.estado['fecha_as'])
        deteccoes = self.detector(quadro)
        _medir(self, deteccoes, config['zonas'], agora, aberta)
        for evento in self.analisador.analisar(deteccoes, agora, aberta):
            try:
                self.portal.alerta(self.numero, evento, _jpg(quadro))
                log.info('Alerta %s no canal %s', evento.tipo, self.numero)
            except requests.RequestException as erro:
                log.warning('Alerta não enviado: %s', type(erro).__name__)


def _medir(camera, deteccoes, zonas, agora, aberta):
    if camera.fila is None:
        return
    m = camera.metricas
    matinal = camera.estado.get('matinal') or {}
    m.zonas = zonas
    m.matinal_inicio = matinal.get('inicio', m.matinal_inicio)
    m.matinal_fim = matinal.get('fim', m.matinal_fim)
    m.matinal_minimo = matinal.get('minimo', m.matinal_minimo)
    pessoas = [d['caixa'] for d in deteccoes if d['classe'] == 'person' and d['conf'] >= 0.5]
    momento = datetime.now().astimezone()
    eventos = m.analisar(pessoas, agora, momento.strftime('%H:%M'), aberta)
    if eventos:
        lista = carimbar(eventos, agora, momento)
        for evento in lista:
            evento['canal'] = camera.numero
        camera.fila.adicionar(lista)


def precisa_descobrir(gravador, cameras, ultima, agora):
    """Sem IP, ou com todas as câmeras sem imagem (o IP pode ter mudado), procura de novo, sem insistir."""
    if ultima is not None and agora - ultima < SEGUNDOS_ENTRE_DESCOBERTAS:
        return False
    if not gravador['ip']:
        return True
    return bool(cameras) and all(agora - c.ultimo_quadro_em > SEGUNDOS_SEM_QUADRO for c in cameras.values())


def rodar(config):
    portal = Portal(config['portal'], config['token'])
    detector = Detector.se_instalado(config.get('modelo', 'yolov8n.pt')) if config.get('analisar', True) else None
    estado, cameras = {}, {}
    ultima_descoberta = None
    fila = FilaDeEventos(os.path.join(config.get('_pasta', '.'), 'fila_medicoes.json'))
    while True:
        espera = 30
        try:
            resposta = portal.sinal()
            espera = resposta.get('proximo_sinal', 30)
            if not resposta.get('ativo'):
                for camera in cameras.values():
                    camera.parar.set()
                cameras.clear()
            else:
                gravador = resposta['gravador']
                if precisa_descobrir(gravador, cameras, ultima_descoberta, time.monotonic()):
                    ultima_descoberta = time.monotonic()
                    log.info('Procurando o gravador na rede')
                    achado = descobrir(gravador['usuario'], gravador['senha'], gravador.get('serial', ''),
                                       gravador['caminho'], config.get('ips', ()))
                    portal.descoberta(achado)
                    log.info('Procura: %s', achado.get('ip') or achado.get('erro'))
                    time.sleep(1)
                    continue
                if gravador['ip']:
                    estado.update(abre_as=resposta['abre_as'], fecha_as=resposta['fecha_as'], matinal=resposta.get('matinal'),
                                  canais={c['numero']: c for c in resposta['canais']})
                    for numero in estado['canais']:
                        url = endereco_rtsp(gravador, numero)
                        if numero in cameras and cameras[numero].url != url:
                            cameras.pop(numero).parar.set()
                        if numero not in cameras:
                            cameras[numero] = Camera(numero, url, portal, detector, estado, fila)
                            cameras[numero].start()
                    for numero in set(cameras) - set(estado['canais']):
                        cameras.pop(numero).parar.set()
                    pedidas = [resposta['foco']] if resposta.get('foco') else resposta.get('fotos', [])
                    fotos = {}
                    for canal_numero in pedidas:
                        camera = cameras.get(canal_numero)
                        if camera is not None and camera.ultimo is not None:
                            fotos[canal_numero] = _jpg(camera.ultimo)
                    if fotos:
                        portal.fotos(fotos)
                    fila.enviar(portal)
        except requests.RequestException as erro:
            # Só o tipo: a mensagem completa pode trazer endereço e cabeçalho.
            log.warning('Sem contato com o portal: %s', type(erro).__name__)
        time.sleep(espera)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    caminho_config = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'config.json')
    with open(caminho_config, encoding='utf-8') as arquivo:
        rodar({**json.load(arquivo), '_pasta': os.path.dirname(caminho_config)})
