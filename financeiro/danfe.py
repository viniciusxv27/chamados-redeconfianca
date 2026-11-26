"""DANFE em PDF (retrato, A4) gerada do XML da NF-e — autorizada (nfeProc) ou prévia.

Segue o desenho do Manual de Orientação do Contribuinte: canhoto, quadro do
emitente, quadro DANFE (entrada/saída, número, série, folha), código de barras
CODE-128C da chave, protocolo, destinatário, cálculo do imposto, transporte,
itens e dados adicionais. Homologação e prévia saem com a marca "SEM VALOR
FISCAL".
"""
from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from lxml import etree
from reportlab.graphics.barcode import code128
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

NS = 'http://www.portalfiscal.inf.br/nfe'
LARGURA, ALTURA = A4
MARGEM = 6 * mm
UTIL = LARGURA - 2 * MARGEM
FONTE, NEGRITO = 'Helvetica', 'Helvetica-Bold'


def _t(el, caminho):
    if el is None:
        return ''
    achado = el.find('/'.join(f'{{{NS}}}{p}' for p in caminho.split('/')))
    return (achado.text or '').strip() if achado is not None and achado.text else ''


def _q(el, tag):
    return el.find(f'{{{NS}}}{tag}') if el is not None else None


def _moeda(valor):
    try:
        numero = Decimal(valor or '0')
    except InvalidOperation:
        return valor
    return f'{numero:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def _numero(valor, casas=4):
    try:
        numero = Decimal(valor or '0')
    except InvalidOperation:
        return valor
    texto = f'{numero:,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    if ',' in texto:
        inteiro, frac = texto.split(',')
        frac = frac.rstrip('0').ljust(2, '0')
        texto = f'{inteiro},{frac}'
    return texto


def _doc(valor):
    d = ''.join(c for c in valor if c.isdigit())
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    if len(d) == 11:
        return f'{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}'
    return valor


def _data(valor, hora=False):
    if not valor:
        return ''
    try:
        momento = datetime.fromisoformat(valor)
    except ValueError:
        return valor[:10]
    return momento.strftime('%H:%M:%S' if hora else '%d/%m/%Y')


def _cep(valor):
    return f'{valor[:5]}-{valor[5:]}' if len(valor) == 8 else valor


def _chave_formatada(chave):
    return ' '.join(chave[i:i + 4] for i in range(0, len(chave), 4))


