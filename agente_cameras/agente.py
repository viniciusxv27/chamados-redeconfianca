"""Agente da loja: lê as câmeras na rede local, roda a análise e conversa com o portal.

    python agente.py config.json

Só faz chamadas de saída para o portal: não precisa abrir porta no roteador da loja.
"""
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import platform
import random
import shutil
import subprocess
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

VERSAO = '3.1'
SEGUNDOS_ENTRE_SAUDES = 60
SEGUNDOS_ENTRE_ENVIOS = 10
SEGUNDOS_PARA_TER_IMAGEM = 20         # câmera sem quadro novo há mais que isso está "sem imagem"
ESPERA_MAXIMA_SEM_PORTAL = 120
SEGUNDOS_ENTRE_TENTATIVAS_DE_ATUALIZAR = 6 * 3600
TAREFA_ATUALIZAR = 'Agente de Cameras - atualizar'
SEM_JANELA = 0x08000000
TAMANHO_DO_LOG = 6000
SEGUNDOS_ENTRE_LOGS = 120
SEGUNDOS_POR_QUADRO_NA_PORTA = 0.33   # a pessoa cruza a porta em 1 a 3 s: um quadro por segundo perde gente
EVENTOS_POR_ENVIO = 200
EVENTOS_NA_FILA = 5000
SEGUNDOS_ENTRE_DESCOBERTAS = 600
SEGUNDOS_SEM_QUADRO = 300
SEGUNDOS_ENTRE_QUADROS = 1.0
SEGUNDOS_ENTRE_QUADROS_RAPIDO = 0.5   # com placa de vídeo (ou computador forte), 2 análises por segundo
SEGUNDOS_ENTRE_ULTIMOS = 0.5          # a imagem guardada para o mosaico do portal
TIMEOUT = 15
LARGURA_FOTO = 640
LARGURA_FOTO_FOCO = 1280
log = logging.getLogger('agente')


class Relogio:
    """O relógio do portal. Hora errada no computador da loja vira medição na hora errada e alerta falso de loja fechada."""

    def __init__(self):
        self.diferenca = 0.0          # segundos a somar ao relógio deste computador

    def acertar(self, texto_iso):
        try:
            portal = datetime.fromisoformat(str(texto_iso))
        except (TypeError, ValueError):
            return
        if portal.tzinfo is None:
            return
        diferenca = (portal - datetime.now(portal.tzinfo)).total_seconds()
        # Menos de 5 s é a demora da própria resposta: não mexe.
        self.diferenca = diferenca if abs(diferenca) > 5 else 0.0

    def agora(self):
        return datetime.now().astimezone() + timedelta(seconds=self.diferenca)


