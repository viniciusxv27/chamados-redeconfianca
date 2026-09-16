"""Template padrão "Rede Confiança", montado a partir do material oficial (4 telas).

As imagens originais vinham com "titulo", "texto" e "01 / 46" desenhados. Os
fundos em static/apresentacoes/modelos/rede-confianca/ são essas telas já
limpas (apresentacoes/limpeza.py), e cada texto voltou como elemento editável
nas mesmas posições, com a tipografia medida no original: Montserrat Black 126
com espaçamento -7 no título da capa, Bold 25 espaçado no rótulo, ExtraBold 76
no título das telas internas, Bold 18 na paginação.
"""
import copy

NOME = 'Rede Confiança'
PASTA = 'static:apresentacoes/modelos/rede-confianca'

ROSA_ROTULO = '#FECEFB'
AMARELO = '#FEDB63'

TEMA = {
    'fonte_titulo': 'Montserrat',
    'fonte_texto': 'Montserrat',
    'cores': {
        'fundo': '#0B0612', 'superficie': '#1C1026', 'primaria': '#FF48CE', 'secundaria': '#8B3DFF',
        'destaque': AMARELO, 'texto': '#FFFFFF', 'texto_suave': '#E6D5F0',
    },
}

ESTILO = (
    'Identidade Rede Confiança (revenda Vivo): fundos escuros quase pretos com brilho neon roxo e magenta '
    'e o hexágono 3D da marca. Títulos curtos e fortes em Montserrat Black/ExtraBold; na capa e no '
    'encerramento o título tem duas linhas, a segunda em amarelo. Rótulo pequeno e espaçado em rosa claro '
    'acima do título, fio magenta, botão em pílula com contorno magenta. Textos brancos, diretos, com '
    'números e palavras-chave em amarelo. Imagens: cenas 3D com luz neon roxa e magenta, fundo escuro, '
    'iluminação dramática, visual premium, nunca com texto escrito na imagem.'
)

SEM_ANIMACAO = {'tipo': 'nenhuma', 'duracao': 600, 'atraso': 0, 'gatilho': 'auto'}


def _anim(tipo, duracao=600, atraso=0, gatilho='auto'):
    return {'tipo': tipo, 'duracao': duracao, 'atraso': atraso, 'gatilho': gatilho}


def _texto(ident, slot, x, y, w, h, html, *, tamanho, peso, cor='tema:texto', papel='texto', espacamento=0,
           entrelinha=1.2, alinhamento='left', vertical='top', maiusculas=False, autoajuste=False, animacao=None):
    return {
        'id': ident, 'tipo': 'texto', 'slot': slot, 'x': x, 'y': y, 'w': w, 'h': h, 'rotacao': 0, 'opacidade': 1,
        'bloqueado': False, 'link': '', 'html': html, 'autoajuste': autoajuste,
        'animacao': animacao or copy.deepcopy(SEM_ANIMACAO),
        'estilo': {
            'fonte': '', 'papel': papel, 'tamanho': tamanho, 'peso': peso, 'italico': False, 'sublinhado': False,
            'tachado': False, 'maiusculas': maiusculas, 'cor': cor, 'alinhamento': alinhamento,
            'vertical': vertical, 'entrelinha': entrelinha, 'espacamento': espacamento, 'fundo': '',
            'borda': {'cor': '', 'largura': 0, 'estilo': 'solid'}, 'raio': 0, 'preenchimento': 0, 'sombra': '',
        },
    }


def _forma(ident, slot, forma, x, y, w, h, *, preenchimento='', borda_cor='', borda_largura=0,
           borda_estilo='solid', raio=0, animacao=None):
    return {
        'id': ident, 'tipo': 'forma', 'slot': slot, 'forma': forma, 'x': x, 'y': y, 'w': w, 'h': h,
        'rotacao': 0, 'opacidade': 1, 'bloqueado': False, 'link': '', 'preenchimento': preenchimento,
        'gradiente': '', 'borda': {'cor': borda_cor, 'largura': borda_largura, 'estilo': borda_estilo},
        'raio': raio, 'sombra': '', 'animacao': animacao or copy.deepcopy(SEM_ANIMACAO),
    }


def _paginacao(sufixo):
    return _texto(f'e-pag{sufixo}', 'paginacao', 1570, 981, 200, 28, '{n} / {total}', tamanho=18, peso=700,
                  cor='#EFE4EF', espacamento=2.5, alinhamento='right', vertical='middle')


def _rotulo(sufixo, x, y, w=760, html='rótulo'):
    return _texto(f'e-rot{sufixo}', 'rotulo', x, y, w, 36, html, tamanho=25, peso=700, cor=ROSA_ROTULO,
                  espacamento=5, vertical='middle', animacao=_anim('fade', 500))


def _slide(ident, nome, layout, imagem, elementos):
    return {
        'id': ident, 'nome': nome, 'layout': layout,
        'fundo': {'cor': 'tema:fundo', 'gradiente': '', 'imagem': f'{PASTA}/{imagem}', 'ajuste': 'cover'},
        'transicao': {'tipo': 'fade', 'duracao': 700}, 'notas': '', 'narracao': None, 'oculto': False,
        'elementos': elementos,
    }


