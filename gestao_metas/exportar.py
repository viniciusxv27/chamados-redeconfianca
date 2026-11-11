"""Excel no mesmo desenho da planilha antiga: uma aba por pilar, mesma ordem de colunas, com valores."""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import servicos
from .models import ABA_DO_PILAR, PILARES, Realizado

AZUL = PatternFill('solid', fgColor='1F3864')
CINZA = PatternFill('solid', fgColor='D9E1F2')
AMARELO = PatternFill('solid', fgColor='FFF2CC')
BRANCO = Font(color='FFFFFF', bold=True)
NEGRITO = Font(bold=True)
FINA = Side(style='thin', color='BFBFBF')
BORDA = Border(left=FINA, right=FINA, top=FINA, bottom=FINA)
MOEDA = '#,##0.00'
INTEIRO = '#,##0'
PCT = '0.0%'


def _meses_do_historico(competencia):
    """Do mês mais antigo com dado (até 19 meses para trás) a M-1 — a janela da planilha antiga."""
    fim = servicos.somar_meses(competencia.mes, -1)
    inicio = servicos.somar_meses(competencia.mes, -19)
    primeiro = Realizado.objects.filter(mes__gte=inicio, mes__lte=fim).order_by('mes').values_list('mes', flat=True).first()
    inicio = max(inicio, primeiro) if primeiro else servicos.somar_meses(competencia.mes, -12)
    inicio = min(inicio, servicos.somar_meses(competencia.mes, -12))
    meses, m = [], inicio
    while m <= fim:
        meses.append(m)
        m = servicos.somar_meses(m, 1)
    return meses


def gerar(competencia):
    dados, _ = servicos.montar(competencia)
    meses = _meses_do_historico(competencia)
    lojas_ids = [l['loja_id'] for l in dados['movel']['linhas']]
    reais = {(r.loja_id, r.mes, r.pilar): r for r in Realizado.objects.filter(loja_id__in=lojas_ids, mes__in=meses)}
    wb = Workbook()
    wb.remove(wb.active)
    for pilar, _ in PILARES:
        ws = wb.create_sheet(ABA_DO_PILAR[pilar])
        if pilar == 'fixa':
            _aba_fixa(ws, dados['fixa'], meses, reais)
        else:
            _aba_padrao(ws, pilar, dados[pilar], meses, reais)
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def _escrever(ws, linha, coluna, valor, formato=None, negrito=False, fundo=None):
    c = ws.cell(linha, coluna, valor)
    c.border = BORDA
    if formato:
        c.number_format = formato
    if negrito:
        c.font = NEGRITO
    if fundo:
        c.fill = fundo
    return c


def _cabecalho(ws, linha, coluna, texto, largura=None):
    c = ws.cell(linha, coluna, texto)
    c.fill = AZUL
    c.font = BRANCO
    c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    c.border = BORDA
    if largura:
        ws.column_dimensions[get_column_letter(coluna)].width = largura


def _linhas_de_saida(bloco):
    """Lojas, uma linha em branco, os subtotais por região e a REDE."""
    saida = [('loja', l) for l in bloco['linhas']]
    saida.append(('vazio', None))
    saida += [('grupo', g) for g in bloco['grupos']]
    saida.append(('rede', bloco['rede']))
    return saida