RELOGIO = Relogio()


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
        self.token = token
        self._local = threading.local()

    @property
    def sessao(self):
        """Uma sessão por thread: as câmeras, os envios e o sinal falam com o portal ao mesmo tempo."""
        sessao = getattr(self._local, 'sessao', None)
        if sessao is None:
            sessao = self._local.sessao = requests.Session()
            sessao.headers['Authorization'] = f'Bearer {self.token}'
        return sessao

    @sessao.setter
    def sessao(self, valor):
        self._local.sessao = valor

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

    def alerta(self, canal, evento, foto, grava_video=False):
        dados = {'canal': canal, 'tipo': evento.tipo, 'confianca': evento.confianca, 'motivo': evento.motivo,
                 'detalhes': json.dumps(getattr(evento, 'detalhes', None) or {}), 'video': '1' if grava_video else ''}
        arquivos = {'foto': ('alerta.jpg', foto, 'image/jpeg')} if foto else None
        r = self.sessao.post(self.base + 'alerta/', data=dados, files=arquivos, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    def clipe(self, dados, video, quadros):
        """Sobe os 3 minutos do alerta. Devolve a resposta (quem chama decide o que fazer com o status)."""
        campos = {k: dados[k] for k in ('alerta', 'inicio', 'segundos', 'evento_segundo') if k in dados}
        abertos = []
        try:
            arquivos = [('video', ('clipe.mp4', open(video, 'rb'), 'video/mp4'))]
            abertos.append(arquivos[0][1][1])
            for segundo, caminho in quadros:
                if os.path.exists(caminho):
                    arquivo = open(caminho, 'rb')
                    abertos.append(arquivo)
                    arquivos.append((f'quadro_{"m" if segundo < 0 else ""}{abs(segundo)}', ('quadro.jpg', arquivo, 'image/jpeg')))
            # Vídeo de alguns MB numa internet de loja: tempo de sobra para subir.
            return self.sessao.post(self.base + 'clipe/', data=campos, files=arquivos, timeout=(TIMEOUT, 600))
        finally:
            for arquivo in abertos:
                arquivo.close()


def endereco_rtsp(gravador, canal):
    usuario = quote(gravador['usuario'], safe='')
    senha = quote(gravador['senha'], safe='')
    caminho = gravador['caminho'].replace('{canal}', str(canal))
    return f"rtsp://{usuario}:{senha}@{gravador['ip']}:{gravador['porta']}{caminho}"


def loja_aberta(abre, fecha, agora=None):
    atual = (agora or RELOGIO.agora()).strftime('%H:%M')
    return abre <= atual < fecha


def _minutos(hhmm):
    try:
        h, m = str(hhmm).split(':')[:2]
        return int(h) * 60 + int(m)
    except ValueError:
        return 0


def na_tolerancia(abre, fecha, tolerancia, agora=None):
    """Na margem antes de abrir ou depois de fechar: gente ali é a equipe abrindo ou fechando a loja."""
    momento = agora or RELOGIO.agora()
    atual, margem = momento.hour * 60 + momento.minute, int(tolerancia or 0)
    return _minutos(abre) - margem <= atual < _minutos(abre) or _minutos(fecha) <= atual < _minutos(fecha) + margem


def carregar_detector(pasta):
    """O detector mais forte que o computador aguenta. Sem nenhum, o agente segue mandando imagem.

    Devolve (detector, aviso para o portal).
    """
    try:
        from detector import escolher
        return escolher(pasta), ''
    except Exception as erro:  # noqa: BLE001 — o ONNX falha de vários jeitos num computador sem as DLLs
        log.warning('Análise desligada: %s', erro)
        return None, f'Análise desligada neste computador: {type(erro).__name__}'


def criar_memoria(numero, montador):
    """A memória de vídeo da câmera (para os 3 minutos dos alertas), se o PyAV abrir neste computador."""
    if montador is None:
        return None
    try:
        from gravacao import Memoria
        return Memoria(numero, montador)
    except Exception as erro:  # noqa: BLE001
        log.warning('Canal %s sem gravação dos alertas: %s', numero, type(erro).__name__)
        return None


def _jpg(quadro, maximo=LARGURA_FOTO, qualidade=70):
    import cv2
    altura, largura = quadro.shape[:2]
    if largura > maximo:
        quadro = cv2.resize(quadro, (maximo, int(altura * maximo / largura)))
    ok, dados = cv2.imencode('.jpg', quadro, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
    return dados.tobytes() if ok else None


class Camera(threading.Thread):
    """Uma por canal: lê o vídeo, guarda os últimos 2 minutos (para os alertas) e analisa 1 ou 2 quadros por segundo."""

    def __init__(self, numero, url, portal, detector, estado, fila=None, montador=None):
        super().__init__(daemon=True)
        self.numero, self.url, self.portal, self.detector, self.estado = numero, url, portal, detector, estado
        self.fila = fila
        self.metricas = Metricas()
        self.ultimo = None
        self.ultimo_quadro_em = time.monotonic()
        self.analisador = Analisador()
        self.parar = threading.Event()
        self.memoria = criar_memoria(numero, montador)
        self.quadros = self.reconexoes = 0
        self.erro = ''
        self._medida = (time.monotonic(), 0)

    def saude(self, agora):
        """Como está esta câmera agora: tem imagem, quadros por segundo, quantas vezes reconectou."""
        desde, antes = self._medida
        fps = (self.quadros - antes) / max(1.0, agora - desde)
        self._medida = (agora, self.quadros)
        idade = agora - self.ultimo_quadro_em
        return {'ok': self.ultimo is not None and idade < SEGUNDOS_PARA_TER_IMAGEM, 'idade': round(idade, 1),
                'fps': round(fps, 1), 'reconexoes': self.reconexoes, 'erro': self.erro, 'gravando': self.memoria is not None}

    def _intervalo(self):
        zonas = (self.estado.get('canais', {}).get(self.numero) or {}).get('zonas') or []
        if zonas_do_tipo(zonas, ZONA_PORTA):
            return SEGUNDOS_POR_QUADRO_NA_PORTA
        return SEGUNDOS_ENTRE_QUADROS_RAPIDO if getattr(self.detector, 'rapido', False) else SEGUNDOS_ENTRE_QUADROS

    def run(self):
        # Nada derruba a câmera de vez: erro inesperado registra, espera e tenta de novo.
        while not self.parar.is_set():
            try:
                if self.memoria is not None:
                    self._rodar_com_memoria()
                else:
                    self._rodar_opencv()
            except Exception as erro:  # noqa: BLE001
                self.erro = type(erro).__name__
                log.exception('Canal %s caiu; tento de novo em 10 s', self.numero)
                self.parar.wait(10)

    def _rodar_com_memoria(self):
        """Uma conexão só: o mesmo pacote vai para a memória de vídeo (cópia) e para a análise (decodificado)."""
        import av
        while not self.parar.is_set():
            try:
                conteiner = av.open(self.url, options={'rtsp_transport': 'tcp', 'timeout': '10000000',
                                                       'analyzeduration': '1000000', 'probesize': '1000000'}, timeout=15)
            except Exception as erro:  # noqa: BLE001 — câmera fora do ar, senha trocada
                self.erro, self.reconexoes = type(erro).__name__, self.reconexoes + 1
                log.warning('Canal %s não abriu (%s); tento de novo em 30 s', self.numero, type(erro).__name__)
                self.parar.wait(30)
                continue
            try:
                entrada = conteiner.streams.video[0]
                entrada.thread_type = 'AUTO'
                self.memoria.nova_entrada(entrada)
                proxima = ultimo_guardado = 0.0
                for pacote in conteiner.demux(entrada):
                    if self.parar.is_set():
                        break
                    agora = time.monotonic()
                    quadros = pacote.decode() if pacote.size else []
                    self.memoria.pacote(pacote, agora)
                    for quadro in quadros:
                        self.ultimo_quadro_em, self.quadros, self.erro = agora, self.quadros + 1, ''
                        analisar = agora >= proxima
                        if analisar or agora - ultimo_guardado >= SEGUNDOS_ENTRE_ULTIMOS or self.memoria.quer_quadro(agora):
                            imagem = quadro.to_ndarray(format='bgr24')
                            self.ultimo, ultimo_guardado = imagem, agora
                            self.memoria.quadro(imagem, agora)
                            if analisar:
                                proxima = agora + self._intervalo()
                                self.analisar(imagem, agora)
                    self.memoria.conferir(agora)
            except Exception as erro:  # noqa: BLE001 — queda no meio do vídeo: reconecta
                self.erro, self.reconexoes = type(erro).__name__, self.reconexoes + 1
                log.warning('Canal %s parou (%s); reconectando', self.numero, type(erro).__name__)
                self.parar.wait(5)
            finally:
                self.memoria.nova_entrada(None)
                conteiner.close()

    def _rodar_opencv(self):
        import cv2
        while not self.parar.is_set():
            captura = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not captura.isOpened():
                self.erro, self.reconexoes = 'sem conexão', self.reconexoes + 1
                log.warning('Canal %s não abriu; tento de novo em 30 s', self.numero)
                self.parar.wait(30)
                continue
            proxima = 0.0
            while not self.parar.is_set():
                ok, quadro = captura.read()
                if not ok:
                    break
                self.ultimo, self.quadros, self.erro = quadro, self.quadros + 1, ''
                agora = self.ultimo_quadro_em = time.monotonic()
                if agora >= proxima:
                    proxima = agora + self._intervalo()
                    self.analisar(quadro, agora)
            captura.release()

    def analisar(self, quadro, agora):
        config = self.estado.get('canais', {}).get(self.numero)
        if not config or not config['ia'] or self.detector is None:
            return
        self.analisador.zonas = zonas_do_tipo(config['zonas'], ZONA_MOSTRUARIO)
        # O que o portal aprendeu com as decisões de furto confirmado / falso alarme desta câmera.
        self.analisador.ajustes = (self.estado.get('ajustes') or {}).get(str(self.numero)) or {}
        momento = RELOGIO.agora()
        aberta = loja_aberta(self.estado['abre_as'], self.estado['fecha_as'], momento)
        # Na tolerância do horário a equipe está abrindo ou fechando: para os alertas, a loja conta como aberta.
        tolerancia = na_tolerancia(self.estado['abre_as'], self.estado['fecha_as'], self.estado.get('tolerancia', 0), momento)
        deteccoes = self.detector(quadro)
        _medir(self, deteccoes, config['zonas'], agora, aberta, momento)
        ligados = self.estado.get('alertas') or {}
        for evento in self.analisador.analisar(deteccoes, agora, aberta or tolerancia):
            if ligados.get(evento.tipo, True):
                self.avisar(evento, quadro, agora)

    def avisar(self, evento, quadro, agora):
        """Manda o alerta com a foto; se o portal quiser, guarda o pedido dos 3 minutos em volta."""
        try:
            resposta = self.portal.alerta(self.numero, evento, _jpg(quadro, LARGURA_FOTO_FOCO, 80),
                                          grava_video=self.memoria is not None)
            log.info('Alerta %s no canal %s', evento.tipo, self.numero)
        except (requests.RequestException, ValueError) as erro:
            log.warning('Alerta não enviado: %s', type(erro).__name__)
            return
        if self.memoria is not None and resposta.get('quer_video') and resposta.get('alerta'):
            from gravacao import Pedido
            video = self.estado.get('video') or {}
            self.memoria.pedir(Pedido(alerta=int(resposta['alerta']), canal=self.numero, evento=agora,
                                      momento=RELOGIO.agora(), antes=int(resposta.get('antes') or video.get('antes') or 90),
                                      depois=int(resposta.get('depois') or video.get('depois') or 90),
                                      acao=int((getattr(evento, 'detalhes', None) or {}).get('acao_segundo') or 0)))


def _medir(camera, deteccoes, zonas, agora, aberta, momento=None):
    if camera.fila is None:
        return
    m = camera.metricas
    matinal = camera.estado.get('matinal') or {}
    m.zonas = zonas
    m.matinal_inicio = matinal.get('inicio', m.matinal_inicio)
    m.matinal_fim = matinal.get('fim', m.matinal_fim)
    m.matinal_minimo = matinal.get('minimo', m.matinal_minimo)
    pessoas = [d['caixa'] for d in deteccoes if d['classe'] == 'person' and d['conf'] >= 0.5]
    momento = momento or RELOGIO.agora()
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


def iniciar_gravacao(pasta):
    """A fila dos vídeos dos alertas e o montador. Sem o PyAV, o agente segue só com a foto. Devolve (fila, montador)."""
    try:
        from gravacao import FilaDeClipes, Montador
        import av  # noqa: F401 — confere que as DLLs do vídeo abrem neste computador
        clipes = FilaDeClipes(os.path.join(pasta, 'fila_clipes'))
        montador = Montador(clipes)
        montador.start()
        return clipes, montador
    except Exception as erro:  # noqa: BLE001
        log.warning('Gravação dos alertas desligada: %s', erro)
        return None, None


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


class Envios(threading.Thread):
    """Medições e vídeos dos alertas sobem aqui, fora do sinal: um vídeo grande numa internet de loja não pode
    deixar a loja "sem contato" no portal enquanto sobe."""

    def __init__(self, portal, fila, clipes):
        super().__init__(daemon=True, name='envios')
        self.portal, self.fila, self.clipes = portal, fila, clipes
        self.acordar = threading.Event()
        self.ligado = threading.Event()
        self.falhas = 0

    def run(self):
        while True:
            self.acordar.wait(SEGUNDOS_ENTRE_ENVIOS)
            self.acordar.clear()
            if not self.ligado.is_set():
                continue
            try:
                self.fila.enviar(self.portal)
                if self.clipes is not None:
                    self.clipes.enviar(self.portal)
                self.falhas = 0
            except requests.RequestException as erro:
                self.falhas += 1
                log.warning('Envio das medições/vídeos falhou: %s', type(erro).__name__)
                time.sleep(min(300, 15 * self.falhas))
            except Exception:  # noqa: BLE001 — nunca para de tentar
                log.exception('Envio das medições/vídeos falhou')
                time.sleep(30)


def _versao(texto):
    try:
        return tuple(int(p) for p in str(texto or '').split('.')[:3])
    except ValueError:
        return ()


def _rodar_windows(*comando):
    return subprocess.run(comando, capture_output=True, text=True, errors='replace',
                          creationflags=SEM_JANELA if os.name == 'nt' else 0)


class Atualizador:
    """Baixa a versão publicada no portal, confere tamanho e SHA-256 e instala sem ninguém na frente do computador.

    O instalador roda numa tarefa à parte do Windows: ele para a tarefa do agente, e um processo filho do agente
    morreria junto. Falhou? Espera SEGUNDOS_ENTRE_TENTATIVAS_DE_ATUALIZAR antes de tentar a mesma versão.
    """

    def __init__(self, pasta, http=requests, rodar=_rodar_windows, em_thread=True):
        self.pasta, self.http, self.rodar, self.em_thread = pasta, http, rodar, em_thread
        self.tentativas = {}
        self.ocupado = False
        self.situacao = ''

    def talvez(self, info):
        if not isinstance(info, dict) or self.ocupado:
            return False
        versao = str(info.get('versao') or '')
        if not _versao(versao) or _versao(versao) <= _versao(VERSAO) or not info.get('url') or not info.get('sha256'):
            return False
        ultima = self.tentativas.get(versao)
        if ultima is not None and time.monotonic() - ultima < SEGUNDOS_ENTRE_TENTATIVAS_DE_ATUALIZAR:
            return False
        self.tentativas[versao] = time.monotonic()
        self.ocupado = True
        if self.em_thread:
            threading.Thread(target=self._atualizar, args=(info,), daemon=True, name='atualizar').start()
        else:
            self._atualizar(info)
        return True

    def _atualizar(self, info):
        try:
            caminho = self.baixar(info)
            self.instalar(caminho)
            self.situacao = f'instalando a versão {info["versao"]}'
            log.info('Atualização %s baixada e conferida; instalador iniciado', info['versao'])
        except Exception as erro:  # noqa: BLE001 — tenta de novo mais tarde
            self.situacao = f'atualização {info.get("versao")} falhou: {type(erro).__name__}: {str(erro)[:80]}'
            log.warning('Atualização falhou: %s', self.situacao)
        finally:
            self.ocupado = False

    def baixar(self, info):
        url = str(info['url'])
        if not url.startswith('https://') and '://localhost' not in url and '://127.0.0.1' not in url:
            raise ValueError('endereço da atualização sem https')
        pasta = os.path.join(self.pasta, 'atualizacao')
        os.makedirs(pasta, exist_ok=True)
        destino = os.path.join(pasta, f'AgenteCameras-{info["versao"]}.exe')
        temporario = destino + '.parcial'
        resumo, tamanho = hashlib.sha256(), 0
        with self.http.get(url, stream=True, timeout=(15, 120)) as resposta:
            resposta.raise_for_status()
            with open(temporario, 'wb') as arquivo:
                for pedaco in resposta.iter_content(1024 * 1024):
                    arquivo.write(pedaco)
                    resumo.update(pedaco)
                    tamanho += len(pedaco)
        esperado = int(info.get('tamanho') or 0)
        if esperado and tamanho != esperado:
            os.remove(temporario)
            raise ValueError(f'tamanho {tamanho} em vez de {esperado}')
        if resumo.hexdigest() != str(info['sha256']).lower():
            os.remove(temporario)
            raise ValueError('impressão digital (SHA-256) não confere')
        os.replace(temporario, destino)
        return destino

    def instalar(self, caminho):
        if os.name != 'nt':
            raise RuntimeError('a atualização automática só roda no Windows')
        hora = (datetime.now() + timedelta(minutes=2)).strftime('%H:%M')
        r = self.rodar('schtasks', '/Create', '/F', '/TN', TAREFA_ATUALIZAR, '/SC', 'ONCE', '/ST', hora,
                       '/RU', 'SYSTEM', '/RL', 'HIGHEST', '/TR', f'"{caminho}" /S')
        if r.returncode != 0:
            raise RuntimeError(f'tarefa de atualização não criada: {(r.stderr or r.stdout or "").strip()[:120]}')
        self.rodar('schtasks', '/Run', '/TN', TAREFA_ATUALIZAR)


def limpar_atualizacoes(pasta):
    """Na subida do agente novo: tira a tarefa de uma vez só e os instaladores já usados."""
    if os.name == 'nt':
        _rodar_windows('schtasks', '/Delete', '/TN', TAREFA_ATUALIZAR, '/F')
    shutil.rmtree(os.path.join(pasta, 'atualizacao'), ignore_errors=True)


def manter_acordado():
    """O computador da loja não pode dormir com o agente rodando (sem mexer no plano de energia de ninguém)."""
    if os.name != 'nt':
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)   # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
    except Exception:  # noqa: BLE001
        pass


def montar_saude(cameras, detector, fila, clipes, pasta, inicio, atualizador=None):
    """O retrato que vai no sinal: o portal mostra na linha da loja e avisa o que precisa de atenção."""
    agora = time.monotonic()
    dados = {
        'cameras': {str(n): c.saude(agora) for n, c in sorted(cameras.items())},
        'analise': detector is not None, 'detector': getattr(detector, 'descricao', '') if detector else '',
        'fila_medicoes': len(fila.eventos), 'fila_clipes': len(clipes.pendentes()) if clipes is not None else None,
        'ligado_ha_min': int((agora - inicio) / 60), 'relogio_diferenca_s': round(-RELOGIO.diferenca, 1),
        'windows': platform.platform()[:60], 'versao': VERSAO,
    }
    try:
        dados['disco_livre_gb'] = round(shutil.disk_usage(pasta).free / 1024 ** 3, 1)
    except OSError:
        pass
    try:
        import psutil
        processo = psutil.Process()
        dados['cpu'] = round(psutil.cpu_percent(interval=None))
        dados['memoria_mb'] = round(processo.memory_info().rss / 1024 ** 2)
    except Exception:  # noqa: BLE001 — sem psutil, segue sem esses dois
        pass
    if atualizador is not None and atualizador.situacao:
        dados['atualizacao'] = atualizador.situacao
    return dados


def marcar_contato(pasta):
    """O instalador lê este arquivo para saber que o agente já conversou com o portal."""
    try:
        with open(os.path.join(pasta, 'contato.txt'), 'w', encoding='utf-8') as arquivo:
            arquivo.write(datetime.now().astimezone().isoformat())
    except OSError:
        pass


def rodar(config):
    portal = Portal(config['portal'], config['token'])
    pasta = config.get('_pasta', '.')
    inicio = time.monotonic()
    manter_acordado()
    limpar_atualizacoes(pasta)
    detector, aviso = carregar_detector(pasta) if config.get('analisar', True) else (None, '')
    estado, cameras = {}, {}
    clipes, montador = iniciar_gravacao(pasta)
    ultima_descoberta = None
    vigia, aviso_vivo = iniciar_ao_vivo(config, estado)
    aviso = aviso_vivo or aviso
    log_enviado_em = saude_enviada_em = contato_em = 0.0
    fila = FilaDeEventos(os.path.join(pasta, 'fila_medicoes.json'))
    envios = Envios(portal, fila, clipes)
    envios.start()
    atualizador = Atualizador(pasta)
    falhas = 0
    log.info('Agente %s iniciado', VERSAO)
    while True:
        espera = 30
        try:
            extras = {}
            if vigia:
                vigia.conferir()
                extras['ao_vivo'] = vigia.resumo()
            agora = time.monotonic()
            if agora - log_enviado_em > SEGUNDOS_ENTRE_LOGS or not log_enviado_em:
                log_enviado_em = agora
                extras['log'] = fim_do_log(pasta)
            if agora - saude_enviada_em > SEGUNDOS_ENTRE_SAUDES or not saude_enviada_em:
                saude_enviada_em = agora
                extras['saude'] = json.dumps(montar_saude(cameras, detector, fila, clipes, pasta, inicio, atualizador))
            resposta = portal.sinal(aviso, extras)
            if falhas:
                log.info('Portal respondeu de novo depois de %s falha(s)', falhas)
            aviso, falhas = '', 0
            if agora - contato_em > 60 or not contato_em:
                contato_em = agora
                marcar_contato(pasta)
            RELOGIO.acertar(resposta.get('agora'))
            espera = resposta.get('proximo_sinal', 30)
            if not resposta.get('ativo'):
                envios.ligado.clear()
                for camera in cameras.values():
                    camera.parar.set()
                cameras.clear()
            else:
                envios.ligado.set()
                atualizador.talvez(resposta.get('atualizacao'))
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
                                  canais={c['numero']: c for c in resposta['canais']}, ajustes=resposta.get('ajustes') or {},
                                  video=resposta.get('video') or {}, tolerancia=resposta.get('tolerancia') or 0,
                                  alertas=resposta.get('alertas') or {})
                    for numero in estado['canais']:
                        url = endereco_rtsp(gravador, numero)
                        if numero in cameras and (cameras[numero].url != url or not cameras[numero].is_alive()):
                            # Endereço mudou, ou a thread morreu: troca por uma nova.
                            cameras.pop(numero).parar.set()
                        if numero not in cameras:
                            cameras[numero] = Camera(numero, url, portal, detector, estado, fila, montador)
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
        except requests.RequestException as erro:
            # Só o tipo: a mensagem completa pode trazer endereço e cabeçalho.
            falhas += 1
            log.warning('Sem contato com o portal: %s', type(erro).__name__)
            # Portal lento ou fora: não insiste no mesmo ritmo, e cada loja volta num momento diferente.
            espera = min(ESPERA_MAXIMA_SEM_PORTAL, 10 * falhas) + random.uniform(0, 5)
        except Exception:  # noqa: BLE001 — o laço principal nunca morre
            falhas += 1
            log.exception('Erro inesperado no laço principal')
            espera = 30
        time.sleep(espera)


if __name__ == '__main__':
    caminho_config = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else 'config.json')
    # Rodando como tarefa do Windows não há tela: o arquivo é o único jeito de saber o que houve.
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', handlers=[
        RotatingFileHandler(os.path.join(os.path.dirname(caminho_config), 'agente.log'), maxBytes=1_000_000, backupCount=2)])
    with open(caminho_config, encoding='utf-8') as arquivo:
        rodar({**json.load(arquivo), '_pasta': os.path.dirname(caminho_config)})