class _Desenho:
    def __init__(self, c):
        self.c = c

    def caixa(self, x, y, w, h, rotulo='', valor='', tamanho=8, negrito=False, alinhar='esq'):
        """y é o topo da caixa (de cima para baixo)."""
        c = self.c
        c.setLineWidth(0.5)
        c.rect(x, y - h, w, h)
        if rotulo:
            c.setFont(FONTE, 5.2)
            c.drawString(x + 1.2, y - 6, rotulo.upper())
        if valor != '':
            c.setFont(NEGRITO if negrito else FONTE, tamanho)
            linhas = simpleSplit(str(valor), NEGRITO if negrito else FONTE, tamanho, w - 3)
            base = y - (7 + tamanho) if rotulo else y - tamanho - 2
            for i, linha in enumerate(linhas[: max(1, int((h - (8 if rotulo else 2)) // (tamanho + 1)))]):
                yy = base - i * (tamanho + 1)
                if alinhar == 'dir':
                    c.drawRightString(x + w - 1.5, yy, linha)
                elif alinhar == 'centro':
                    c.drawCentredString(x + w / 2, yy, linha)
                else:
                    c.drawString(x + 1.5, yy, linha)

    def titulo(self, x, y, texto):
        self.c.setFont(NEGRITO, 6.5)
        self.c.drawString(x, y - 7, texto.upper())
        return y - 9


def gerar(xml_texto, previa='', correcoes=None):
    """PDF (bytes). ``previa``: texto da faixa quando a nota não está autorizada."""
    raiz = etree.fromstring(xml_texto.encode('utf-8') if isinstance(xml_texto, str) else xml_texto)
    inf = raiz.find(f'.//{{{NS}}}infNFe')
    prot = raiz.find(f'.//{{{NS}}}protNFe/{{{NS}}}infProt')
    ide, emit, dest = _q(inf, 'ide'), _q(inf, 'emit'), _q(inf, 'dest')
    total = inf.find(f'{{{NS}}}total/{{{NS}}}ICMSTot')
    chave = (inf.get('Id') or '')[3:]
    homologacao = _t(ide, 'tpAmb') == '2'
    itens = inf.findall(f'{{{NS}}}det')

    saida = io.BytesIO()
    c = canvas.Canvas(saida, pagesize=A4)
    c.setTitle(f'DANFE {chave}')
    d = _Desenho(c)

    linhas_itens = []
    for det in itens:
        prod, imposto = _q(det, 'prod'), _q(det, 'imposto')
        icms = _q(imposto, 'ICMS')
        grupo = icms[0] if icms is not None and len(icms) else None
        cst = (_t(grupo, 'orig') + (_t(grupo, 'CST') or _t(grupo, 'CSOSN'))) if grupo is not None else ''
        descricao = _t(prod, 'xProd')
        if _t(det, 'infAdProd'):
            descricao += ' — ' + _t(det, 'infAdProd')
        linhas_itens.append([_t(prod, 'cProd'), descricao, _t(prod, 'NCM'), cst, _t(prod, 'CFOP'), _t(prod, 'uCom'),
                             _numero(_t(prod, 'qCom')), _numero(_t(prod, 'vUnCom'), 10), _moeda(_t(prod, 'vProd')),
                             _moeda(_t(grupo, 'vBC')), _moeda(_t(grupo, 'vICMS')), _numero(_t(grupo, 'pICMS'), 2)])

    colunas = [('CÓDIGO', 16), ('DESCRIÇÃO DO PRODUTO', 52), ('NCM', 13), ('CST', 8), ('CFOP', 8), ('UN', 8),
               ('QUANT.', 14), ('V. UNIT.', 15), ('V. TOTAL', 16), ('BC ICMS', 15), ('V. ICMS', 13), ('ALÍQ.', 9)]
    escala = UTIL / sum(w for _, w in colunas)
    larguras = [w * escala for _, w in colunas]

    def marca_dagua():
        if homologacao or previa:
            c.saveState()
            c.setFillColorRGB(0.85, 0.85, 0.85)
            c.setFont(NEGRITO, 46)
            c.translate(LARGURA / 2, ALTURA / 2)
            c.rotate(40)
            c.drawCentredString(0, 0, 'SEM VALOR FISCAL')
            c.restoreState()
            c.setFont(NEGRITO, 8)
            c.setFillColorRGB(0.75, 0.1, 0.1)
            faixa = previa or 'EMITIDA EM AMBIENTE DE HOMOLOGAÇÃO — SEM VALOR FISCAL'
            c.drawCentredString(LARGURA / 2, ALTURA - MARGEM + 1.5, faixa)
            c.setFillColorRGB(0, 0, 0)

    def cabecalho(y, folha, folhas):
        # Quadro do emitente | DANFE | código de barras
        h = 34 * mm
        w_emit, w_danfe = UTIL * 0.40, UTIL * 0.17
        w_barra = UTIL - w_emit - w_danfe
        x = MARGEM
        c.rect(x, y - h, w_emit, h)
        c.setFont(NEGRITO, 9.5)
        nome = _t(emit, 'xNome')
        for i, linha in enumerate(simpleSplit(nome, NEGRITO, 9.5, w_emit - 6)[:2]):
            c.drawCentredString(x + w_emit / 2, y - 14 - i * 11, linha)
        end = _q(emit, 'enderEmit')
        c.setFont(FONTE, 7)
        endereco = [f"{_t(end, 'xLgr')}, {_t(end, 'nro')} {_t(end, 'xCpl')}".strip(),
                    f"{_t(end, 'xBairro')} · CEP {_cep(_t(end, 'CEP'))}",
                    f"{_t(end, 'xMun')} - {_t(end, 'UF')}" + (f" · Fone {_t(end, 'fone')}" if _t(end, 'fone') else '')]
        for i, linha in enumerate(endereco):
            c.drawCentredString(x + w_emit / 2, y - 42 - i * 9, linha[:80])
        x += w_emit
        c.rect(x, y - h, w_danfe, h)
        c.setFont(NEGRITO, 12)
        c.drawCentredString(x + w_danfe / 2, y - 14, 'DANFE')
        c.setFont(FONTE, 5.8)
        c.drawCentredString(x + w_danfe / 2, y - 21, 'Documento Auxiliar da')
        c.drawCentredString(x + w_danfe / 2, y - 27.5, 'Nota Fiscal Eletrônica')
        c.setFont(FONTE, 6.5)
        c.drawString(x + 4, y - 40, '0 - ENTRADA')
        c.drawString(x + 4, y - 48, '1 - SAÍDA')
        c.rect(x + w_danfe - 18, y - 50, 12, 13)
        c.setFont(NEGRITO, 10)
        c.drawCentredString(x + w_danfe - 12, y - 47, _t(ide, 'tpNF'))
        c.setFont(NEGRITO, 8)
        c.drawCentredString(x + w_danfe / 2, y - 62, f"Nº {int(_t(ide, 'nNF') or 0):011,d}".replace(',', '.'))
        c.drawCentredString(x + w_danfe / 2, y - 71, f"SÉRIE {int(_t(ide, 'serie') or 0):03d}")
        c.setFont(FONTE, 7)
        c.drawCentredString(x + w_danfe / 2, y - 80, f'Folha {folha}/{folhas}')
        x += w_danfe
        c.rect(x, y - h, w_barra, h)
        if len(chave) == 44:
            barra = code128.Code128(chave, barHeight=11 * mm, barWidth=0.27 * mm, quiet=False)
            barra.drawOn(c, x + (w_barra - barra.width) / 2, y - 15 * mm)
        c.setFont(FONTE, 5.2)
        c.drawString(x + 2, y - 16 * mm - 6, 'CHAVE DE ACESSO')
        c.setFont(NEGRITO, 7.6)
        c.drawCentredString(x + w_barra / 2, y - 16 * mm - 15, _chave_formatada(chave))
        c.setFont(FONTE, 6.5)
        c.drawCentredString(x + w_barra / 2, y - 25 * mm, 'Consulta de autenticidade no portal nacional da NF-e')
        c.drawCentredString(x + w_barra / 2, y - 25 * mm - 8, 'www.nfe.fazenda.gov.br/portal ou no site da Sefaz Autorizadora')
        y -= h
        protocolo = ''
        if prot is not None:
            protocolo = f"{_t(prot, 'nProt')} - {_data(_t(prot, 'dhRecbto'))} {_data(_t(prot, 'dhRecbto'), hora=True)}"
        d.caixa(MARGEM, y, UTIL * 0.57, 8.5 * mm, 'Natureza da operação', _t(ide, 'natOp'))
        d.caixa(MARGEM + UTIL * 0.57, y, UTIL * 0.43, 8.5 * mm, 'Protocolo de autorização de uso',
                protocolo or (previa or 'Sem protocolo'), negrito=bool(protocolo), alinhar='centro')
        y -= 8.5 * mm
        terco = UTIL / 3
        d.caixa(MARGEM, y, terco, 8.5 * mm, 'Inscrição estadual', _t(emit, 'IE'))
        d.caixa(MARGEM + terco, y, terco, 8.5 * mm, 'Inscrição estadual do subst. tributário', _t(emit, 'IEST'))
        d.caixa(MARGEM + 2 * terco, y, terco, 8.5 * mm, 'CNPJ', _doc(_t(emit, 'CNPJ')))
        return y - 8.5 * mm

    def cabecalho_itens(y):
        y = d.titulo(MARGEM, y, 'Dados dos produtos / serviços')
        x = MARGEM
        c.setFont(NEGRITO, 5.4)
        for (rotulo, _w), largura in zip(colunas, larguras):
            c.rect(x, y - 9, largura, 9)
            c.drawCentredString(x + largura / 2, y - 6.3, rotulo)
            x += largura
        return y - 9

    def linha_item(y, valores):
        c.setFont(FONTE, 6.2)
        partes = [simpleSplit(str(v), FONTE, 6.2, largura - 2.5) or [''] for v, largura in zip(valores, larguras)]
        altura = max(len(p) for p in partes) * 7.4 + 2.5
        x = MARGEM
        for i, (p, largura) in enumerate(zip(partes, larguras)):
            c.setLineWidth(0.3)
            c.rect(x, y - altura, largura, altura)
            for j, trecho in enumerate(p):
                yy = y - 7 - j * 7.4
                if i >= 6:
                    c.drawRightString(x + largura - 1.3, yy, trecho)
                elif i in (2, 3, 4, 5):
                    c.drawCentredString(x + largura / 2, yy, trecho)
                else:
                    c.drawString(x + 1.3, yy, trecho)
            x += largura
        return y - altura, altura

    # Quantas folhas: conta as alturas das linhas antes de desenhar.
    def altura_da_linha(valores):
        return max(len(simpleSplit(str(v), FONTE, 6.2, largura - 2.5) or ['']) for v, largura in zip(valores, larguras)) * 7.4 + 2.5

    espaco_primeira = 92 * mm   # depois de canhoto, cabeçalho, destinatário, imposto e transporte; antes dos dados adicionais
    espaco_demais = ALTURA - 2 * MARGEM - 58 * mm - 12
    folhas, usado, limite = 1, 0, espaco_primeira
    for linha in linhas_itens:
        alt = altura_da_linha(linha)
        if usado + alt > limite:
            folhas += 1
            usado, limite = 0, espaco_demais
        usado += alt

    # ── Folha 1 ──
    marca_dagua()
    y = ALTURA - MARGEM
    # Canhoto
    h = 15 * mm
    w_num = UTIL * 0.18
    c.setLineWidth(0.5)
    c.rect(MARGEM, y - h, UTIL - w_num, h)
    c.setFont(FONTE, 5.6)
    recebemos = (f"RECEBEMOS DE {_t(emit, 'xNome')} OS PRODUTOS E/OU SERVIÇOS CONSTANTES DA NOTA FISCAL ELETRÔNICA "
                 f"INDICADA AO LADO. EMISSÃO: {_data(_t(ide, 'dhEmi'))} · DESTINATÁRIO: {_t(dest, 'xNome')} · "
                 f"VALOR TOTAL: R$ {_moeda(_t(total, 'vNF'))}")
    for i, linha in enumerate(simpleSplit(recebemos, FONTE, 5.6, UTIL - w_num - 4)[:2]):
        c.drawString(MARGEM + 2, y - 7 - i * 6.5, linha)
    c.line(MARGEM, y - 8 * mm, MARGEM + UTIL - w_num, y - 8 * mm)
    c.line(MARGEM + (UTIL - w_num) * 0.3, y - 8 * mm, MARGEM + (UTIL - w_num) * 0.3, y - h)
    c.setFont(FONTE, 5.2)
    c.drawString(MARGEM + 1.5, y - 8 * mm - 6, 'DATA DE RECEBIMENTO')
    c.drawString(MARGEM + (UTIL - w_num) * 0.3 + 1.5, y - 8 * mm - 6, 'IDENTIFICAÇÃO E ASSINATURA DO RECEBEDOR')
    c.rect(MARGEM + UTIL - w_num, y - h, w_num, h)
    c.setFont(NEGRITO, 9)
    c.drawCentredString(MARGEM + UTIL - w_num / 2, y - 12, 'NF-e')
    c.setFont(NEGRITO, 7.5)
    c.drawCentredString(MARGEM + UTIL - w_num / 2, y - 23, f"Nº {int(_t(ide, 'nNF') or 0):011,d}".replace(',', '.'))
    c.drawCentredString(MARGEM + UTIL - w_num / 2, y - 32, f"SÉRIE {int(_t(ide, 'serie') or 0):03d}")
    y -= h
    c.setDash(2, 2)
    c.line(MARGEM, y - 3, MARGEM + UTIL, y - 3)
    c.setDash()
    y -= 6
    y = cabecalho(y, 1, folhas)

    # Destinatário / remetente
    y = d.titulo(MARGEM, y - 1, 'Destinatário / remetente')
    end = _q(dest, 'enderDest')
    a = 7.6 * mm
    d.caixa(MARGEM, y, UTIL * 0.58, a, 'Nome / razão social', _t(dest, 'xNome'))
    d.caixa(MARGEM + UTIL * 0.58, y, UTIL * 0.25, a, 'CNPJ / CPF', _doc(_t(dest, 'CNPJ') or _t(dest, 'CPF')))
    d.caixa(MARGEM + UTIL * 0.83, y, UTIL * 0.17, a, 'Data da emissão', _data(_t(ide, 'dhEmi')), alinhar='centro')
    y -= a
    d.caixa(MARGEM, y, UTIL * 0.48, a, 'Endereço', f"{_t(end, 'xLgr')}, {_t(end, 'nro')} {_t(end, 'xCpl')}".strip())
    d.caixa(MARGEM + UTIL * 0.48, y, UTIL * 0.23, a, 'Bairro / distrito', _t(end, 'xBairro'))
    d.caixa(MARGEM + UTIL * 0.71, y, UTIL * 0.12, a, 'CEP', _cep(_t(end, 'CEP')), alinhar='centro')
    d.caixa(MARGEM + UTIL * 0.83, y, UTIL * 0.17, a, 'Data da saída/entrada', _data(_t(ide, 'dhSaiEnt')), alinhar='centro')
    y -= a
    d.caixa(MARGEM, y, UTIL * 0.40, a, 'Município', _t(end, 'xMun'))
    d.caixa(MARGEM + UTIL * 0.40, y, UTIL * 0.18, a, 'Fone / fax', _t(end, 'fone'))
    d.caixa(MARGEM + UTIL * 0.58, y, UTIL * 0.06, a, 'UF', _t(end, 'UF'), alinhar='centro')
    d.caixa(MARGEM + UTIL * 0.64, y, UTIL * 0.19, a, 'Inscrição estadual', _t(dest, 'IE'))
    d.caixa(MARGEM + UTIL * 0.83, y, UTIL * 0.17, a, 'Hora da saída/entrada', _data(_t(ide, 'dhSaiEnt'), hora=True),
            alinhar='centro')
    y -= a

    # Cálculo do imposto
    y = d.titulo(MARGEM, y - 1, 'Cálculo do imposto')
    sexto = UTIL / 6
    for i, (rotulo, tag) in enumerate((('Base de cálculo do ICMS', 'vBC'), ('Valor do ICMS', 'vICMS'),
                                       ('Base de cálculo do ICMS ST', 'vBCST'), ('Valor do ICMS ST', 'vST'),
                                       ('Valor do PIS', 'vPIS'), ('Valor total dos produtos', 'vProd'))):
        d.caixa(MARGEM + i * sexto, y, sexto, a, rotulo, _moeda(_t(total, tag)), alinhar='dir')
    y -= a
    for i, (rotulo, tag) in enumerate((('Valor do frete', 'vFrete'), ('Valor do seguro', 'vSeg'),
                                       ('Desconto', 'vDesc'), ('Outras despesas', 'vOutro'),
                                       ('Valor da Cofins', 'vCOFINS'), ('Valor total da nota', 'vNF'))):
        d.caixa(MARGEM + i * sexto, y, sexto, a, rotulo, _moeda(_t(total, tag)), alinhar='dir', negrito=tag == 'vNF')
    y -= a

    # Transportador
    y = d.titulo(MARGEM, y - 1, 'Transportador / volumes transportados')
    modalidades = {'0': '0 - Por conta do emitente', '1': '1 - Por conta do destinatário', '2': '2 - Terceiros',
                   '3': '3 - Próprio, remetente', '4': '4 - Próprio, destinatário', '9': '9 - Sem frete'}
    transp = _q(inf, 'transp')
    d.caixa(MARGEM, y, UTIL * 0.5, a, 'Razão social', _t(transp, 'transporta/xNome'))
    d.caixa(MARGEM + UTIL * 0.5, y, UTIL * 0.25, a, 'Frete por conta', modalidades.get(_t(transp, 'modFrete'), ''))
    d.caixa(MARGEM + UTIL * 0.75, y, UTIL * 0.25, a, 'CNPJ / CPF', _doc(_t(transp, 'transporta/CNPJ')))
    y -= a

    # Itens
    y = cabecalho_itens(y - 1)
    folha = 1
    fundo_primeira = MARGEM + 34 * mm
    restante = list(linhas_itens)
    while restante:
        limite_y = fundo_primeira if folha == 1 else MARGEM + 6
        alt = altura_da_linha(restante[0])
        if y - alt < limite_y:
            if folha == 1:
                _dados_adicionais(c, d, inf, previa, correcoes, homologacao)
            c.showPage()
            folha += 1
            marca_dagua()
            y = cabecalho(ALTURA - MARGEM, folha, folhas)
            y = cabecalho_itens(y - 1)
            continue
        y, _alt = linha_item(y, restante.pop(0))
    if folha == 1:
        _dados_adicionais(c, d, inf, previa, correcoes, homologacao)
    c.showPage()
    c.save()
    return saida.getvalue()


def _dados_adicionais(c, d, inf, previa, correcoes, homologacao):
    y = MARGEM + 32 * mm
    c.setFont(NEGRITO, 6.5)
    c.drawString(MARGEM, y + 2.5, 'DADOS ADICIONAIS')
    textos = []
    adic = _q(inf, 'infAdic')
    if _t(adic, 'infCpl'):
        textos.append(_t(adic, 'infCpl'))
    ref = inf.find(f'{{{NS}}}ide/{{{NS}}}NFref/{{{NS}}}refNFe')
    if ref is not None and ref.text and ref.text not in ' '.join(textos):
        textos.append(f'NF-e referenciada: {_chave_formatada(ref.text)}')
    for correcao in correcoes or []:
        textos.append(f'Carta de correção {correcao}')
    if homologacao:
        textos.append('NF-e emitida em ambiente de homologação — sem valor fiscal.')
    if previa:
        textos.append(previa)
    d.caixa(MARGEM, y, UTIL * 0.68, 32 * mm - 2, 'Informações complementares', ' '.join(textos), tamanho=6.6)
    d.caixa(MARGEM + UTIL * 0.68, y, UTIL * 0.32, 32 * mm - 2, 'Reservado ao fisco', '')