def _aba_padrao(ws, pilar, bloco, meses, reais):
    # A PDV VIVO · B PDV REDE · C meta · D vazio · meses · MEDIA QUAD · vazio · META VIVO · META MÊS ANTERIOR ·
    # REAL ANO ANTERIOR · vazio · M X M · Y X Y · META X QUAD · M X M
    c_mes0 = 5
    c_quad = c_mes0 + len(meses)
    c_vivo = c_quad + 2
    c_comp = c_vivo + 4
    ws.merge_cells(start_row=1, start_column=c_mes0, end_row=1, end_column=c_quad)
    _cabecalho(ws, 1, c_mes0, 'REALIZADO')
    ws.merge_cells(start_row=1, start_column=c_comp, end_row=1, end_column=c_comp + 1)
    _cabecalho(ws, 1, c_comp, 'REALIZADO')
    ws.merge_cells(start_row=1, start_column=c_comp + 2, end_row=1, end_column=c_comp + 3)
    _cabecalho(ws, 1, c_comp + 2, 'META')
    _cabecalho(ws, 2, 1, 'PDV VIVO', 34)
    _cabecalho(ws, 2, 2, 'PDV REDE', 26)
    _cabecalho(ws, 2, 3, ABA_DO_PILAR[pilar], 14)
    ws.column_dimensions['D'].width = 3
    for i, mes in enumerate(meses):
        _cabecalho(ws, 2, c_mes0 + i, servicos.rotulo_mes(mes), 12)
    _cabecalho(ws, 2, c_quad, 'MEDIA QUAD', 13)
    ws.column_dimensions[get_column_letter(c_quad + 1)].width = 3
    for i, titulo in enumerate(('META VIVO', 'META MÊS ANTERIOR', 'REAL ANO ANTERIOR')):
        _cabecalho(ws, 2, c_vivo + i, titulo, 14)
    ws.column_dimensions[get_column_letter(c_vivo + 3)].width = 3
    for i, titulo in enumerate(('M X M', 'Y X Y', 'META X QUAD', 'M X M')):
        _cabecalho(ws, 2, c_comp + i, titulo, 11)
    r = 3
    for tipo, item in _linhas_de_saida(bloco):
        if tipo == 'vazio':
            r += 1
            continue
        negrito = tipo != 'loja'
        fundo = CINZA if negrito else None
        if tipo == 'loja':
            _escrever(ws, r, 1, item['loja'].pdv_vivo)
            _escrever(ws, r, 2, item['loja'].nome)
        else:
            _escrever(ws, r, 1, 'REDE' if tipo == 'rede' else '', negrito=True, fundo=fundo)
            _escrever(ws, r, 2, '' if tipo == 'rede' else item['titulo'], negrito=True, fundo=fundo)
        _escrever(ws, r, 3, item['meta'], MOEDA, negrito, fundo or AMARELO)
        for i, mes in enumerate(meses):
            if tipo == 'loja':
                real = reais.get((item['loja_id'], mes, pilar))
                valor = float(real.valor) if (real and real.valor is not None) else None
            else:
                valor = None
            _escrever(ws, r, c_mes0 + i, valor, MOEDA, negrito, fundo)
        if tipo != 'loja':
            # Subtotais: soma das lojas mês a mês.
            for i, mes in enumerate(meses):
                soma = None
                ids = [l['loja_id'] for l in bloco['linhas']
                       if tipo == 'rede' or (l['regiao'] or 'Sem região') == item['titulo']]
                for lid in ids:
                    real = reais.get((lid, mes, pilar))
                    if real and real.valor is not None:
                        soma = (soma or 0) + float(real.valor)
                ws.cell(r, c_mes0 + i).value = soma
        _escrever(ws, r, c_quad, item['media_quad'], MOEDA, negrito, fundo)
        _escrever(ws, r, c_vivo, item['meta_vivo'], MOEDA, negrito, fundo)
        _escrever(ws, r, c_vivo + 1, item['meta_anterior'], MOEDA, negrito, fundo)
        _escrever(ws, r, c_vivo + 2, item['real_ano_anterior'], MOEDA, negrito, fundo)
        for i, chave in enumerate(('mxm', 'yxy', 'mxquad', 'mxmeta')):
            _escrever(ws, r, c_comp + i, item[chave], PCT, negrito, fundo)
        r += 1
    ws.freeze_panes = 'C3'


