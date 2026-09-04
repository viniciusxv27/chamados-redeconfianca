"""Leitura do PDF do currículo: nome, endereço e experiência.

Currículo não tem formato. Cada um vem de um site diferente, com seções em
ordens diferentes e títulos diferentes. Então aqui não existe "parser": existem
pistas, ordenadas da mais confiável para a mais frágil, e o que não for
encontrado fica vazio para o RH completar na mão — melhor um campo em branco do
que um endereço inventado.

Usa a mesma dupla de bibliotecas que a folha de ponto: pdfplumber primeiro,
pypdfium2 quando ele não consegue.
"""
import re
import unicodedata

try:
    import pdfplumber
except ImportError:                                          # pragma: no cover
    pdfplumber = None

try:
    import pypdfium2 as pdfium
except ImportError:                                          # pragma: no cover
    pdfium = None


LIMITE_PAGINAS = 12          # currículo maior que isso é raro; evita PDF gigante


def ler_pdf(dados):
    """Texto do PDF. Devolve '' quando não dá para ler (PDF escaneado, por ex.)."""
    texto = _com_pdfplumber(dados) or _com_pdfium(dados)
    return _limpar(texto or '')


def _com_pdfplumber(dados):
    if pdfplumber is None:
        return ''
    try:
        import io
        with pdfplumber.open(io.BytesIO(dados)) as pdf:
            paginas = [p.extract_text() or '' for p in pdf.pages[:LIMITE_PAGINAS]]
        return '\n'.join(paginas)
    except Exception:
        return ''


def _com_pdfium(dados):
    if pdfium is None:
        return ''
    try:
        doc = pdfium.PdfDocument(dados)
        paginas = []
        for i in range(min(len(doc), LIMITE_PAGINAS)):
            paginas.append(doc[i].get_textpage().get_text_range())
        return '\n'.join(paginas)
    except Exception:
        return ''


def _limpar(texto):
    texto = texto.replace('\x00', ' ')
    linhas = [re.sub(r'[ \t]+', ' ', l).strip() for l in texto.splitlines()]
    return '\n'.join(l for l in linhas if l)


def _sem_acento(t):
    t = unicodedata.normalize('NFKD', t or '')
    return ''.join(c for c in t if not unicodedata.combining(c)).lower()


# ── Nome ────────────────────────────────────────────────────────────────────

ROTULO_NOME = re.compile(r'^\s*nome\s*(completo)?\s*[:\-]\s*(?P<v>.+)$', re.I)
SO_LETRAS = re.compile(r'^[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\'`´^~\s\.]{4,60}$')
NAO_E_NOME = {
    'curriculo', 'curriculum', 'vitae', 'dados pessoais', 'objetivo',
    'experiencia', 'experiencias', 'formacao', 'escolaridade', 'contato',
    'contatos', 'resumo', 'perfil', 'qualificacoes', 'informacoes pessoais',
}


def achar_nome(texto):
    """Nome do candidato.

    Primeiro procura um rótulo explícito ("Nome: ..."), que é o caso confiável.
    Sem rótulo, cai na convenção: o nome costuma ser a primeira linha de texto
    do currículo, antes de qualquer seção.
    """
    for linha in texto.splitlines()[:40]:
        m = ROTULO_NOME.match(linha)
        if m:
            valor = m.group('v').strip(' .:-')
            if 4 <= len(valor) <= 80:
                return _titulo(valor)

    for linha in texto.splitlines()[:12]:
        bruto = linha.strip(' .:-')
        if not SO_LETRAS.match(bruto):
            continue
        if _sem_acento(bruto).strip() in NAO_E_NOME:
            continue
        if len(bruto.split()) < 2:            # nome tem pelo menos nome e sobrenome
            continue
        return _titulo(bruto)
    return ''


def _titulo(valor):
    miudas = {'de', 'da', 'do', 'das', 'dos', 'e'}
    partes = []
    for p in valor.split():
        partes.append(p.lower() if p.lower() in miudas else p.capitalize())
    return ' '.join(partes)[:180]


# ── Endereço ────────────────────────────────────────────────────────────────

ROTULO_ENDERECO = re.compile(
    r'^\s*(endere[cç]o|resid[eê]ncia|moro em|mora em)\s*[:\-]\s*(?P<v>.+)$', re.I)
LOGRADOURO = re.compile(
    r'^\s*((rua|r\.|av\.|avenida|travessa|tv\.|rodovia|estrada|alameda|praça|praca)\s+.+)$',
    re.I)
CEP = re.compile(r'\b\d{5}-?\d{3}\b')
BAIRRO = re.compile(r'\bbairro\s*[:\-]?\s*(?P<v>[A-Za-zÀ-ÿ\s]{3,40})', re.I)
# [^\S\n] é "espaço, menos quebra de linha": sem isso o nome da cidade
# atravessava linhas e "Bairro: Centro\nViana/ES" virava cidade "Centro Viana".
CIDADE_UF = re.compile(
    r'(?P<cidade>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.\-]*(?:[^\S\n]+[A-Za-zÀ-ÿ.\-]+){0,3})'
    r'[^\S\n]*[/-][^\S\n]*(?P<uf>[A-Z]{2})\b')


