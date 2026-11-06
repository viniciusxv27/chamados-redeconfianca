"""Acha pessoas, celulares e bolsas na imagem com o YOLOX (licença Apache 2.0), rodando em ONNX.

Escolhe sozinho o mais forte que o computador da loja aguenta:
- placa de vídeo (qualquer uma com DirectX 12, inclusive a integrada da Intel/AMD) pelo DirectML;
  sem ela, o processador;
- o YOLOX-s em 640 px (enxerga aparelho pequeno e gente longe) quando cabe no tempo; senão o
  YOLOX-tiny em 416 px, o de antes.

Só caixas: não há reconhecimento de rosto nem de quem é a pessoa.
"""
import logging
import os
import threading
import time

import cv2
import numpy as np
import onnxruntime

log = logging.getLogger('agente')

CLASSES = {0: 'person', 24: 'backpack', 26: 'handbag', 28: 'suitcase', 63: 'laptop', 67: 'cell phone'}
TAMANHO = 416
CONFIANCA_MINIMA = 0.25
SOBREPOSICAO = 0.45
PASSOS = (8, 16, 32)
# Do mais forte para o mais leve. O forte só fica se cada análise couber neste tempo.
MODELOS = (('yolox_s.onnx', 'YOLOX-s'), ('yolox_tiny.onnx', 'YOLOX-tiny'))
LIMITE_MS_FORTE = 170
RAPIDO_MS = 45            # com isso as câmeras comuns analisam 2 quadros por segundo


def _grades(tamanho):
    grades, passos = [], []
    for passo in PASSOS:
        lado = tamanho // passo
        x, y = np.meshgrid(np.arange(lado), np.arange(lado))
        grades.append(np.stack((x, y), 2).reshape(-1, 2))
        passos.append(np.full((lado * lado, 1), passo))
    return np.concatenate(grades).astype(np.float32), np.concatenate(passos).astype(np.float32)


def _nms(caixas, notas):
    """Tira as caixas repetidas sobre o mesmo objeto. `caixas` em x1, y1, x2, y2."""
    ordem = notas.argsort()[::-1]
    ficam = []
    while ordem.size:
        i = ordem[0]
        ficam.append(i)
        resto = ordem[1:]
        x1, y1 = np.maximum(caixas[i, 0], caixas[resto, 0]), np.maximum(caixas[i, 1], caixas[resto, 1])
        x2, y2 = np.minimum(caixas[i, 2], caixas[resto, 2]), np.minimum(caixas[i, 3], caixas[resto, 3])
        comum = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        areas = (caixas[:, 2] - caixas[:, 0]) * (caixas[:, 3] - caixas[:, 1])
        ordem = resto[comum / (areas[i] + areas[resto] - comum + 1e-6) <= SOBREPOSICAO]
    return ficam


def provedores():
    """Placa de vídeo primeiro (DirectML), processador sempre como reserva."""
    disponiveis = onnxruntime.get_available_providers()
    return [p for p in ('DmlExecutionProvider', 'CPUExecutionProvider') if p in disponiveis] or ['CPUExecutionProvider']


