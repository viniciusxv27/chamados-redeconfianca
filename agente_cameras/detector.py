"""Acha pessoas e celulares na imagem com o YOLOX (licença Apache 2.0), rodando em ONNX.

Leve de propósito: cabe no instalador e dispensa os 2 GB do PyTorch, que também não dá para
embutir num arquivo só. Só caixas: não há reconhecimento de rosto nem de quem é a pessoa.
"""
import threading

import cv2
import numpy as np
import onnxruntime

CLASSES = {0: 'person', 67: 'cell phone'}
TAMANHO = 416
CONFIANCA_MINIMA = 0.25
SOBREPOSICAO = 0.45
PASSOS = (8, 16, 32)


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


class Detector:
    def __init__(self, modelo, tamanho=TAMANHO):
        opcoes = onnxruntime.SessionOptions()
        opcoes.intra_op_num_threads = 2     # o computador da loja também é usado para outras coisas
        self.sessao = onnxruntime.InferenceSession(modelo, opcoes, providers=['CPUExecutionProvider'])
        self.entrada = self.sessao.get_inputs()[0].name
        self.tamanho = tamanho
        self.grade, self.passo = _grades(tamanho)
        self.trava = threading.Lock()       # as câmeras dividem o mesmo modelo

    def __call__(self, quadro):
        altura, largura = quadro.shape[:2]
        escala = min(self.tamanho / altura, self.tamanho / largura)
        menor = cv2.resize(quadro, (int(largura * escala), int(altura * escala)))
        tela = np.full((self.tamanho, self.tamanho, 3), 114, dtype=np.uint8)
        tela[:menor.shape[0], :menor.shape[1]] = menor
        tensor = tela.transpose(2, 0, 1)[None].astype(np.float32)
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
