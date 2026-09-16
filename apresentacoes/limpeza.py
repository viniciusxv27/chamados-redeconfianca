"""Apaga áreas de uma imagem de fundo (o texto de exemplo gravado no template).

Templates chegam como imagens com "título", "texto" e "01 / 46" desenhados
por cima do fundo. Para virar fundo de slide, esses textos precisam sumir —
e voltar como elementos editáveis.

Como funciona, sem mandar a imagem para fora:
1. dentro de cada área marcada, separa o que é "tinta" (texto, fio, contorno)
   do fundo, pelo contraste com a moldura em volta da área;
2. alarga um pouco essa máscara (antisserrilhado e brilho do texto);
3. preenche só os pixels mascarados por difusão (equação de Laplace), resolvida
   primeiro em baixa resolução e refinada — converge rápido e não borra o que
   não era texto;
4. devolve um grão parecido com o da vizinhança, para a área não ficar lisa
   demais em fundos com textura.
Se a detecção pegar quase a área inteira (fundo claro, textura forte), a área
toda é preenchida.
"""
import io

import numpy as np
from PIL import Image, ImageFilter

MARGEM_MOLDURA = 10          # px ao redor da área usados como referência do fundo
DILATACAO = 7                # tamanho (ímpar) do alargamento da máscara
COBERTURA_MAXIMA = 0.65      # acima disso, preenche a área inteira


def _luminancia(rgb):
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _mascara_de_tinta(janela, area_local):
    """Pixels da área que destoam da moldura (texto claro em fundo escuro ou o contrário)."""
    x0, y0, x1, y1 = area_local
    lum = _luminancia(janela)
    moldura = np.ones(lum.shape, dtype=bool)
    moldura[y0:y1, x0:x1] = False
    ref = lum[moldura]
    if ref.size < 16:
        ref = lum.reshape(-1)
    mediana = float(np.median(ref))
    desvio = float(np.std(ref))
    limiar = max(28.0, 2.5 * desvio)
    dentro = lum[y0:y1, x0:x1]
    diferenca = np.abs(dentro - mediana)
    # Cor muito saturada também é tinta (fio magenta num fundo roxo escuro de mesma luz).
    rgb = janela[y0:y1, x0:x1]
    saturacao = rgb.max(axis=-1) - rgb.min(axis=-1)
    sat_ref = janela[moldura]
    sat_ref = (sat_ref.max(axis=-1) - sat_ref.min(axis=-1)) if sat_ref.size else np.array([0.0])
    limiar_sat = float(np.percentile(sat_ref, 95)) + 40.0
    mascara = (diferenca > limiar) | (saturacao > limiar_sat)
    completa = np.zeros(lum.shape, dtype=bool)
    completa[y0:y1, x0:x1] = mascara
    return completa


def _dilatar(mascara, tamanho=DILATACAO):
    if tamanho < 3:
        return mascara
    img = Image.fromarray((mascara * 255).astype(np.uint8))
    img = img.filter(ImageFilter.MaxFilter(tamanho | 1))
    return np.asarray(img) > 0


def _difundir(janela, mascara, iteracoes):
    """Jacobi só nos pixels mascarados; os demais são a condição de contorno."""
    img = janela.copy()
    if not mascara.any():
        return img
    for _ in range(iteracoes):
        vizinhos = (np.roll(img, 1, 0) + np.roll(img, -1, 0) + np.roll(img, 1, 1) + np.roll(img, -1, 1)) / 4.0
        img[mascara] = vizinhos[mascara]
    return img


def _preencher(janela, mascara):
    """Difusão em pirâmide: resolve pequeno, sobe de escala e refina."""
    niveis = []
    img, msk = janela, mascara
    while min(img.shape[0], img.shape[1]) > 24 and len(niveis) < 6:
        niveis.append((img, msk))
        h, w = img.shape[0] // 2, img.shape[1] // 2
        if h < 8 or w < 8:
            break
        img = np.asarray(Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize((w, h), Image.BILINEAR),
                         dtype=np.float32)
        msk = np.asarray(Image.fromarray((msk * 255).astype(np.uint8)).resize((w, h), Image.BILINEAR)) > 0
    niveis.append((img, msk))

    solucao = None
    for indice in range(len(niveis) - 1, -1, -1):
        img, msk = niveis[indice]
        atual = img.copy()
        if solucao is not None:
            subida = np.asarray(
                Image.fromarray(np.clip(solucao, 0, 255).astype(np.uint8)).resize(
                    (img.shape[1], img.shape[0]), Image.BILINEAR), dtype=np.float32)
            atual[msk] = subida[msk]
        else:
            atual[msk] = img[~msk].mean(axis=0) if (~msk).any() else 0
        iteracoes = 400 if indice == len(niveis) - 1 else 60
        solucao = _difundir(atual, msk, iteracoes)
    return solucao