def _aba_fixa(ws, bloco, meses, reais):
    # A PDV VIVO · B PDV REDE · C FIXA (R$) · D QTD CNS · E QTD · F ARRED/CIMA · G ARRED/BAIXO · H Decisão ·
    # I NOVA META · J TICKET MÉDIO · R$ por mês · MEDIA QUAD · vazio · (QTD TV, QTD FIXA) por mês · MEDIA QUAD ·
    # vazio · META VIVO · META MÊS ANTERIOR · REAL ANO ANTERIOR · vazio · M X M · Y X Y · META X QUAD · M X M
    meses_qtd = meses[-8:]
    c_rs0 = 11
    c_quad_rs = c_rs0 + len(meses)
    c_q0 = c_quad_rs + 2
    c_quad_q = c_q0 + 2 * len(meses_qtd)
    c_vivo = c_quad_q + 2
    c_comp = c_vivo + 4
    ws.merge_cells(start_row=1, start_column=c_rs0, end_row=1, end_column=c_quad_rs)
    _cabecalho(ws, 1, c_rs0, 'REALIZADO (R$)')
    ws.merge_cells(start_row=1, start_column=c_q0, end_row=1, end_column=c_quad_q)
    _cabecalho(ws, 1, c_q0, 'REALIZADO (QUANTIDADE)')
    ws.merge_cells(start_row=1, start_column=c_comp, end_row=1, end_column=c_comp + 1)
    _cabecalho(ws, 1, c_comp, 'REALIZADO')
    ws.merge_cells(start_row=1, start_column=c_comp + 2, end_row=1, end_column=c_comp + 3)
    _cabecalho(ws, 1, c_comp + 2, 'META')
    titulos = [('PDV VIVO', 34), ('PDV REDE', 26), ('FIXA', 13), ('QTD CNS', 9), ('QTD', 8), ('ARRED/CIMA', 11),
               ('ARRED/BAIXO', 11), ('Decisão', 9), ('NOVA META', 11), ('TICKET MÉDIO', 12)]
    for i, (titulo, largura) in enumerate(titulos, start=1):
        _cabecalho(ws, 3, i, titulo, largura)
    for i, mes in enumerate(meses):
        _cabecalho(ws, 3, c_rs0 + i, servicos.rotulo_mes(mes), 11)
    _cabecalho(ws, 3, c_quad_rs, 'MEDIA QUAD', 12)
    for i, mes in enumerate(meses_qtd):
        c = c_q0 + 2 * i
        ws.merge_cells(start_row=2, start_column=c, end_row=2, end_column=c + 1)
        _cabecalho(ws, 2, c, servicos.rotulo_mes(mes))
        _cabecalho(ws, 3, c, 'QTD TV', 8)
        _cabecalho(ws, 3, c + 1, 'QTD FIXA', 9)
    _cabecalho(ws, 3, c_quad_q, 'MEDIA QUAD', 12)
    for i, titulo in enumerate(('META VIVO', 'META MÊS ANTERIOR', 'REAL ANO ANTERIOR')):
        _cabecalho(ws, 3, c_vivo + i, titulo, 14)
    for i, titulo in enumerate(('M X M', 'Y X Y', 'META X QUAD', 'M X M')):
        _cabecalho(ws, 3, c_comp + i, titulo, 11)
    r = 4
    for tipo, item in _linhas_de_saida(bloco):
        if tipo == 'vazio':
            r += 1
            continue
        negrito = tipo != 'loja'
        fundo = CINZA if negrito else None
        if tipo == 'loja':
            ids = [item['loja_id']]
            _escrever(ws, r, 1, item['loja'].pdv_vivo)
            _escrever(ws, r, 2, item['loja'].nome)
        else:
            ids = [l['loja_id'] for l in bloco['linhas']
                   if tipo == 'rede' or (l['regiao'] or 'Sem região') == item['titulo']]
            _escrever(ws, r, 1, 'REDE' if tipo == 'rede' else '', negrito=True, fundo=fundo)
            _escrever(ws, r, 2, '' if tipo == 'rede' else item['titulo'], negrito=True, fundo=fundo)
        _escrever(ws, r, 3, item['meta'], MOEDA, negrito, fundo or AMARELO)
        _escrever(ws, r, 4, item['qtd_cns'], INTEIRO, negrito, fundo or AMARELO)
        _escrever(ws, r, 5, item['qtd'], INTEIRO, negrito, fundo)
        _escrever(ws, r, 6, item['cima'], INTEIRO, negrito, fundo)
        _escrever(ws, r, 7, item['baixo'], INTEIRO, negrito, fundo)
        _escrever(ws, r, 8, ('Baixo' if item.get('decisao') == 'BAIXO' else 'Cima') if tipo == 'loja' else '',
                  None, negrito, fundo or AMARELO)
        _escrever(ws, r, 9, item['nova_meta'], INTEIRO, True, fundo)
        _escrever(ws, r, 10, item.get('ticket'), MOEDA, negrito, fundo)
        for i, mes in enumerate(meses):
            soma = None
            for lid in ids:
                real = reais.get((lid, mes, 'fixa'))
                if real and real.valor is not None:
                    soma = (soma or 0) + float(real.valor)
            _escrever(ws, r, c_rs0 + i, soma, MOEDA, negrito, fundo)
        _escrever(ws, r, c_quad_rs, item['media_quad_valor'], MOEDA, negrito, fundo)
        for i, mes in enumerate(meses_qtd):
            tv = fx = None
            for lid in ids:
                real = reais.get((lid, mes, 'fixa'))
                if real and real.qtd_tv is not None:
                    tv = (tv or 0) + real.qtd_tv
                if real and real.qtd_fixa is not None:
                    fx = (fx or 0) + real.qtd_fixa
            _escrever(ws, r, c_q0 + 2 * i, tv, INTEIRO, negrito, fundo)
            _escrever(ws, r, c_q0 + 2 * i + 1, fx, INTEIRO, negrito, fundo)
        _escrever(ws, r, c_quad_q, item['media_quad'], '#,##0.0', negrito, fundo)
        _escrever(ws, r, c_vivo, item['meta_vivo'], '#,##0.0', negrito, fundo)
        _escrever(ws, r, c_vivo + 1, item['meta_anterior'], INTEIRO, negrito, fundo)
        _escrever(ws, r, c_vivo + 2, item['real_ano_anterior'], INTEIRO, negrito, fundo)
        for i, chave in enumerate(('mxm', 'yxy', 'mxquad', 'mxmeta')):
            _escrever(ws, r, c_comp + i, item[chave], PCT, negrito, fundo)
        r += 1
    ws.freeze_panes = 'C4'
