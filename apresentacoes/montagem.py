"""Transforma o roteiro da IA em slides desenhados no template.

Cada slide do roteiro diz o layout e o conteúdo; aqui ele ganha posição,
tamanho, cor e animação. Os lugares do layout do template (`slot`: rótulo,
título, subtítulo, botão, paginação) são preenchidos; o que não tem conteúdo
sai (botão sem texto leva junto a pílula). O conteúdo em si (tópicos, passos,
tela anotada…) é desenhado dentro da `area_conteudo` do layout.

O tamanho de fonte sai de uma estimativa pela largura média dos caracteres:
o editor refaz a medida exata no navegador (`autoajuste`), mas a exportação
logo depois da geração já precisa caber.
"""
import copy
import math
from html import escape

from . import formato
from .formato import novo_id

AREA_PADRAO = {'x': 150, 'y': 300, 'w': 1620, 'h': 590}
PAPEL_DO_LAYOUT = {
    'capa': ('capa',), 'secao': ('secao', 'capa'), 'encerramento': ('encerramento', 'capa'),
    'numero_destaque': ('quadro', 'conteudo'), 'citacao': ('quadro', 'conteudo'),
}
TRANSICAO_DO_LAYOUT = {'capa': 'zoom', 'secao': 'empurrar-esquerda', 'encerramento': 'fade'}


# ---------------------------------------------------------------------------
# Medidas de texto
# ---------------------------------------------------------------------------
def _largura_media(peso, maiusculas):
    base = 0.53 + (int(peso) - 400) / 100 * 0.018
    return base * (1.22 if maiusculas else 1.0)


def estimar_linhas(texto, tamanho, largura, peso=400, espacamento=0.0, maiusculas=False):
    por_caractere = tamanho * _largura_media(peso, maiusculas) + espacamento
    cabem = max(1, int(largura / max(1.0, por_caractere)))
    total = 0
    for paragrafo in (texto or '').split('\n'):
        palavras = paragrafo.split()
        if not palavras:
            total += 1
            continue
        linha = 0
        linhas = 1
        for palavra in palavras:
            tamanho_palavra = len(palavra)
            if linha and linha + 1 + tamanho_palavra > cabem:
                linhas += 1
                linha = tamanho_palavra
            else:
                linha += (1 if linha else 0) + tamanho_palavra
            while linha > cabem:            # palavra maior que a linha quebra
                linhas += 1
                linha -= cabem
        total += linhas
    return total


def ajustar_tamanho(texto, largura, altura, maximo, minimo=14, peso=400, entrelinha=1.2, espacamento=0.0,
                    maiusculas=False, preenchimento=0):
    largura_util = max(10, largura - 2 * preenchimento)
    altura_util = max(10, altura - 2 * preenchimento)
    tamanho = maximo
    while tamanho > minimo:
        linhas = estimar_linhas(texto, tamanho, largura_util, peso, espacamento * tamanho / maximo, maiusculas)
        if linhas * tamanho * entrelinha <= altura_util:
            return tamanho
        tamanho -= 2 if tamanho > 40 else 1
    return minimo


# ---------------------------------------------------------------------------
# Construtores
# ---------------------------------------------------------------------------
def _anim(tipo='fade', duracao=600, atraso=0, gatilho='auto'):
    return {'tipo': tipo, 'duracao': duracao, 'atraso': atraso, 'gatilho': gatilho}


def texto(x, y, w, h, html, *, tamanho=32, peso=400, cor='tema:texto', papel='texto', alinhamento='left',
          vertical='top', entrelinha=1.25, espacamento=0, maiusculas=False, fundo='', raio=0, preenchimento=0,
          autoajuste=True, animacao=None, slot='', italico=False, sombra=''):
    return {
        'id': novo_id('e'), 'tipo': 'texto', 'slot': slot, 'x': round(x, 1), 'y': round(y, 1),
        'w': round(max(w, 10), 1), 'h': round(max(h, 10), 1), 'rotacao': 0, 'opacidade': 1, 'bloqueado': False,
        'link': '', 'html': html, 'autoajuste': autoajuste, 'animacao': animacao or _anim('nenhuma'),
        'estilo': {
            'fonte': '', 'papel': papel, 'tamanho': tamanho, 'peso': peso, 'italico': italico, 'sublinhado': False,
            'tachado': False, 'maiusculas': maiusculas, 'cor': cor, 'alinhamento': alinhamento, 'vertical': vertical,
            'entrelinha': entrelinha, 'espacamento': espacamento, 'fundo': fundo,
            'borda': {'cor': '', 'largura': 0, 'estilo': 'solid'}, 'raio': raio, 'preenchimento': preenchimento,
            'sombra': sombra,
        },
    }


