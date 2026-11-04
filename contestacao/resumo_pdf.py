"""PDF do resumo do ciclo: totais por loja e, por venda, o detalhado.

Quem recebe é gestão, não a loja: o PDF sai do portal por um link no WhatsApp e
pode ser encaminhado. Por isso o documento nunca traz CPF inteiro, nem no campo
próprio nem no motivo, que é texto livre.
"""
import io
import re

from django.utils import timezone

MAX_MOTIVO = 170

_CPF_SOLTO = re.compile(r'(?<!\d)(\d{3})[.\s]?(\d{3})[.\s]?(\d{3})[-\s]?(\d{2})(?!\d)')
_CNPJ_SOLTO = re.compile(r'(?<!\d)(\d{2})\.?(\d{3})\.?(\d{3})/?(\d{4})-?(\d{2})(?!\d)')


def mascarar_documento(valor):
    """CPF ou CNPJ com só o miolo à mostra. Sem jeito de reconhecer: nada à mostra.

    A planilha já trouxe número com ``.0`` no fim e sem o zero da frente, então
    a conta é feita sobre os dígitos, não sobre a máscara que veio.
    """
    texto = re.sub(r'[.,]0+$', '', str(valor or '').strip())
    digitos = re.sub(r'\D', '', texto)
    if not digitos:
        return ''
    if len(digitos) <= 11:
        d = digitos.zfill(11)
        return f'***.{d[3:6]}.{d[6:9]}-**'
    if len(digitos) <= 14:
        d = digitos.zfill(14)
        return f'**.{d[2:5]}.{d[5:8]}/****-**'
    return '***'


def mascarar_texto_livre(texto):
    """Tira CPF/CNPJ digitado no meio de um texto (o motivo é escrito à mão)."""
    texto = _CNPJ_SOLTO.sub(lambda m: f'**.{m[2]}.{m[3]}/****-**', str(texto or ''))
    return _CPF_SOLTO.sub(lambda m: f'***.{m[2]}.{m[3]}-**', texto)


def _seguro(texto):
    """Texto pronto para ``Paragraph``: é XML, e Helvetica só tem o latim do cp1252."""
    from xml.sax.saxutils import escape
    limpo = ' '.join(str(texto or '').split())
    return escape(limpo.encode('cp1252', 'replace').decode('cp1252'))


def _brl(valor):
    inteiro = f'{float(valor or 0):,.2f}'
    return 'R$ ' + inteiro.replace(',', 'X').replace('.', ',').replace('X', '.')