class Detector:
    def __init__(self, modelo, tamanho=None, usar_gpu=True, nome=''):
        opcoes = onnxruntime.SessionOptions()
        opcoes.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        # O computador da loja também é usado para outras coisas: no máximo metade dos núcleos.
        opcoes.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
        lista = provedores() if usar_gpu else ['CPUExecutionProvider']
        if 'DmlExecutionProvider' in lista:
            opcoes.enable_mem_pattern = False        # exigência do DirectML
            opcoes.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        self.sessao = onnxruntime.InferenceSession(modelo, opcoes, providers=lista)
        entrada = self.sessao.get_inputs()[0]
        self.entrada = entrada.name
        forma = entrada.shape
        self.tamanho = tamanho or (forma[2] if len(forma) == 4 and isinstance(forma[2], int) else TAMANHO)
        self.grade, self.passo = _grades(self.tamanho)
        self.trava = threading.Lock()       # as câmeras dividem o mesmo modelo
        self.nome = nome or os.path.basename(modelo)
        self.gpu = self.sessao.get_providers()[0] == 'DmlExecutionProvider'
        self.ms = 0.0

    @property
    def rapido(self):
        return 0 < self.ms <= RAPIDO_MS

    @property
    def descricao(self):
        return f'{self.nome} {self.tamanho}px · {"placa de vídeo (DirectML)" if self.gpu else "processador"} · {self.ms:.0f} ms por imagem'

    def medir(self, vezes=4):
        """Milissegundos por análise, numa imagem do tamanho da câmera (a primeira rodada não conta)."""
        quadro = np.random.randint(0, 255, (480, 704, 3), dtype=np.uint8)
        self(quadro)
        inicio = time.perf_counter()
        for _ in range(vezes):
            self(quadro)
        self.ms = (time.perf_counter() - inicio) * 1000 / vezes
        return self.ms

    def __call__(self, quadro):
        altura, largura = quadro.shape[:2]
        escala = min(self.tamanho / altura, self.tamanho / largura)
        menor = cv2.resize(quadro, (int(largura * escala), int(altura * escala)))
        tela = np.full((self.tamanho, self.tamanho, 3), 114, dtype=np.uint8)
        tela[:menor.shape[0], :menor.shape[1]] = menor
        tensor = np.ascontiguousarray(tela.transpose(2, 0, 1)[None], dtype=np.float32)
        with self.trava:
            saida = self.sessao.run(None, {self.entrada: tensor})[0][0]
        centro = (saida[:, :2] + self.grade) * self.passo / escala
        lados = np.exp(saida[:, 2:4]) * self.passo / escala
        caixas = np.concatenate((centro - lados / 2, centro + lados / 2), axis=1)
        deteccoes = []
        for indice, classe in CLASSES.items():
            notas = saida[:, 4] * saida[:, 5 + indice]
            boas = notas >= CONFIANCA_MINIMA
            if not boas.any():
                continue
            caixas_boas, notas_boas = caixas[boas], notas[boas]
            for i in _nms(caixas_boas, notas_boas):
                x1, y1, x2, y2 = caixas_boas[i]
                x1, y1 = max(0.0, float(x1)), max(0.0, float(y1))
                x2, y2 = min(float(largura), float(x2)), min(float(altura), float(y2))
                deteccoes.append({'classe': classe, 'conf': float(notas_boas[i]),
                                  'caixa': (x1 / largura, y1 / altura, (x2 - x1) / largura, (y2 - y1) / altura)})
        return deteccoes


def escolher(pasta, limite_ms=LIMITE_MS_FORTE):
    """O detector mais forte que cabe neste computador. Levanta exceção se nenhum abrir."""
    ultimo_erro = None
    candidatos = [(os.path.join(pasta, arquivo), nome) for arquivo, nome in MODELOS if os.path.exists(os.path.join(pasta, arquivo))]
    for posicao, (caminho, nome) in enumerate(candidatos):
        for usar_gpu in (True, False):
            try:
                detector = Detector(caminho, usar_gpu=usar_gpu, nome=nome)
                ms = detector.medir()
            except Exception as erro:  # noqa: BLE001 — DirectML sem placa compatível, DLL faltando
                ultimo_erro = erro
                log.warning('%s (%s) não abriu: %s', nome, 'placa de vídeo' if usar_gpu else 'processador', erro)
                continue
            ultimo = posicao == len(candidatos) - 1
            if ms <= limite_ms or ultimo:
                log.info('Análise: %s', detector.descricao)
                return detector
            log.info('%s levou %.0f ms: pesado para este computador', nome, ms)
            break               # já rodou no melhor jeito possível (placa de vídeo, se havia): passa ao mais leve
    raise RuntimeError(f'Nenhum modelo de análise abriu: {ultimo_erro}')