def forma(tipo, x, y, w, h, *, preenchimento='', borda_cor='', borda_largura=0, borda_estilo='solid', raio=0,
          sombra='', animacao=None, slot='', gradiente=''):
    return {
        'id': novo_id('e'), 'tipo': 'forma', 'slot': slot, 'forma': tipo, 'x': round(x, 1), 'y': round(y, 1),
        'w': round(max(w, 1), 1), 'h': round(max(h, 1), 1), 'rotacao': 0, 'opacidade': 1, 'bloqueado': False,
        'link': '', 'preenchimento': preenchimento, 'gradiente': gradiente,
        'borda': {'cor': borda_cor, 'largura': borda_largura, 'estilo': borda_estilo}, 'raio': raio,
        'sombra': sombra, 'animacao': animacao or _anim('nenhuma'),
    }


def icone(nome, x, y, w, h, *, cor='#FFFFFF', animacao=None):
    return {
        'id': novo_id('e'), 'tipo': 'icone', 'slot': '', 'icone': nome, 'x': round(x, 1), 'y': round(y, 1),
        'w': round(w, 1), 'h': round(h, 1), 'rotacao': 0, 'opacidade': 1, 'bloqueado': False, 'link': '',
        'cor': cor, 'fundo': '', 'raio': 0, 'animacao': animacao or _anim('nenhuma'),
    }


def imagem(midia, x, y, w, h, *, ajuste='cover', raio=24, sombra='forte', animacao=None, borda_cor=''):
    return {
        'id': novo_id('e'), 'tipo': 'imagem', 'slot': 'imagem', 'x': round(x, 1), 'y': round(y, 1),
        'w': round(w, 1), 'h': round(h, 1), 'rotacao': 0, 'opacidade': 1, 'bloqueado': False, 'link': '',
        'src': midia.url, 'midia': midia.pk, 'ajuste': ajuste, 'foco': {'x': 0.5, 'y': 0.5}, 'raio': raio,
        'borda': {'cor': borda_cor, 'largura': 2 if borda_cor else 0, 'estilo': 'solid'}, 'sombra': sombra,
        'filtro': '', 'alt': midia.nome or '', 'animacao': animacao or _anim('fade', 700),
    }


def _e(valor):
    return escape(str(valor or '').strip(), quote=False)


def _escuro(tema):
    cor = ((tema or {}).get('cores') or {}).get('fundo') or '#000000'
    try:
        r, g, b = int(cor[1:3], 16), int(cor[3:5], 16), int(cor[5:7], 16)
    except (ValueError, IndexError):
        return True
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 140


def _cartao(tema, x, y, w, h, animacao=None):
    escuro = _escuro(tema)
    return forma('retangulo-arredondado', x, y, w, h,
                 preenchimento='rgba(255,255,255,0.07)' if escuro else 'rgba(0,0,0,0.04)',
                 borda_cor='rgba(255,72,206,0.38)' if escuro else 'rgba(0,0,0,0.10)', borda_largura=2, raio=28,
                 animacao=animacao)


def _cor_destaque(tema):
    return ((tema or {}).get('cores') or {}).get('destaque') or formato.TEMA_PADRAO['cores']['destaque']


def _icone_valido(nome, padrao='fa-solid fa-circle-check'):
    nome = (nome or '').strip()
    if nome.startswith('fa-') and ' ' not in nome:
        nome = f'fa-solid {nome}'
    return nome if formato._RE_ICONE.match(nome) else padrao


def _encaixar(largura_img, altura_img, x, y, w, h):
    """Retângulo onde a imagem cabe inteira (contain) dentro da caixa, centralizada."""
    if not largura_img or not altura_img:
        return x, y, w, h
    escala = min(w / largura_img, h / altura_img)
    lw, lh = largura_img * escala, altura_img * escala
    return x + (w - lw) / 2, y + (h - lh) / 2, lw, lh


