"""Exportação em Excel do extrato e do relatório de conciliação."""
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.http import HttpResponse

ZERO = Decimal('0.00')

CABECALHO = {'bold': True, 'fundo': 'FF4C1D95'}
DINHEIRO = 'R$ #,##0.00'
DATA_BR = 'DD/MM/YYYY'


def _estilizar_cabecalho(planilha, colunas):
    from openpyxl.styles import Alignment, Font, PatternFill
    fundo = PatternFill('solid', fgColor=CABECALHO['fundo'])
    for i, (titulo, largura) in enumerate(colunas, start=1):
        celula = planilha.cell(row=1, column=i, value=titulo)
        celula.font = Font(bold=True, color='FFFFFFFF')
        celula.fill = fundo
        celula.alignment = Alignment(horizontal='center', vertical='center')
        planilha.column_dimensions[celula.column_letter].width = largura
    planilha.freeze_panes = 'A2'


def _resposta(livro, nome):
    buffer = BytesIO()
    livro.save(buffer)
    buffer.seek(0)
    resposta = HttpResponse(
        buffer.read(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resposta['Content-Disposition'] = f'attachment; filename="{nome}"'
    return resposta


def extrato_excel(cartao, gastos, inicio=None, fim=None):
    """Extrato do cartão no período, uma linha por gasto."""
    from openpyxl import Workbook

    livro = Workbook()
    aba = livro.active
    aba.title = 'Extrato'
    _estilizar_cabecalho(aba, [
        ('Data', 12), ('Estabelecimento', 38), ('Categoria', 22),
        ('Valor (R$)', 14), ('Origem', 10), ('Lançado por', 26),
        ('Chamado', 12), ('Descrição', 46),
    ])

    linha = 2
    for gasto in gastos:
        aba.cell(row=linha, column=1, value=gasto.data_gasto).number_format = DATA_BR
        aba.cell(row=linha, column=2, value=gasto.estabelecimento or '')
        aba.cell(row=linha, column=3, value=gasto.categoria_gasto or '')
        aba.cell(row=linha, column=4, value=float(gasto.valor)).number_format = DINHEIRO
        aba.cell(row=linha, column=5, value=gasto.get_origem_display())
        aba.cell(row=linha, column=6,
                 value=getattr(gasto.criado_por, 'full_name', '') or '')
        aba.cell(row=linha, column=7, value=gasto.ticket_id or '')
        aba.cell(row=linha, column=8, value=gasto.descricao or '')
        linha += 1

    if linha > 2:
        from openpyxl.styles import Font
        aba.cell(row=linha, column=3, value='TOTAL').font = Font(bold=True)
        total = aba.cell(row=linha, column=4, value=f'=SUM(D2:D{linha - 1})')
        total.number_format = DINHEIRO
        total.font = Font(bold=True)

    periodo = ''
    if inicio or fim:
        periodo = f"_{(inicio or date.min):%Y%m%d}-{(fim or date.today()):%Y%m%d}"
    return _resposta(livro, f'extrato_cartao_{cartao.last4}{periodo}.xlsx')


SITUACOES = {
    'conferido': ('Conferido', 'FFD1FAE5'),
    'divergente': ('Divergência de valor', 'FFFEE2E2'),
    'nao_lancado': ('Na fatura, não lançado', 'FFFEF3C7'),
    'sem_cobranca': ('Lançado, sem cobrança', 'FFDBEAFE'),
}
ORDEM_SITUACOES = ('divergente', 'nao_lancado', 'sem_cobranca', 'conferido')


def _tipo(item):
    if not item:
        return ''
    if item.get('iof'):
        return 'IOF'
    return 'Internacional' if item.get('internacional') else 'Nacional'


def _celula(aba, linha, coluna, valor, formato=None, negrito=False, fundo=None):
    from openpyxl.styles import Font, PatternFill
    celula = aba.cell(row=linha, column=coluna, value=valor)
    if formato:
        celula.number_format = formato
    if negrito:
        celula.font = Font(bold=True)
    if fundo:
        celula.fill = PatternFill('solid', fgColor=fundo)
    return celula


def _aba_conciliacao(livro, titulo, itens):
    """Uma linha por item conciliado: ``itens`` = [(rótulo do cartão ou None, linha)]."""
    com_cartao = any(rotulo for rotulo, _ in itens)
    colunas = ([('Cartão', 26)] if com_cartao else []) + [
        ('Situação', 24), ('Data (fatura)', 13), ('Estabelecimento (fatura)', 34), ('Categoria', 18),
        ('Cidade', 16), ('Parcela', 9), ('Tipo', 13), ('Valor fatura', 15),
        ('Data (portal)', 13), ('Estabelecimento (portal)', 30), ('Valor portal', 15),
        ('Diferença', 14), ('Lançado por', 24), ('Chamado', 10), ('Observação', 42)]
    aba = livro.create_sheet(titulo)
    _estilizar_cabecalho(aba, colunas)
    desloca = 1 if com_cartao else 0
    linha_planilha = 2
    for rotulo, item in itens:
        f, g = item['fatura'], item['gasto']
        nome, cor = SITUACOES[item['situacao']]
        if com_cartao:
            _celula(aba, linha_planilha, 1, rotulo or '')
        c = desloca
        _celula(aba, linha_planilha, c + 1, nome, fundo=cor)
        _celula(aba, linha_planilha, c + 2, f['data'] if f else None, DATA_BR)
        _celula(aba, linha_planilha, c + 3, f['estabelecimento'] if f else '')
        _celula(aba, linha_planilha, c + 4, (f.get('categoria') or '') if f else '')
        _celula(aba, linha_planilha, c + 5, (f.get('cidade') or '') if f else '')
        _celula(aba, linha_planilha, c + 6, (f.get('parcela') or '') if f else '')
        _celula(aba, linha_planilha, c + 7, _tipo(f))
        _celula(aba, linha_planilha, c + 8, float(f['valor']) if f else None, DINHEIRO)
        _celula(aba, linha_planilha, c + 9, g.data_gasto if g else None, DATA_BR)
        _celula(aba, linha_planilha, c + 10, (g.estabelecimento or '') if g else '')
        _celula(aba, linha_planilha, c + 11, float(g.valor) if g else None, DINHEIRO)
        _celula(aba, linha_planilha, c + 12, float(item['diferenca']) if item['situacao'] != 'conferido' else 0.0,
                DINHEIRO)
        _celula(aba, linha_planilha, c + 13, (getattr(g.criado_por, 'full_name', '') or '') if g else '')
        _celula(aba, linha_planilha, c + 14, (g.ticket_id or '') if g else '')
        observacao = ''
        if item.get('parcelado'):
            observacao = 'Compra inteira lançada no portal; a fatura cobra a parcela'
        elif item.get('total_parcelado'):
            observacao = f"Parcela × nº de parcelas = R$ {item['total_parcelado']:.2f} (comparado com o portal)"
        _celula(aba, linha_planilha, c + 15, observacao)
        linha_planilha += 1

    ultima = linha_planilha - 1
    if ultima >= 2:
        from openpyxl.utils import get_column_letter
        aba.auto_filter.ref = f'A1:{get_column_letter(len(colunas))}{ultima}'
        # SUBTOTAL(109) soma só o que está visível: o total acompanha o filtro.
        _celula(aba, linha_planilha + 1, desloca + 7, 'TOTAL (filtro)', negrito=True)
        for coluna in (desloca + 8, desloca + 11, desloca + 12):
            letra = get_column_letter(coluna)
            _celula(aba, linha_planilha + 1, coluna, f'=SUBTOTAL(109,{letra}2:{letra}{ultima})', DINHEIRO,
                    negrito=True)
    return aba


def _bloco(aba, linha, pares, largura_a=34):
    """Rótulo/valor em duas colunas; devolve a próxima linha livre."""
    aba.column_dimensions['A'].width = max(aba.column_dimensions['A'].width or 0, largura_a)
    for rotulo, valor in pares:
        if rotulo is None:
            linha += 1
            continue
        _celula(aba, linha, 1, rotulo, negrito=True)
        formato = DINHEIRO if isinstance(valor, (Decimal, float)) else (DATA_BR if isinstance(valor, date) else None)
        _celula(aba, linha, 2, float(valor) if isinstance(valor, Decimal) else valor, formato)
        linha += 1
    return linha


def _tabela_situacoes(aba, linha, relatorio):
    _celula(aba, linha, 1, 'Situação', negrito=True)
    _celula(aba, linha, 2, 'Qtd.', negrito=True)
    _celula(aba, linha, 3, 'Valor (R$)', negrito=True)
    linha += 1
    totais = {
        'divergente': relatorio['total_divergencia'], 'nao_lancado': relatorio['total_so_na_fatura'],
        'sem_cobranca': relatorio['total_so_no_extrato'], 'conferido': relatorio['total_conferido'],
    }
    for chave in ORDEM_SITUACOES:
        nome, cor = SITUACOES[chave]
        _celula(aba, linha, 1, nome, fundo=cor)
        _celula(aba, linha, 2, sum(1 for l in relatorio['linhas'] if l['situacao'] == chave))
        _celula(aba, linha, 3, float(totais[chave]), DINHEIRO)
        linha += 1
    return linha + 1


def _tabela_categorias(aba, linha, categorias):
    _celula(aba, linha, 1, 'Categoria (fatura)', negrito=True)
    _celula(aba, linha, 2, '% do total', negrito=True)
    _celula(aba, linha, 3, 'Valor (R$)', negrito=True)
    linha += 1
    for c in categorias:
        _celula(aba, linha, 1, c['categoria'])
        _celula(aba, linha, 2, c['percentual'] / 100, '0.0%')
        _celula(aba, linha, 3, float(c['valor']), DINHEIRO)
        linha += 1
    return linha + 1


def conciliacao_excel(cartao, relatorio, referencia=None, leitura=None, arquivo='', janela=None):
    """Conciliação de um cartão: resumo + uma aba única com tudo, filtrável."""
    from openpyxl import Workbook

    livro = Workbook()
    resumo = livro.active
    resumo.title = 'Resumo'
    resumo.column_dimensions['B'].width = 22
    resumo.column_dimensions['C'].width = 18
    _celula(resumo, 1, 1, f'Conciliação — {cartao.titulo} (final {cartao.last4})', negrito=True)
    pares = [
        ('Responsável', getattr(cartao.responsavel, 'full_name', '') or ''),
        ('Fatura de referência', referencia.strftime('%m/%Y') if referencia else '—'),
        ('Arquivo', arquivo or '—'),
    ]
    if janela and janela[0]:
        pares.append(('Gastos do portal comparados', f'{janela[0]:%d/%m/%Y} a {janela[1]:%d/%m/%Y}'))
    pares += [(None, None),
              ('Total da fatura (este cartão)', relatorio['total_fatura']),
              ('Lançado no portal', relatorio['total_extrato']),
              ('Diferença', relatorio['diferenca_total']),
              ('Conciliado (valor)', relatorio['total_conferido']),
              ('% conciliado', f"{relatorio['percentual_conciliado']}%")]
    if leitura:
        pares += [(None, None),
                  ('Fatura declara', leitura.get('declarado')),
                  ('   nacional', leitura.get('declarado_nacional')),
                  ('   internacional (com IOF)', leitura.get('declarado_internacional') or Decimal('0')),
                  ('Leitura conferida', 'Sim' if leitura.get('confere') else 'NÃO — confira')]
    linha = _bloco(resumo, 3, pares) + 1
    linha = _tabela_situacoes(resumo, linha, relatorio)
    _tabela_categorias(resumo, linha, relatorio['por_categoria'])

    _aba_conciliacao(livro, 'Conciliação', [(None, l) for l in relatorio['linhas']])
    marca = f"_{referencia:%Y%m}" if referencia else ''
    return _resposta(livro, f'conciliacao_cartao_{cartao.last4}{marca}.xlsx')


def conciliacao_geral_excel(dados):
    """Conciliação geral da fatura: um cartão por linha, tudo junto na aba única."""
    from openpyxl import Workbook

    geral = dados['geral']
    livro = Workbook()
    resumo = livro.active
    resumo.title = 'Resumo'
    _celula(resumo, 1, 1, 'Conciliação geral da fatura', negrito=True)
    linha = _bloco(resumo, 3, [
        ('Arquivo', geral['arquivo'] or '—'),
        ('Referência', geral['referencia'].strftime('%m/%Y')),
        ('Vencimento', geral['vencimento'] or '—'),
        ('Total declarado na fatura', geral['total_declarado'] if geral['total_declarado'] is not None else '—'),
        ('Total lido', geral['total_lido']),
        ('Lançado no portal (cartões cadastrados)', dados['total_portal']),
        ('Conciliado (valor)', dados['total_conferido']),
        ('% conciliado', f"{dados['percentual_conciliado']}%"),
        ('Em cartões não cadastrados no portal', dados['total_nao_cadastrados']),
    ]) + 1

    cabecalho = ['Final', 'Nome na fatura', 'Cartão no portal', 'Responsável', 'Lançamentos', 'Nacional',
                 'Internacional', 'Total fatura', 'Leitura confere', 'Lançado no portal', 'Conciliado',
                 'Divergências', 'Valor divergências', 'Não lançados', 'Valor não lançados', 'Sem cobrança',
                 'Valor sem cobrança', '% conciliado']
    larguras = [8, 26, 26, 26, 12, 14, 14, 15, 14, 16, 15, 12, 16, 12, 16, 12, 16, 12]
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    for i, (titulo, largura) in enumerate(zip(cabecalho, larguras), start=1):
        celula = resumo.cell(row=linha, column=i, value=titulo)
        celula.font = Font(bold=True, color='FFFFFFFF')
        celula.fill = PatternFill('solid', fgColor=CABECALHO['fundo'])
        letra = get_column_letter(i)
        resumo.column_dimensions[letra].width = max(resumo.column_dimensions[letra].width or 0, largura)
    primeira = linha + 1
    linha += 1
    for item in dados['linhas_geral']:
        leitura, cartao, rel = item['leitura'], item['cartao'], item['relatorio']
        valores = [
            item['final'], leitura.get('nome', ''),
            cartao.titulo if cartao else 'NÃO CADASTRADO',
            (getattr(cartao.responsavel, 'full_name', '') or '') if cartao else '',
            leitura.get('lancamentos', 0),
            float(leitura.get('lido_nacional') or 0), float(leitura.get('lido_internacional') or 0),
            float(leitura.get('lido') or 0), 'Sim' if leitura.get('confere') else 'NÃO',
            float(rel['total_extrato']) if rel else None, float(rel['total_conferido']) if rel else None,
            len(rel['divergentes']) if rel else None, float(rel['total_divergencia']) if rel else None,
            len(rel['so_na_fatura']) if rel else None, float(rel['total_so_na_fatura']) if rel else None,
            len(rel['so_no_extrato']) if rel else None, float(rel['total_so_no_extrato']) if rel else None,
            (rel['percentual_conciliado'] / 100) if rel else None,
        ]
        for i, valor in enumerate(valores, start=1):
            formato = DINHEIRO if i in (6, 7, 8, 10, 11, 13, 15, 17) else ('0.0%' if i == 18 else None)
            fundo = 'FFFEE2E2' if (i == 9 and valor == 'NÃO') or (i == 3 and not cartao) else None
            _celula(resumo, linha, i, valor, formato, fundo=fundo)
        linha += 1
    if linha > primeira:
        _celula(resumo, linha, 2, 'TOTAL', negrito=True)
        for i in (5, 6, 7, 8, 10, 11, 12, 13, 14, 15, 16, 17):
            letra = get_column_letter(i)
            _celula(resumo, linha, i, f'=SUM({letra}{primeira}:{letra}{linha - 1})',
                    DINHEIRO if i not in (5, 12, 14, 16) else None, negrito=True)

    itens = []
    for item in dados['cadastrados']:
        rotulo = f"{item['final']} · {item['cartao'].titulo}"
        itens += [(rotulo, l) for l in item['relatorio']['linhas']]
    _aba_conciliacao(livro, 'Conciliação', itens)

    if dados['nao_cadastrados']:
        aba = livro.create_sheet('Cartões fora do portal')
        _estilizar_cabecalho(aba, [('Final', 8), ('Nome na fatura', 26), ('Data', 12), ('Estabelecimento', 36),
                                   ('Categoria', 18), ('Parcela', 9), ('Tipo', 13), ('Valor (R$)', 15)])
        n = 2
        for item in dados['nao_cadastrados']:
            for f in item['lancamentos']:
                for coluna, valor, formato in ((1, item['final'], None), (2, item['leitura'].get('nome', ''), None),
                                               (3, f['data'], DATA_BR), (4, f['estabelecimento'], None),
                                               (5, f.get('categoria') or '', None), (6, f.get('parcela') or '', None),
                                               (7, _tipo(f), None), (8, float(f['valor']), DINHEIRO)):
                    _celula(aba, n, coluna, valor, formato)
                n += 1

    return _resposta(livro, f"conciliacao_geral_{geral['referencia']:%Y%m}.xlsx")