def titulo_duas_cores(linha1, linha2, destaque=AMARELO):
    """HTML do título em duas linhas, a segunda na cor de destaque (texto já escapado)."""
    if not linha2:
        return linha1
    return f'{linha1}<br><span style="color: {destaque}">{linha2}</span>'


def documento_rede_confianca():
    capa = _slide('s-capa0001', 'Capa', 'capa', 'capa.jpg', [
        _rotulo('capa', 148, 113),
        _forma('e-fiocapa', 'decoracao', 'retangulo', 154, 182, 395, 5, preenchimento='tema:primaria',
               animacao=_anim('revelar', 600)),
        _texto('e-titcapa', 'titulo', 134, 270, 740, 330, titulo_duas_cores('título da', 'apresentação'),
               tamanho=126, peso=900, papel='titulo', espacamento=-7, entrelinha=0.87, autoajuste=True,
               animacao=_anim('subir', 700)),
        _texto('e-subcapa', 'subtitulo', 150, 618, 740, 120, 'Subtítulo da apresentação', tamanho=34, peso=800,
               entrelinha=1.25, autoajuste=True, animacao=_anim('fade', 600)),
        _forma('e-pilcapa', 'botao', 'pilula', 156, 772, 594, 83, borda_cor='tema:primaria', borda_largura=3,
               raio=42, animacao=_anim('zoom', 500)),
        _texto('e-botcapa', 'botao', 190, 772, 540, 83, 'texto do botão', tamanho=25, peso=700, espacamento=3,
               vertical='middle', animacao=_anim('fade', 400, gatilho='junto')),
        _paginacao('capa'),
    ])
    secao = _slide('s-secao001', 'Seção', 'secao', 'conteudo.jpg', [
        _rotulo('secao', 148, 392),
        _forma('e-fiosecao', 'decoracao', 'retangulo', 154, 452, 395, 5, preenchimento='tema:primaria',
               animacao=_anim('revelar', 600)),
        _texto('e-titsecao', 'titulo', 134, 486, 1000, 330, titulo_duas_cores('nome da', 'seção'), tamanho=110,
               peso=900, papel='titulo', espacamento=-5, entrelinha=0.9, autoajuste=True,
               animacao=_anim('subir', 700)),
        _paginacao('secao'),
    ])
    conteudo = _slide('s-conteud1', 'Conteúdo', 'conteudo', 'conteudo.jpg', [
        _rotulo('cont', 148, 96, w=1200),
        _texto('e-titcont', 'titulo', 144, 160, 1520, 100, 'TÍTULO DO SLIDE', tamanho=76, peso=800,
               papel='titulo', entrelinha=1.1, maiusculas=True, autoajuste=True, animacao=_anim('fade', 600)),
        _forma('e-areacont', 'area_conteudo', 'retangulo', 150, 300, 1620, 590, borda_cor='tema:primaria',
               borda_largura=2, borda_estilo='dashed'),
        _paginacao('cont'),
    ])
    quadro = _slide('s-quadro01', 'Quadro', 'quadro', 'quadro.jpg', [
        _texto('e-titquad', 'titulo', 260, 138, 1400, 100, 'Título no quadro', tamanho=64, peso=800,
               papel='titulo', alinhamento='center', entrelinha=1.1, autoajuste=True, animacao=_anim('fade', 600)),
        _forma('e-areaquad', 'area_conteudo', 'retangulo', 260, 262, 1400, 520, borda_cor='tema:primaria',
               borda_largura=2, borda_estilo='dashed'),
        _paginacao('quad'),
    ])
    encerramento = _slide('s-encerra1', 'Encerramento', 'encerramento', 'encerramento.jpg', [
        _rotulo('fim', 156, 115, html='obrigado'),
        _forma('e-fiofim', 'decoracao', 'retangulo', 159, 181, 467, 6, preenchimento='tema:primaria',
               animacao=_anim('revelar', 600)),
        _texto('e-titfim', 'titulo', 138, 272, 900, 290, titulo_duas_cores('obrigado', 'pela atenção'),
               tamanho=96, peso=800, papel='titulo', espacamento=-1, entrelinha=0.97, autoajuste=True,
               animacao=_anim('subir', 700)),
        _texto('e-subfim', 'subtitulo', 156, 600, 900, 110, 'Contato ou próxima ação', tamanho=34, peso=700,
               cor='tema:texto_suave', entrelinha=1.25, autoajuste=True, animacao=_anim('fade', 600)),
        _paginacao('fim'),
    ])
    return {
        'formato': 1, 'largura': 1920, 'altura': 1080, 'tema': copy.deepcopy(TEMA),
        'slides': [capa, secao, conteudo, quadro, encerramento],
    }


def garantir_template_padrao():
    """Cria o template da rede se ainda não existe (ou se foi apagado). Devolve o template."""
    from .formato import sanear_documento
    from .models import TemplateApresentacao

    existente = TemplateApresentacao.objects.filter(origem=TemplateApresentacao.Origem.SISTEMA, nome=NOME).first()
    if existente:
        return existente
    return TemplateApresentacao.objects.create(
        nome=NOME, descricao='Identidade visual oficial: capa, seção, conteúdo, quadro e encerramento.',
        estilo=ESTILO, documento=sanear_documento(documento_rede_confianca()),
        origem=TemplateApresentacao.Origem.SISTEMA, padrao_da_rede=True,
        principal=not TemplateApresentacao.objects.filter(principal=True).exists(),
    )