# ---------------------------------------------------------------------------
# Layouts do template
# ---------------------------------------------------------------------------
def escolher_layout(template_doc, tipo_ia):
    layouts = (template_doc or {}).get('slides') or []
    preferencias = PAPEL_DO_LAYOUT.get(tipo_ia, ('conteudo', 'quadro'))
    for papel in preferencias + ('conteudo', 'livre'):
        for layout in layouts:
            if layout.get('layout') == papel:
                return copy.deepcopy(layout)
    if layouts:
        return copy.deepcopy(layouts[0])
    return {'id': novo_id('s'), 'layout': 'livre', 'fundo': {'cor': 'tema:fundo', 'gradiente': '', 'imagem': '',
                                                             'ajuste': 'cover'},
            'transicao': {'tipo': 'fade', 'duracao': 700}, 'notas': '', 'narracao': None, 'oculto': False,
            'elementos': [texto(150, 100, 1620, 120, 'Título', tamanho=72, peso=800, papel='titulo', slot='titulo'),
                          forma('retangulo', 150, 300, 1620, 590, slot='area_conteudo')]}


def area_do_layout(layout):
    for el in layout.get('elementos') or []:
        if el.get('slot') == 'area_conteudo':
            return {'x': el['x'], 'y': el['y'], 'w': el['w'], 'h': el['h']}
    return dict(AREA_PADRAO)


def _preencher_slots(layout, item, tema, tipo_ia):
    destaque = _cor_destaque(tema)
    grande = tipo_ia in ('capa', 'secao', 'encerramento')
    titulo = (item.get('titulo') or '').strip()
    segunda = (item.get('titulo_destaque') or '').strip()
    subtitulo = (item.get('subtitulo') or '').strip()
    if grande and not subtitulo:
        subtitulo = (item.get('texto') or '').strip()
    botao = (item.get('botao') or '').strip()
    rotulo = (item.get('rotulo') or '').strip()
    tem_slot_destaque = any(el.get('slot') == 'titulo_destaque' for el in layout.get('elementos') or [])

    elementos = []
    for el in layout.get('elementos') or []:
        slot = el.get('slot') or ''
        if el.get('tipo') == 'apagar' or slot == 'area_conteudo':
            continue
        if slot == 'rotulo':
            if not rotulo:
                continue
            el['html'] = _e(rotulo)
        elif slot == 'titulo':
            if grande and segunda and not tem_slot_destaque:
                el['html'] = f'{_e(titulo)}<br><span style="color: {destaque}">{_e(segunda)}</span>'
            elif segunda and not tem_slot_destaque:
                el['html'] = f'{_e(titulo)} <span style="color: {destaque}">{_e(segunda)}</span>'
            else:
                el['html'] = _e(titulo)
            _ajustar_elemento(el, f'{titulo}\n{segunda}' if grande and segunda else f'{titulo} {segunda}'.strip())
        elif slot == 'titulo_destaque':
            if not segunda:
                continue
            el['html'] = _e(segunda)
        elif slot == 'subtitulo':
            if not subtitulo:
                continue
            el['html'] = _e(subtitulo)
            _ajustar_elemento(el, subtitulo)
        elif slot == 'texto':
            if not item.get('texto'):
                continue
            el['html'] = formato.texto_para_html(item['texto'])
            _ajustar_elemento(el, item['texto'])
        elif slot == 'botao':
            if not botao:
                continue
            if el.get('tipo') == 'texto':
                el['html'] = _e(botao)
        elif slot == 'imagem':
            continue                        # a imagem do conteúdo é desenhada pelo layout de conteúdo
        el['id'] = novo_id('e')
        elementos.append(el)
    layout['elementos'] = elementos


def _ajustar_elemento(el, conteudo):
    estilo = el.get('estilo') or {}
    maximo = estilo.get('tamanho') or 40
    estilo['tamanho'] = ajustar_tamanho(
        conteudo, el['w'], el['h'], maximo, minimo=max(12, int(maximo * 0.4)), peso=estilo.get('peso', 400),
        entrelinha=estilo.get('entrelinha', 1.2), espacamento=estilo.get('espacamento', 0),
        maiusculas=estilo.get('maiusculas', False), preenchimento=estilo.get('preenchimento', 0))


# ---------------------------------------------------------------------------
# Conteúdo por tipo de layout
# ---------------------------------------------------------------------------
def _intro(elementos, item, area, tema):
    """Texto de abertura no topo da área, quando o slide tem texto além dos itens."""
    corpo = (item.get('texto') or '').strip()
    if not corpo:
        return area
    altura = 96 if len(corpo) > 110 else 64
    tamanho = ajustar_tamanho(corpo, area['w'], altura, 30, 18, peso=500, entrelinha=1.3)
    elementos.append(texto(area['x'], area['y'], area['w'], altura, formato.texto_para_html(corpo), tamanho=tamanho,
                           peso=500, cor='tema:texto_suave', entrelinha=1.3, animacao=_anim('fade', 500)))
    return {'x': area['x'], 'y': area['y'] + altura + 28, 'w': area['w'], 'h': area['h'] - altura - 28}