def gerar_pdf(dados):
    """``dados`` vem de ``resumo_whatsapp.dados_do_resumo``. Devolve os bytes do PDF."""
    # Só aqui dentro: se a imagem ainda não tiver o reportlab, o portal segue no ar
    # e o erro aparece na tela do resumo, em vez de derrubar o import do app.
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    roxo = colors.HexColor('#4f46e5')
    cinza = colors.HexColor('#6b7280')
    linha = colors.HexColor('#e5e7eb')
    faixa = colors.HexColor('#f9fafb')

    base = ParagraphStyle('base', fontName='Helvetica', fontSize=7.5, leading=9.5)
    direita = ParagraphStyle('direita', parent=base, alignment=TA_RIGHT)
    negrito = ParagraphStyle('negrito', parent=base, fontName='Helvetica-Bold')
    cabecalho = ParagraphStyle('cabecalho', parent=negrito, textColor=colors.white)
    cabecalho_dir = ParagraphStyle('cabecalho_dir', parent=cabecalho, alignment=TA_RIGHT)
    titulo = ParagraphStyle('titulo', fontName='Helvetica-Bold', fontSize=15, leading=19, textColor=roxo)
    sub = ParagraphStyle('sub', fontName='Helvetica', fontSize=9, leading=12, textColor=cinza)
    secao = ParagraphStyle('secao', fontName='Helvetica-Bold', fontSize=11, leading=14, spaceBefore=8, spaceAfter=4)

    def p(texto, estilo=base):
        return Paragraph(_seguro(texto), estilo)

    corpo = [
        Paragraph('Contestações: resumo do ciclo', titulo),
        p(f"Planilha sincronizada em {dados['sincronizado_em']} · gerado em "
          f"{timezone.localtime():%d/%m/%Y %H:%M} · CPF/CNPJ mascarados", sub),
    ]
    if dados.get('aviso'):
        corpo.append(Spacer(1, 3 * mm))
        corpo.append(p(dados['aviso'], ParagraphStyle('aviso', parent=sub, textColor=colors.HexColor('#b45309'))))
    corpo.append(Spacer(1, 3 * mm))

    t = dados['totais']
    kpis = Table([[
        Paragraph(f"<b>{t['contestacoes']}</b> contestações", base),
        Paragraph(f"<b>{_seguro(_brl(t['valor']))}</b> em vendas", base),
        Paragraph(f"<b>{t['lojas_com_envio']}</b> lojas enviaram · <b>{t['lojas_sem_envio']}</b> sem contestação", base),
        Paragraph(f"<b>{t['com_evidencia']}</b> com evidência · <b>{t['sem_evidencia']}</b> sem", base),
    ]], colWidths=[50 * mm, 55 * mm, 92 * mm, 70 * mm])
    kpis.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), faixa), ('BOX', (0, 0), (-1, -1), 0.5, linha),
                              ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
    corpo += [kpis, Paragraph('Totais por loja', secao)]

    def cab(texto, estilo=cabecalho):
        return Paragraph(_seguro(texto), estilo)

    linhas = [[cab('Loja'), cab('Contestações', cabecalho_dir), cab('Valor', cabecalho_dir),
               cab('Aprovadas', cabecalho_dir), cab('Rejeitadas', cabecalho_dir), cab('Negadas', cabecalho_dir),
               cab('Em aberto', cabecalho_dir), cab('Sem evidência', cabecalho_dir)]]
    for loja in dados['lojas']:
        if loja['contestacoes']:
            linhas.append([p(loja['filial']), p(loja['contestacoes'], direita), p(_brl(loja['valor']), direita),
                           p(loja['aprovadas'], direita), p(loja['rejeitadas'], direita), p(loja['negadas'], direita),
                           p(loja['em_aberto'], direita), p(loja['sem_evidencia'], direita)])
        else:
            linhas.append([p(loja['filial']), p('Sem contestação no ciclo', direita)] + [''] * 6)
    tabela = Table(linhas, repeatRows=1, colWidths=[75 * mm, 30 * mm, 32 * mm, 26 * mm, 26 * mm, 26 * mm, 26 * mm, 26 * mm])
    estilo = [('BACKGROUND', (0, 0), (-1, 0), roxo), ('LINEBELOW', (0, 1), (-1, -1), 0.25, linha),
              ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), ('TOPPADDING', (0, 0), (-1, -1), 2.5),
              ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5)]
    for i, loja in enumerate(dados['lojas'], start=1):
        if not loja['contestacoes']:
            estilo.append(('SPAN', (1, i), (-1, i)))
    tabela.setStyle(TableStyle(estilo))
    corpo += [tabela, Paragraph('Detalhe por venda', secao)]

    larguras = [38 * mm, 50 * mm, 24 * mm, 20 * mm, 72 * mm, 19 * mm, 11 * mm, 33 * mm]
    for loja in dados['lojas']:
        if not loja['contestacoes']:
            continue
        linhas = [
            [Paragraph(f"{_seguro(loja['filial'])} · {loja['contestacoes']} contestação(ões) · "
                       f"{_seguro(_brl(loja['valor']))}", negrito)] + [''] * 7,
            [cab('Vendedor'), cab('Cliente / CPF-CNPJ'), cab('Nº da venda'), cab('Pilar'), cab('Motivo'),
             cab('Valor', cabecalho_dir), cab('Evid.'), cab('Situação')],
        ]
        for v in loja['vendas']:
            cliente = v['cliente'] + (f" · {v['documento']}" if v['documento'] else '')
            linhas.append([p(v['vendedor']), p(cliente), p(v['numero_venda']), p(v['pilar']), p(v['motivo']),
                           p(_brl(v['valor']), direita), p(v['evidencia']), p(v['situacao'])])
        # As duas primeiras linhas repetem a cada página: a loja não se perde na virada.
        tabela = Table(linhas, repeatRows=2, colWidths=larguras)
        tabela.setStyle(TableStyle([
            ('SPAN', (0, 0), (-1, 0)), ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#eef2ff')),
            ('BACKGROUND', (0, 1), (-1, 1), roxo), ('LINEBELOW', (0, 2), (-1, -1), 0.25, linha),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 2.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5), ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ]))
        corpo += [tabela, Spacer(1, 4 * mm)]

    def rodape(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(cinza)
        canvas.drawString(14 * mm, 8 * mm, 'Rede Confiança · uso interno')
        canvas.drawRightString(landscape(A4)[0] - 14 * mm, 8 * mm, f'Página {doc.page}')
        canvas.restoreState()

    saida = io.BytesIO()
    documento = SimpleDocTemplate(
        saida, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=12 * mm, bottomMargin=14 * mm,
        title='Contestações: resumo do ciclo', author='Rede Confiança')
    documento.build(corpo, onFirstPage=rodape, onLaterPages=rodape)
    return saida.getvalue()
