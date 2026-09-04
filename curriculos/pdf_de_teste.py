"""Gera um PDF mínimo com texto, para os testes não dependerem de arquivo real.

Não é um gerador de PDF de verdade — monta o mínimo que o pdfplumber e o
pypdfium2 conseguem ler: um catálogo, uma página e um fluxo de texto com uma
linha por Tj. É o suficiente para provar a extração ponta a ponta.
"""


def _escapar(texto):
    return (texto.replace('\\', r'\\').replace('(', r'\(').replace(')', r'\)'))


def pdf_com_texto(linhas):
    """Bytes de um PDF de uma página com essas linhas."""
    corpo = ['BT', '/F1 11 Tf', '14 TL', '40 780 Td']
    for linha in linhas:
        corpo.append(f'({_escapar(linha)}) Tj')
        corpo.append('T*')
    corpo.append('ET')
    fluxo = '\n'.join(corpo).encode('latin-1', 'replace')

    objetos = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
        b'/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
        b'<< /Length ' + str(len(fluxo)).encode() + b' >>\nstream\n' + fluxo + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]

    saida = bytearray(b'%PDF-1.4\n')
    posicoes = []
    for i, obj in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += f'{i} 0 obj\n'.encode() + obj + b'\nendobj\n'

    inicio_xref = len(saida)
    saida += f'xref\n0 {len(objetos) + 1}\n'.encode()
    saida += b'0000000000 65535 f \n'
    for pos in posicoes:
        saida += f'{pos:010d} 00000 n \n'.encode()
    saida += (f'trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\n'
              f'startxref\n{inicio_xref}\n%%EOF\n').encode()
    return bytes(saida)


CURRICULO_EXEMPLO = [
    'MARIA SILVA SANTOS',
    'Rua das Palmeiras, 250 - Bairro: Centro',
    'Viana/ES - CEP 29135-000',
    'Telefone: (27) 99876-5432',
    'E-mail: maria.silva@exemplo.com',
    '',
    'OBJETIVO',
    'Atuar na area comercial.',
    '',
    'EXPERIENCIA PROFISSIONAL',
    'Loja Central - Vendedora - 2022 a 2024',
    'Atendimento ao cliente, fechamento de vendas e organizacao da vitrine.',
    'Supermercado Bom Preco - Operadora de caixa - 2020 a 2022',
    '',
    'FORMACAO',
    'Ensino medio completo.',
]
