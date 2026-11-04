"""Agente da loja: lê as câmeras na rede local, roda a análise e conversa com o portal.

    python agente.py config.json

Só faz chamadas de saída para o portal: não precisa abrir porta no roteador da loja.
"""
import json
import logging
from logging.handlers import RotatingFileHandler
import sys
import threading
import time
import os
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from descoberta import descobrir
from regras import ZONA_MOSTRUARIO, ZONA_PORTA, Analisador, Metricas, zonas_do_tipo

VERSAO = '2.2'
TAMANHO_DO_LOG = 6000
SEGUNDOS_ENTRE_LOGS = 120
SEGUNDOS_POR_QUADRO_NA_PORTA = 0.33   # a pessoa cruza a porta em 1 a 3 s: um quadro por segundo perde gente
EVENTOS_POR_ENVIO = 200
EVENTOS_NA_FILA = 5000
SEGUNDOS_ENTRE_DESCOBERTAS = 600
SEGUNDOS_SEM_QUADRO = 300
SEGUNDOS_ENTRE_QUADROS = 1.0
TIMEOUT = 15
LARGURA_FOTO = 640
LARGURA_FOTO_FOCO = 1280
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

    def sinal(self, aviso='', extras=None):
        r = self.sessao.post(self.base + 'sinal/', data={'versao': VERSAO, 'aviso': aviso, **(extras or {})}, timeout=TIMEOUT)
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


def carregar_detector(modelo):
    """Sem a análise, o agente segue mandando imagem. Devolve (detector, aviso para o portal)."""
    try:
        from detector import Detector
        return Detector(modelo), ''
    except Exception as erro:  # noqa: BLE001 — o ONNX falha de vários jeitos num computador sem as DLLs
        log.warning('Análise desligada: %s', erro)
        return None, f'Análise desligada neste computador: {type(erro).__name__}'


def _jpg(quadro, maximo=LARGURA_FOTO, qualidade=70):
    import cv2
    altura, largura = quadro.shape[:2]
    if largura > maximo:
        quadro = cv2.resize(quadro, (maximo, int(altura * maximo / largura)))
    ok, dados = cv2.imencode('.jpg', quadro, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
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


def iniciar_ao_vivo(config, estado):
    """Sem as peças do vídeo ao vivo, o agente segue mandando imagens. Devolve (vigia, aviso para o portal)."""
    def endereco(numero, qualidade):
        gravador = estado.get('gravador') or {}
        if not gravador.get('ip'):
            return ''
        return endereco_rtsp({**gravador, 'caminho': caminho_da_qualidade(gravador['caminho'], qualidade)}, numero)
    try:
        from ao_vivo import AoVivo, Vigia, caminho_da_qualidade
        arquivo_de_log = os.path.join(config.get('_pasta', '.'), 'agente.log')
        return Vigia(lambda: AoVivo(config['portal'], config['token'], endereco), arquivo_de_log), ''
    except Exception as erro:  # noqa: BLE001 — falta de DLL no Windows aparece aqui
        log.warning('Vídeo ao vivo desligado: %s', erro)
        return None, f'Vídeo ao vivo desligado neste computador: {type(erro).__name__}'


def fim_do_log(pasta, tamanho=TAMANHO_DO_LOG):
    """O fim do agente.log, sem senha, para dar para ver de longe o que houve na loja."""
    try:
        with open(os.path.join(pasta, 'agente.log'), 'rb') as arquivo:
            arquivo.seek(0, os.SEEK_END)
            arquivo.seek(max(0, arquivo.tell() - tamanho))
            texto = arquivo.read().decode('utf-8', 'replace')
    except OSError:
        return ''
    return re.sub(r'rtsp://[^\s\'"]*@', 'rtsp://***@', texto)


def precisa_descobrir(gravador, cameras, ultima, agora):
    """Sem IP, ou com todas as câmeras sem imagem (o IP pode ter mudado), procura de novo, sem insistir."""
    if ultima is not None and agora - ultima < SEGUNDOS_ENTRE_DESCOBERTAS:
        return False
    if not gravador['ip']:
        return True
    return bool(cameras) and all(agora - c.ultimo_quadro_em > SEGUNDOS_SEM_QUADRO for c in cameras.values())


def rodar(config):
    portal = Portal(config['portal'], config['token'])
    pasta = config.get('_pasta', '.')
    detector, aviso = carregar_detector(os.path.join(pasta, config.get('modelo', 'yolox_tiny.onnx'))) if config.get('analisar', True) else (None, '')
    estado, cameras = {}, {}
    ultima_descoberta = None
    vigia, aviso_vivo = iniciar_ao_vivo(config, estado)
    aviso = aviso_vivo or aviso
    log_enviado_em = 0.0
    fila = FilaDeEventos(os.path.join(pasta, 'fila_medicoes.json'))
    while True:
        espera = 30
        try:
            extras = {}
            if vigia:
                vigia.conferir()
                extras['ao_vivo'] = vigia.resumo()
            if time.monotonic() - log_enviado_em > SEGUNDOS_ENTRE_LOGS or not log_enviado_em:
                log_enviado_em = time.monotonic()
                extras['log'] = fim_do_log(pasta)
            resposta = portal.sinal(aviso, extras)
            aviso = ''
            espera = resposta.get('proximo_sinal', 30)
            if not resposta.get('ativo'):
                for camera in cameras.values():
                    camera.parar.set()
                cameras.clear()
            else:
                gravador = estado['gravador'] = resposta['gravador']
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
                    foco = resposta.get('foco')
                    pedidas = [foco] if foco else resposta.get('fotos', [])
                    fotos = {}
                    for canal_numero in pedidas:
                        camera = cameras.get(canal_numero)
                        if camera is not None and camera.ultimo is not None:
                            fotos[canal_numero] = _jpg(camera.ultimo, LARGURA_FOTO_FOCO, 80) if foco else _jpg(camera.ultimo)
                    if fotos:
                        portal.fotos(fotos)
                    fila.enviar(portal)
        except requests.RequestException as erro:
            # Só o tipo: a mensagem completa pode trazer endereço e cabeçalho.
            log.warning('Sem contato com o portal: %s', type(erro).__name__)
        time.sleep(espera)


if __name__ == '__main__':
    caminho_config = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'config.json')
    # Rodando como tarefa do Windows não há tela: o arquivo é o único jeito de saber o que houve.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', handlers=[
        RotatingFileHandler(os.path.join(os.path.dirname(caminho_config), 'agente.log'), maxBytes=1_000_000, backupCount=2)])
    with open(caminho_config, encoding='utf-8') as arquivo:
        rodar({**json.load(arquivo), '_pasta': os.path.dirname(caminho_config)})
