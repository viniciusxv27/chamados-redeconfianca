"""Agente da loja: lê as câmeras na rede local, roda a análise e conversa com o portal.

    python agente.py config.json

Só faz chamadas de saída para o portal: não precisa abrir porta no roteador da loja.
"""
import json
import logging
import sys
import threading
import time
from datetime import datetime
from urllib.parse import quote

import requests

from regras import Analisador

VERSAO = '1.0'
SEGUNDOS_ENTRE_QUADROS = 1.0
TIMEOUT = 15
LARGURA_FOTO = 640
log = logging.getLogger('agente')


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

    def __call__(self, quadro):
        altura, largura = quadro.shape[:2]
        saida = []
        for caixa in self.modelo.predict(quadro, classes=list(self.CLASSES), verbose=False)[0].boxes:
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

    def __init__(self, numero, url, portal, detector, estado):
        super().__init__(daemon=True)
        self.numero, self.url, self.portal, self.detector, self.estado = numero, url, portal, detector, estado
        self.ultimo = None
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
                agora = time.monotonic()
                if agora >= proxima:
                    proxima = agora + SEGUNDOS_ENTRE_QUADROS
                    self.analisar(quadro, agora)
            captura.release()

    def analisar(self, quadro, agora):
        config = self.estado.get('canais', {}).get(self.numero)
        if not config or not config['ia'] or self.detector is None:
            return
        self.analisador.zonas = config['zonas']
        aberta = loja_aberta(self.estado['abre_as'], self.estado['fecha_as'])
        for evento in self.analisador.analisar(self.detector(quadro), agora, aberta):
            try:
                self.portal.alerta(self.numero, evento, _jpg(quadro))
                log.info('Alerta %s no canal %s', evento.tipo, self.numero)
            except requests.RequestException as erro:
                log.warning('Alerta não enviado: %s', type(erro).__name__)


def rodar(config):
    portal = Portal(config['portal'], config['token'])
    detector = Detector(config.get('modelo', 'yolov8n.pt')) if config.get('analisar', True) else None
    estado, cameras = {}, {}
    while True:
        espera = 30
        try:
            resposta = portal.sinal()
            espera = resposta.get('proximo_sinal', 30)
            if not resposta.get('ativo'):
                for camera in cameras.values():
                    camera.parar.set()
                cameras.clear()
            elif not resposta['gravador']['ip']:
                log.warning('Falta o IP do gravador em Configuração, no portal')
            else:
                estado.update(abre_as=resposta['abre_as'], fecha_as=resposta['fecha_as'],
                              canais={c['numero']: c for c in resposta['canais']})
                for numero in estado['canais']:
                    if numero not in cameras:
                        cameras[numero] = Camera(numero, endereco_rtsp(resposta['gravador'], numero), portal, detector, estado)
                        cameras[numero].start()
                for numero in set(cameras) - set(estado['canais']):
                    cameras.pop(numero).parar.set()
                pedidas = [resposta['foco']] if resposta.get('foco') else resposta.get('fotos', [])
                fotos = {}
                for canal_numero in pedidas:
                    camera = next((c for n, c in cameras.items() if n == canal_numero), None)
                    if camera is not None and camera.ultimo is not None:
                        fotos[canal_numero] = _jpg(camera.ultimo)
                if fotos:
                    portal.fotos(fotos)
        except requests.RequestException as erro:
            # Só o tipo: a mensagem completa pode trazer endereço e cabeçalho.
            log.warning('Sem contato com o portal: %s', type(erro).__name__)
        time.sleep(espera)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
    with open(sys.argv[1] if len(sys.argv) > 1 else 'config.json', encoding='utf-8') as arquivo:
        rodar(json.load(arquivo))