def _grao(original, mascara, preenchida, semente=7):
    """Ruído com a intensidade do grão em volta (fundo com textura não fica liso)."""
    suave = np.asarray(Image.fromarray(np.clip(original, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(2)), dtype=np.float32)
    detalhe = original - suave
    fora = ~_dilatar(mascara, 15)
    if not fora.any():
        return preenchida
    desvio = float(np.std(detalhe[fora]))
    if desvio < 0.5:
        return preenchida
    ruido = np.random.default_rng(semente).normal(0, desvio * 0.85, preenchida.shape).astype(np.float32)
    resultado = preenchida.copy()
    resultado[mascara] = preenchida[mascara] + ruido[mascara]
    return resultado


def limpar_areas(imagem, areas, automatico=True):
    """Imagem PIL (RGB) com as áreas apagadas. `areas`: [{x, y, w, h}] em px da própria imagem."""
    base = np.asarray(imagem.convert('RGB'), dtype=np.float32).copy()
    altura, largura = base.shape[0], base.shape[1]
    for area in areas or []:
        try:
            x0 = max(0, int(round(float(area['x']))))
            y0 = max(0, int(round(float(area['y']))))
            x1 = min(largura, int(round(float(area['x']) + float(area['w']))))
            y1 = min(altura, int(round(float(area['y']) + float(area['h']))))
        except (KeyError, TypeError, ValueError):
            continue
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        # Janela com moldura: a difusão precisa do fundo em volta como contorno.
        m = MARGEM_MOLDURA + DILATACAO
        jx0, jy0 = max(0, x0 - m), max(0, y0 - m)
        jx1, jy1 = min(largura, x1 + m), min(altura, y1 + m)
        janela = base[jy0:jy1, jx0:jx1]
        local = (x0 - jx0, y0 - jy0, x1 - jx0, y1 - jy0)

        mascara = _mascara_de_tinta(janela, local) if automatico else None
        area_px = (local[2] - local[0]) * (local[3] - local[1])
        if mascara is None or mascara.sum() > COBERTURA_MAXIMA * area_px or not mascara.any():
            mascara = np.zeros(janela.shape[:2], dtype=bool)
            mascara[local[1]:local[3], local[0]:local[2]] = True
        else:
            mascara = _dilatar(mascara)
        # A borda da janela nunca é incógnita (é o contorno).
        mascara[0, :] = mascara[-1, :] = False
        mascara[:, 0] = mascara[:, -1] = False

        preenchida = _preencher(janela, mascara)
        preenchida = _grao(janela, mascara, preenchida)
        base[jy0:jy1, jx0:jx1] = preenchida
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def limpar_bytes(conteudo, areas, escala_do_documento=None, formato='JPEG'):
    """Como `limpar_areas`, para bytes. `escala_do_documento`: (largura, altura) do quadro
    em que as áreas foram marcadas (1920×1080) — converte para os px reais da imagem."""
    imagem = Image.open(io.BytesIO(conteudo))
    imagem.load()
    imagem = imagem.convert('RGB')
    if escala_do_documento:
        fx = imagem.width / float(escala_do_documento[0])
        fy = imagem.height / float(escala_do_documento[1])
        areas = [{'x': a['x'] * fx, 'y': a['y'] * fy, 'w': a['w'] * fx, 'h': a['h'] * fy} for a in areas or []]
    limpa = limpar_areas(imagem, areas)
    saida = io.BytesIO()
    if formato == 'JPEG':
        limpa.save(saida, 'JPEG', quality=90, optimize=True, progressive=True)
    else:
        limpa.save(saida, formato)
    return saida.getvalue()