def _topicos(item, area, tema, elementos, **_):
    area = _intro(elementos, item, area, tema)
    itens = [i for i in (item.get('itens') or []) if (i or {}).get('titulo')][:6]
    if not itens:
        return
    n = len(itens)
    colunas = 1 if n <= 3 else 2
    linhas = math.ceil(n / colunas)
    vao = 28
    cw = (area['w'] - vao * (colunas - 1)) / colunas
    ch = min(230, (area['h'] - vao * (linhas - 1)) / linhas)
    for i, it in enumerate(itens):
        cx = area['x'] + (i % colunas) * (cw + vao)
        cy = area['y'] + (i // colunas) * (ch + vao)
        entrada = _anim('subir', 550, 0, 'auto')
        elementos.append(_cartao(tema, cx, cy, cw, ch, animacao=entrada))
        bola = min(84, ch - 48)
        bx, by = cx + 30, cy + (ch - bola) / 2
        elementos.append(forma('circulo', bx, by, bola, bola, preenchimento='tema:primaria', sombra='brilho',
                               animacao=_anim('zoom', 450, 0, 'junto')))
        elementos.append(icone(_icone_valido(it.get('icone')), bx + bola * 0.24, by + bola * 0.24, bola * 0.52,
                               bola * 0.52, animacao=_anim('zoom', 450, 0, 'junto')))
        tx = bx + bola + 30
        tw = cx + cw - tx - 30
        corpo = (it.get('texto') or '').strip()
        titulo_h = 50 if corpo else ch - 48
        t_tam = ajustar_tamanho(it['titulo'], tw, titulo_h, 34, 20, peso=800, entrelinha=1.15)
        elementos.append(texto(tx, cy + (26 if corpo else 24), tw, titulo_h, _e(it['titulo']), tamanho=t_tam,
                               peso=800, papel='titulo', entrelinha=1.15, vertical='top' if corpo else 'middle',
                               animacao=_anim('fade', 450, 0, 'junto')))
        if corpo:
            altura = ch - 26 - titulo_h - 30
            tam = ajustar_tamanho(corpo, tw, altura, 26, 16, peso=500, entrelinha=1.3)
            elementos.append(texto(tx, cy + 26 + titulo_h + 6, tw, altura, formato.texto_para_html(corpo),
                                   tamanho=tam, peso=500, cor='tema:texto_suave', entrelinha=1.3,
                                   animacao=_anim('fade', 450, 0, 'junto')))


def _passos(item, area, tema, elementos, imagem_midia=None, **_):
    passos = [i for i in (item.get('itens') or []) if (i or {}).get('titulo')][:8]
    if imagem_midia is not None:
        lado = {'x': area['x'] + area['w'] * 0.46, 'y': area['y'], 'w': area['w'] * 0.54, 'h': area['h']}
        ix, iy, iw, ih = _encaixar(imagem_midia.largura, imagem_midia.altura, lado['x'], lado['y'], lado['w'],
                                   lado['h'])
        elementos.append(imagem(imagem_midia, ix, iy, iw, ih, ajuste='contain', raio=18,
                                borda_cor='rgba(255,255,255,0.18)', animacao=_anim('fade', 700)))
        area = {'x': area['x'], 'y': area['y'], 'w': area['w'] * 0.43, 'h': area['h']}
        _passos_verticais(passos, area, tema, elementos, colunas=1)
        return
    area = _intro(elementos, item, area, tema)
    if not passos:
        return
    if len(passos) <= 4 and area['w'] > 1000:
        _passos_horizontais(passos, area, tema, elementos)
    else:
        _passos_verticais(passos, area, tema, elementos, colunas=1 if len(passos) <= 4 else 2)


def _passos_horizontais(passos, area, tema, elementos):
    n = len(passos)
    vao = 36
    cw = (area['w'] - vao * (n - 1)) / n
    bola = 96
    centro_y = area['y'] + bola / 2
    for i in range(n - 1):
        x0 = area['x'] + i * (cw + vao) + cw / 2 + bola / 2 + 14
        x1 = area['x'] + (i + 1) * (cw + vao) + cw / 2 - bola / 2 - 14
        elementos.append(forma('linha', x0, centro_y - 3, max(10, x1 - x0), 6, borda_cor='tema:primaria',
                               borda_largura=4, borda_estilo='dashed', animacao=_anim('revelar', 400, 0, 'auto')))
    for i, passo in enumerate(passos):
        cx = area['x'] + i * (cw + vao)
        bx = cx + cw / 2 - bola / 2
        entrada = _anim('zoom', 450, 0, 'auto')
        elementos.append(forma('circulo', bx, area['y'], bola, bola, preenchimento='tema:primaria', sombra='brilho',
                               animacao=entrada))
        elementos.append(texto(bx, area['y'], bola, bola, f'{i + 1:02d}', tamanho=36, peso=900, papel='titulo',
                               alinhamento='center', vertical='middle', autoajuste=False,
                               animacao=_anim('zoom', 450, 0, 'junto')))
        corpo = (passo.get('texto') or '').strip()
        ty = area['y'] + bola + 28
        t_tam = ajustar_tamanho(passo['titulo'], cw, 96, 34, 20, peso=800, entrelinha=1.15)
        elementos.append(texto(cx, ty, cw, 96, _e(passo['titulo']), tamanho=t_tam, peso=800, papel='titulo',
                               alinhamento='center', entrelinha=1.15, animacao=_anim('fade', 400, 0, 'junto')))
        if corpo:
            altura = area['y'] + area['h'] - (ty + 110)
            tam = ajustar_tamanho(corpo, cw, altura, 26, 16, peso=500, entrelinha=1.3)
            elementos.append(texto(cx, ty + 110, cw, altura, formato.texto_para_html(corpo), tamanho=tam, peso=500,
                                   cor='tema:texto_suave', alinhamento='center', entrelinha=1.3,
                                   animacao=_anim('fade', 400, 0, 'junto')))


def _passos_verticais(passos, area, tema, elementos, colunas=1):
    if not passos:
        return
    linhas = math.ceil(len(passos) / colunas)
    vao_x, vao_y = 40, 18
    cw = (area['w'] - vao_x * (colunas - 1)) / colunas
    ch = min(150, (area['h'] - vao_y * (linhas - 1)) / linhas)
    for i, passo in enumerate(passos):
        cx = area['x'] + (i % colunas) * (cw + vao_x)
        cy = area['y'] + (i // colunas) * (ch + vao_y)
        bola = min(64, ch - 16)
        elementos.append(forma('circulo', cx, cy + 4, bola, bola, preenchimento='tema:primaria', sombra='brilho',
                               animacao=_anim('zoom', 400, 0, 'auto')))
        elementos.append(texto(cx, cy + 4, bola, bola, f'{i + 1}', tamanho=int(bola * 0.45), peso=900,
                               papel='titulo', alinhamento='center', vertical='middle', autoajuste=False,
                               animacao=_anim('zoom', 400, 0, 'junto')))
        tx, tw = cx + bola + 22, cw - bola - 22
        corpo = (passo.get('texto') or '').strip()
        titulo_h = min(46, ch) if corpo else ch
        t_tam = ajustar_tamanho(passo['titulo'], tw, titulo_h, 30, 18, peso=800, entrelinha=1.15)
        elementos.append(texto(tx, cy + 6, tw, titulo_h, _e(passo['titulo']), tamanho=t_tam, peso=800,
                               papel='titulo', entrelinha=1.15, animacao=_anim('fade', 400, 0, 'junto')))
        if corpo:
            altura = ch - titulo_h - 10
            tam = ajustar_tamanho(corpo, tw, altura, 24, 14, peso=500, entrelinha=1.28)
            elementos.append(texto(tx, cy + 6 + titulo_h + 2, tw, altura, formato.texto_para_html(corpo), tamanho=tam,
                                   peso=500, cor='tema:texto_suave', entrelinha=1.28,
                                   animacao=_anim('fade', 400, 0, 'junto')))


def _lista_html(itens):
    partes = []
    for it in itens:
        titulo = _e(it.get('titulo'))
        corpo = _e(it.get('texto'))
        partes.append(f'<li><b>{titulo}</b>' + (f' — {corpo}' if corpo else '') + '</li>')
    return '<ul>' + ''.join(partes) + '</ul>'


def _imagem_texto(item, area, tema, elementos, imagem_midia=None, **_):
    itens = [i for i in (item.get('itens') or []) if (i or {}).get('titulo')][:6]
    corpo = (item.get('texto') or '').strip()
    if imagem_midia is None:
        # Sem imagem, o slide vira tópicos: a informação não se perde.
        _topicos(item, area, tema, elementos)
        return
    iw_caixa = area['w'] * 0.52
    ix0 = area['x'] + area['w'] - iw_caixa
    captura = imagem_midia.origem in ('CAPTURA', 'PRINT')
    if captura:
        ix, iy, iw, ih = _encaixar(imagem_midia.largura, imagem_midia.altura, ix0, area['y'], iw_caixa, area['h'] - 50)
        elementos.append(imagem(imagem_midia, ix, iy, iw, ih, ajuste='contain', raio=16,
                                borda_cor='rgba(255,255,255,0.18)'))
    else:
        ix, iy, iw, ih = ix0, area['y'], iw_caixa, area['h'] - 50
        elementos.append(imagem(imagem_midia, ix, iy, iw, ih, ajuste='cover', raio=28))
    legenda = ((item.get('imagem') or {}).get('legenda') or '').strip()
    if legenda:
        elementos.append(texto(ix, iy + ih + 12, iw, 38, _e(legenda), tamanho=20, peso=500, cor='tema:texto_suave',
                               alinhamento='center', animacao=_anim('fade', 400, 0, 'junto')))
    tw = area['w'] - iw_caixa - 60
    y = area['y']
    if corpo:
        altura = 150 if itens else area['h']
        tam = ajustar_tamanho(corpo, tw, altura, 34, 18, peso=500, entrelinha=1.35)
        elementos.append(texto(area['x'], y, tw, altura, formato.texto_para_html(corpo), tamanho=tam, peso=500,
                               cor='tema:texto_suave', entrelinha=1.35, animacao=_anim('fade', 500)))
        y += altura + 24
    if itens:
        altura = area['y'] + area['h'] - y
        conteudo = '\n'.join(f"{i['titulo']} — {i.get('texto') or ''}" for i in itens)
        tam = ajustar_tamanho(conteudo, tw - 40, altura, 30, 16, peso=500, entrelinha=1.4)
        elementos.append(texto(area['x'], y, tw, altura, _lista_html(itens), tamanho=tam, peso=500,
                               entrelinha=1.4, animacao=_anim('subir', 500)))


def _tela_anotada(item, area, tema, elementos, imagem_midia=None, **_):
    if imagem_midia is None:
        _topicos(item, area, tema, elementos)
        return
    marcacoes = [m for m in (item.get('marcacoes') or []) if isinstance(m, dict) and m.get('texto')][:6]
    largura_img = area['w'] * (0.64 if marcacoes else 1.0)
    ix, iy, iw, ih = _encaixar(imagem_midia.largura, imagem_midia.altura, area['x'], area['y'], largura_img, area['h'])
    elementos.append(imagem(imagem_midia, ix, iy, iw, ih, ajuste='contain', raio=14,
                            borda_cor='rgba(255,255,255,0.2)', animacao=_anim('fade', 600)))
    if not marcacoes:
        return
    bola = 56
    for i, marca in enumerate(marcacoes):
        mx = ix + formato.numero(marca.get('x'), 0.5, 0, 1, 4) * iw - bola / 2
        my = iy + formato.numero(marca.get('y'), 0.5, 0, 1, 4) * ih - bola / 2
        elementos.append(forma('circulo', mx, my, bola, bola, preenchimento='tema:destaque', borda_cor='#0B0612',
                               borda_largura=3, sombra='brilho', animacao=_anim('zoom', 400, 0, 'auto')))
        elementos.append(texto(mx, my, bola, bola, str(i + 1), tamanho=26, peso=900, cor='#0B0612', papel='titulo',
                               alinhamento='center', vertical='middle', autoajuste=False,
                               animacao=_anim('zoom', 400, 0, 'junto')))
    lx = area['x'] + largura_img + 44
    lw = area['x'] + area['w'] - lx
    ch = min(120, (area['h'] - 14 * (len(marcacoes) - 1)) / len(marcacoes))
    for i, marca in enumerate(marcacoes):
        ly = area['y'] + i * (ch + 14)
        elementos.append(forma('circulo', lx, ly + 4, 44, 44, preenchimento='tema:destaque',
                               animacao=_anim('fade', 300, 0, 'auto')))
        elementos.append(texto(lx, ly + 4, 44, 44, str(i + 1), tamanho=22, peso=900, cor='#0B0612', papel='titulo',
                               alinhamento='center', vertical='middle', autoajuste=False,
                               animacao=_anim('fade', 300, 0, 'junto')))
        tam = ajustar_tamanho(marca['texto'], lw - 62, ch, 26, 15, peso=600, entrelinha=1.25)
        elementos.append(texto(lx + 62, ly, lw - 62, ch, _e(marca['texto']), tamanho=tam, peso=600,
                               entrelinha=1.25, animacao=_anim('fade', 300, 0, 'junto')))


def _duas_colunas(item, area, tema, elementos, **_):
    area = _intro(elementos, item, area, tema)
    colunas = [c for c in (item.get('colunas') or []) if (c or {}).get('titulo')][:2]
    if not colunas:
        _topicos(item, area, tema, elementos)
        return
    vao = 40
    cw = (area['w'] - vao * (len(colunas) - 1)) / len(colunas)
    for i, coluna in enumerate(colunas):
        cx = area['x'] + i * (cw + vao)
        elementos.append(_cartao(tema, cx, area['y'], cw, area['h'], animacao=_anim('subir', 550, 0, 'auto')))
        t_tam = ajustar_tamanho(coluna['titulo'], cw - 80, 70, 40, 22, peso=800)
        elementos.append(texto(cx + 40, area['y'] + 34, cw - 80, 70, _e(coluna['titulo']), tamanho=t_tam, peso=800,
                               papel='titulo', cor='tema:destaque', animacao=_anim('fade', 400, 0, 'junto')))
        itens = [str(x) for x in (coluna.get('itens') or []) if str(x).strip()][:6]
        if itens:
            altura = area['h'] - 150
            tam = ajustar_tamanho('\n'.join(itens), cw - 120, altura, 30, 16, peso=500, entrelinha=1.45)
            html = '<ul>' + ''.join(f'<li>{_e(x)}</li>' for x in itens) + '</ul>'
            elementos.append(texto(cx + 40, area['y'] + 124, cw - 80, altura, html, tamanho=tam, peso=500,
                                   entrelinha=1.45, animacao=_anim('fade', 400, 0, 'junto')))


def _numero(item, area, tema, elementos, **_):
    numero = item.get('numero') or {}
    valor = (numero.get('valor') or '').strip()
    if not valor:
        _topicos(item, area, tema, elementos)
        return
    tam = ajustar_tamanho(valor, area['w'], area['h'] * 0.48, 220, 60, peso=900, entrelinha=1.0)
    elementos.append(texto(area['x'], area['y'], area['w'], area['h'] * 0.48, _e(valor), tamanho=tam, peso=900,
                           papel='titulo', cor='tema:destaque', alinhamento='center', vertical='bottom',
                           entrelinha=1.0, sombra='brilho', animacao=_anim('zoom', 700)))
    legenda = (numero.get('legenda') or '').strip()
    y = area['y'] + area['h'] * 0.5
    if legenda:
        elementos.append(texto(area['x'], y, area['w'], 70, _e(legenda), tamanho=44, peso=800, papel='titulo',
                               alinhamento='center', animacao=_anim('subir', 500)))
        y += 90
    corpo = (item.get('texto') or '').strip()
    if corpo:
        altura = area['y'] + area['h'] - y
        tam = ajustar_tamanho(corpo, area['w'] * 0.8, altura, 30, 16, peso=500, entrelinha=1.35)
        elementos.append(texto(area['x'] + area['w'] * 0.1, y, area['w'] * 0.8, altura, formato.texto_para_html(corpo),
                               tamanho=tam, peso=500, cor='tema:texto_suave', alinhamento='center', entrelinha=1.35,
                               animacao=_anim('fade', 500)))


def _tabela(item, area, tema, elementos, **_):
    area = _intro(elementos, item, area, tema)
    dados = item.get('tabela') or {}
    cabecalho = [str(c) for c in (dados.get('cabecalho') or [])][:6]
    linhas = [[str(c) for c in (linha or [])][:6] for linha in (dados.get('linhas') or [])][:8]
    if not (cabecalho or linhas):
        _topicos(item, area, tema, elementos)
        return
    todas = ([cabecalho] if cabecalho else []) + linhas
    altura = min(area['h'], 86 * len(todas))
    tam = max(16, min(30, int(altura / max(1, len(todas)) * 0.36)))
    escuro = _escuro(tema)
    elementos.append({
        'id': novo_id('e'), 'tipo': 'tabela', 'slot': '', 'x': area['x'], 'y': area['y'], 'w': area['w'],
        'h': altura, 'rotacao': 0, 'opacidade': 1, 'bloqueado': False, 'link': '', 'linhas': todas,
        'cabecalho': bool(cabecalho), 'animacao': _anim('fade', 600),
        'estilo': {'fonte': '', 'tamanho': tam, 'cor': 'tema:texto', 'fundo_cabecalho': 'tema:primaria',
                   'cor_cabecalho': '#FFFFFF',
                   'fundo_linhas': 'rgba(255,255,255,0.05)' if escuro else 'rgba(0,0,0,0.03)',
                   'fundo_alternado': 'rgba(255,255,255,0.10)' if escuro else 'rgba(0,0,0,0.06)',
                   'borda': 'rgba(255,255,255,0.18)' if escuro else 'rgba(0,0,0,0.12)', 'alinhamento': 'left',
                   'raio': 16},
    })


def _citacao(item, area, tema, elementos, **_):
    frase = (item.get('texto') or item.get('titulo_destaque') or '').strip()
    if not frase:
        return
    elementos.append(icone('fa-solid fa-quote-left', area['x'] + 20, area['y'] + 10, 90, 90, cor='tema:primaria',
                           animacao=_anim('zoom', 500)))
    tam = ajustar_tamanho(frase, area['w'] - 240, area['h'] - 150, 60, 26, peso=700, entrelinha=1.25)
    elementos.append(texto(area['x'] + 120, area['y'] + 40, area['w'] - 240, area['h'] - 150, f'“{_e(frase)}”',
                           tamanho=tam, peso=700, papel='titulo', italico=True, alinhamento='center',
                           vertical='middle', entrelinha=1.25, animacao=_anim('fade', 800)))
    autor = (item.get('subtitulo') or '').strip()
    if autor:
        elementos.append(texto(area['x'], area['y'] + area['h'] - 90, area['w'] - 120, 60, f'— {_e(autor)}',
                               tamanho=30, peso=600, cor='tema:destaque', alinhamento='right',
                               animacao=_anim('fade', 500)))


CONSTRUTORES = {
    'topicos': _topicos, 'passo_a_passo': _passos, 'imagem_texto': _imagem_texto, 'tela_anotada': _tela_anotada,
    'duas_colunas': _duas_colunas, 'numero_destaque': _numero, 'tabela': _tabela, 'citacao': _citacao,
}


# ---------------------------------------------------------------------------
# Montagem
# ---------------------------------------------------------------------------
def montar_slide(item, template_doc, tema, imagem_midia=None, slide_id=None):
    tipo = item.get('layout') if item.get('layout') in CONSTRUTORES or item.get('layout') in PAPEL_DO_LAYOUT else 'topicos'
    layout = escolher_layout(template_doc, tipo)
    area = area_do_layout(layout)
    _preencher_slots(layout, item, tema, tipo)
    conteudo = []
    construtor = CONSTRUTORES.get(tipo)
    if construtor:
        construtor(item, area, tema, conteudo, imagem_midia=imagem_midia)
    elif imagem_midia is not None and tipo in ('capa', 'secao', 'encerramento') and imagem_midia.origem == 'IA_IMAGEM':
        # Capa com ilustração pedida: vai à direita, onde o template não tem texto.
        conteudo.append(imagem(imagem_midia, 1040, 140, 760, 700, ajuste='cover', raio=36))
    # Conteúdo antes da paginação (que fica por cima de tudo).
    paginacao = [el for el in layout['elementos'] if el.get('slot') == 'paginacao']
    outros = [el for el in layout['elementos'] if el.get('slot') != 'paginacao']
    layout['elementos'] = outros + conteudo + paginacao
    layout['id'] = slide_id or novo_id('s')
    layout['nome'] = (item.get('titulo') or '')[:120]
    layout['layout'] = layout.get('layout') or 'livre'
    layout['notas'] = (item.get('notas') or '').strip()
    layout['narracao'] = None
    layout['oculto'] = False
    layout['transicao'] = {'tipo': TRANSICAO_DO_LAYOUT.get(tipo, 'fade'), 'duracao': 700}
    return layout


def montar_documento(roteiro, template, imagens_por_slide=None):
    """Documento completo e saneado. `imagens_por_slide`: {índice do slide: Midia}."""
    template_doc = (template.documento if template else None) or {}
    tema = copy.deepcopy(template_doc.get('tema') or formato.TEMA_PADRAO)
    imagens_por_slide = imagens_por_slide or {}
    slides = []
    for indice, item in enumerate(roteiro.get('slides') or []):
        if isinstance(item, dict):
            slides.append(montar_slide(item, template_doc, tema, imagens_por_slide.get(indice)))
    return formato.sanear_documento({'tema': tema, 'slides': slides})
