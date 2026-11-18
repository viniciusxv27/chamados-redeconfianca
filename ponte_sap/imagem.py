"""Quadro da janela → pedaços PNG que mudaram. Só biblioteca padrão.

A tela do app quase não muda de um quadro para o outro: em vez de mandar a imagem
inteira, divide em faixas horizontais e manda só as faixas diferentes da última vez.
"""
import struct
import zlib

FAIXA = 48            # altura de cada faixa, em pixels


def _png(largura, altura, bgra):
    """PNG RGB de bytes BGRA (de cima para baixo)."""
    rgba = bytearray(bgra)
    rgba[0::4], rgba[2::4] = bgra[2::4], bgra[0::4]          # BGRA → RGBA
    rgb = bytearray(largura * altura * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = rgba[0::4], rgba[1::4], rgba[2::4]
    passo = largura * 3
    cru = b''.join(b'\x00' + bytes(rgb[i:i + passo]) for i in range(0, len(rgb), passo))

    def bloco(tipo, dados):
        return struct.pack('>I', len(dados)) + tipo + dados + struct.pack('>I', zlib.crc32(tipo + dados) & 0xFFFFFFFF)

    return (b'\x89PNG\r\n\x1a\n' + bloco(b'IHDR', struct.pack('>IIBBBBB', largura, altura, 8, 2, 0, 0, 0))
            + bloco(b'IDAT', zlib.compress(cru, 3)) + bloco(b'IEND', b''))


def png_inteiro(largura, altura, bgra):
    return _png(largura, altura, bgra)


class Diferenca:
    """Guarda o último quadro e devolve só as faixas que mudaram: [(y, altura, png)]."""

    def __init__(self):
        self.tamanho = None
        self.faixas = {}

    def esquecer(self):
        self.tamanho, self.faixas = None, {}

    def mudancas(self, largura, altura, bgra, tudo=False):
        if (largura, altura) != self.tamanho:
            self.tamanho, self.faixas, tudo = (largura, altura), {}, True
        passo = largura * 4
        saida = []
        for y in range(0, altura, FAIXA):
            h = min(FAIXA, altura - y)
            pedaco = bgra[y * passo:(y + h) * passo]
            assinatura = zlib.crc32(pedaco)
            if tudo or self.faixas.get(y) != assinatura:
                self.faixas[y] = assinatura
                saida.append((y, h, _png(largura, h, pedaco)))
        return saida