def achar_endereco(texto, cidades_conhecidas=()):
    """(endereço, cidade, bairro). Campo que não aparece volta vazio."""
    endereco = cidade = bairro = ''

    for linha in texto.splitlines()[:60]:
        m = ROTULO_ENDERECO.match(linha)
        if m and not endereco:
            endereco = m.group('v').strip(' .;')[:300]
        elif LOGRADOURO.match(linha) and not endereco:
            endereco = linha.strip(' .;')[:300]
        m = BAIRRO.search(linha)
        if m and not bairro:
            bairro = m.group('v').strip(' .;,')[:120]

    # "Vila Velha/ES" e "Serra - ES" são a pista mais confiável de cidade.
    m = CIDADE_UF.search(texto)
    if m:
        cidade = m.group('cidade').strip(' .,;')[:120]

    # Sem UF, tenta casar com as cidades que o portal já conhece (os setores).
    if not cidade and cidades_conhecidas:
        alvo = _sem_acento(texto)
        achadas = [c for c in cidades_conhecidas if c and _sem_acento(c) in alvo]
        if achadas:
            cidade = max(achadas, key=len)[:120]

    if not endereco:
        m = CEP.search(texto)
        if m:
            linha = _linha_do(texto, m.start())
            endereco = linha[:300]

    return endereco.strip(), cidade.strip(), bairro.strip()


def _linha_do(texto, posicao):
    inicio = texto.rfind('\n', 0, posicao) + 1
    fim = texto.find('\n', posicao)
    return texto[inicio:fim if fim > 0 else len(texto)].strip()


# ── Experiência ─────────────────────────────────────────────────────────────

TITULOS_EXPERIENCIA = (
    'experiencia profissional', 'experiencias profissionais', 'experiencia',
    'experiencias', 'historico profissional', 'vivencia profissional',
    'atuacao profissional', 'trajetoria profissional', 'empregos anteriores',
)
TITULOS_OUTRAS_SECOES = (
    'formacao', 'escolaridade', 'cursos', 'qualificacoes', 'idiomas',
    'habilidades', 'competencias', 'informatica', 'objetivo', 'dados pessoais',
    'referencias', 'informacoes adicionais', 'atividades complementares',
)


def achar_experiencia(texto):
    """O bloco de experiência profissional, do título até a próxima seção."""
    linhas = texto.splitlines()
    inicio = None
    for i, linha in enumerate(linhas):
        chave = _sem_acento(linha).strip(' :.-')
        if len(chave) <= 45 and any(chave.startswith(t) for t in TITULOS_EXPERIENCIA):
            inicio = i + 1
            break
    if inicio is None:
        return ''

    corpo = []
    for linha in linhas[inicio:]:
        chave = _sem_acento(linha).strip(' :.-')
        if len(chave) <= 45 and any(chave.startswith(t) for t in TITULOS_OUTRAS_SECOES):
            break
        corpo.append(linha)
    return '\n'.join(corpo).strip()[:8000]


# ── Cargos ──────────────────────────────────────────────────────────────────

# Vocabulário do varejo da rede. Currículo raramente diz "cargo: X" — o nome da
# função aparece solto no meio da experiência, e é por ele que o RH busca.
CARGOS_CONHECIDOS = (
    'vendedor', 'vendedora', 'consultor de vendas', 'consultora de vendas',
    'consultor', 'consultora', 'atendente', 'caixa', 'operador de caixa',
    'operadora de caixa', 'gerente', 'gerente de loja', 'subgerente',
    'supervisor', 'supervisora', 'coordenador', 'coordenadora', 'estoquista',
    'auxiliar administrativo', 'assistente administrativo', 'auxiliar de loja',
    'repositor', 'promotor', 'promotora', 'recepcionista', 'estagiario',
    'estagiaria', 'jovem aprendiz', 'aprendiz', 'tecnico', 'analista',
    'assistente', 'auxiliar', 'motorista', 'entregador', 'seguranca',
    'telemarketing', 'sac', 'atendimento ao cliente', 'pos venda',
)


def achar_cargos(texto):
    """Cargos citados no currículo, do mais específico para o mais genérico."""
    alvo = _sem_acento(texto)
    achados = []
    for cargo in CARGOS_CONHECIDOS:
        if re.search(rf'\b{re.escape(cargo)}\b', alvo):
            achados.append(cargo)
    # "consultor de vendas" torna "consultor" redundante.
    finais = [c for c in achados
              if not any(c != outro and c in outro for outro in achados)]
    return sorted(set(finais), key=len, reverse=True)


def extrair(dados_pdf, cidades_conhecidas=()):
    """Lê o PDF e devolve o que deu para identificar.

    Nunca levanta: PDF ilegível devolve tudo vazio, e o RH completa na mão.
    """
    texto = ler_pdf(dados_pdf)
    if not texto:
        return {'texto': '', 'nome': '', 'endereco': '', 'cidade': '',
                'bairro': '', 'experiencia': '', 'cargos': [],
                'telefone': '', 'email': '', 'legivel': False}

    endereco, cidade, bairro = achar_endereco(texto, cidades_conhecidas)
    tel = re.search(r'\(?\d{2}\)?\s*9?\d{4}[-\s]?\d{4}', texto)
    mail = re.search(r'[\w\.\-\+]+@[\w\-]+\.[\w\.\-]+', texto)
    return {
        'texto': texto,
        'nome': achar_nome(texto),
        'endereco': endereco,
        'cidade': cidade,
        'bairro': bairro,
        'experiencia': achar_experiencia(texto),
        'cargos': achar_cargos(texto),
        'telefone': (tel.group(0).strip() if tel else '')[:40],
        'email': (mail.group(0).strip() if mail else '')[:254],
        'legivel': True,
    }
