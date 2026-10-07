"""Leitura da fatura do cartão em PDF e conciliação com os lançamentos do portal.

A fatura do Itaú imprime os lançamentos em **duas colunas por página**, e cada
lançamento ocupa duas linhas:

    04/11  ProdutosUOL 09/12                    19,90
           DIVERSOS .Sao Paulo

Remontar isso como texto e sair aplicando expressão regular não funciona: o
valor da coluna da esquerda é impresso perto do centro da página, e qualquer
corte "na metade" o gruda na descrição da coluna da direita — a fatura fecha,
mas com os valores trocados de dono.

Por isso a leitura é **geométrica**: as colunas são descobertas pela posição da
coluna de datas, e dentro de cada uma o lançamento é montado pelo x de cada
palavra (data, descrição, valor). No fim, o total lido é conferido contra o
total que a própria fatura declara por cartão — se não bater, o arquivo é
reportado como suspeito em vez de entrar calado na conciliação.

Uma fatura traz vários cartões (titular e adicionais), cada um num bloco
"NOME (final 1234) … Lançamentos no cartão (final 1234) total". Depois vêm os
**lançamentos internacionais**, de novo por cartão, com o repasse de IOF e um
total próprio, e por fim as **compras parceladas das próximas faturas** — que
não são cobrança deste mês e ficam de fora.
"""
import logging
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

ZERO = Decimal('0.00')

RE_DATA = re.compile(r'^\d{2}/\d{2}$')
RE_VALOR = re.compile(r'^-?[\d.]*\d,\d{2}$')
RE_ABRE_CARTAO = re.compile(r'\(\s*final\s*(\d{4})\s*\)', re.I)
RE_FECHA_CARTAO = re.compile(r'lancamentos?\s*no\s*cartao', re.I)
RE_VENCIMENTO = re.compile(r'Vencimento:?\s*(\d{2}/\d{2}/\d{4})', re.I)
RE_EMISSAO = re.compile(r'Emiss[aã]o:?\s*(\d{2}/\d{2}/\d{4})', re.I)

# Seções da fatura, pelo texto compactado (sem acento, espaço nem pontuação).
NACIONAL, INTERNACIONAL, FUTURAS = 'nacional', 'internacional', 'futuras'
# Parcela colada no fim da descrição: 'COMPERIMPORTACAO02/10'
RE_PARCELA = re.compile(r'\s*(\d{2})/(\d{2})\s*$')

# Distância máxima entre dois x da mesma coluna de datas.
TOLERANCIA_COLUNA = 20


def _sem_acento(texto):
    return unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()


def _compacto(texto):
    """'Lançamentos internacionais' -> 'lancamentosinternacionais'."""
    return re.sub(r'[^a-z0-9]', '', _sem_acento(texto).lower())


def _secao_da_linha(compacto):
    """A seção que a linha abre, ou None. O cabeçalho se repete no topo de cada
    coluna — é ele que diz de que seção é a continuação."""
    if 'lancamentosinternacionais' in compacto:
        return INTERNACIONAL
    if 'comprasparceladas' in compacto or 'proximasfaturas' in compacto:
        return FUTURAS
    if compacto.startswith('lancamentoscomprasesaques') or compacto.startswith('lancamentosprodutoseservicos'):
        return NACIONAL
    return None


def para_decimal(texto):
    """'1.258,60' -> Decimal('1258.60'). None quando não é número."""
    bruto = (texto or '').strip().replace('.', '').replace(',', '.')
    try:
        return Decimal(bruto).quantize(ZERO)
    except (InvalidOperation, ValueError):
        return None


def _colunas_de_data(palavras):
    """Os x em que começam as colunas de data — uma por bloco de lançamentos.

    Nem toda data marca uma coluna: a parcela vem colada na descrição
    ('ProdutosUOL 09/12') e tem a mesma cara. O que distingue a coluna é a data
    **abrir** a linha — não ter nada logo à esquerda dela.
    """
    por_linha = {}
    for w in palavras:
        por_linha.setdefault(round(w['top'] / 3), []).append(w)

    posicoes = []
    for linha in por_linha.values():
        linha.sort(key=lambda w: w['x0'])
        for i, w in enumerate(linha):
            if not RE_DATA.match(w['text']):
                continue
            vizinho = linha[i - 1] if i else None
            # Nas páginas cheias o valor da coluna da esquerda termina a ~27 px
            # da data da direita: folga não basta. Data logo depois de um valor
            # é começo de outro lançamento — portanto de outra coluna.
            if (vizinho is None or w['x0'] - vizinho['x1'] > 30
                    or RE_VALOR.match(vizinho['text'])):
                posicoes.append(w['x0'])

    posicoes.sort()
    if not posicoes:
        return []
    grupos = [[posicoes[0]]]
    for x in posicoes[1:]:
        if x - grupos[-1][-1] <= TOLERANCIA_COLUNA:
            grupos[-1].append(x)
        else:
            grupos.append([x])
    # Coluna de verdade tem várias datas; uma data solta é vencimento,
    # fechamento, data do documento.
    return [min(g) for g in grupos if len(g) >= 3]


def _faixas(palavras, limites):
    """Divide as palavras nas faixas horizontais de cada coluna."""
    faixas = []
    for i, inicio in enumerate(limites):
        fim = limites[i + 1] if i + 1 < len(limites) else float('inf')
        # A faixa começa um pouco antes da data para pegar o texto encostado.
        faixas.append([w for w in palavras if inicio - 6 <= w['x0'] < fim - 6])
    return faixas


def _linhas(palavras):
    """Agrupa palavras em linhas pela posição vertical."""
    linhas = {}
    for w in palavras:
        linhas.setdefault(round(w['top'] / 3), []).append(w)
    return [sorted(linhas[k], key=lambda w: w['x0']) for k in sorted(linhas)]


def _data_do_lancamento(ddmm, referencia):
    """'27/03' + mês de referência -> data completa.

    A fatura só traz dia/mês. Mês maior que o da referência é do ano anterior
    (compra de dezembro aparecendo na fatura de janeiro).
    """
    try:
        dia, mes = (int(x) for x in ddmm.split('/'))
        ano = referencia.year - 1 if mes > referencia.month else referencia.year
        return date(ano, mes, dia)
    except (ValueError, TypeError):
        return None


def _lancamentos_da_faixa(faixa, referencia, ja_fechados=(), estado=None):
    """Percorre uma coluna de cima para baixo, trocando de cartão nos rótulos.

    ``estado`` traz o que vinha aberto da coluna anterior: o cartão e a seção
    (nacional, internacional ou parcelas futuras). ``ja_fechados`` são os blocos
    (seção, final) que já terminaram: se um deles reabrir na mesma seção, é o
    resumo do fim da fatura e não é contado de novo.

    Devolve (lançamentos, totais declarados, blocos fechados, estado), com os
    totais em ``{'nacional': {final: valor}, 'internacional': {...},
    'geral': valor, 'nomes': {final: nome impresso}}``.
    """
    estado = dict(estado or {'cartao': None, 'secao': NACIONAL})
    achados, fechados = [], set()
    declarados = {NACIONAL: {}, INTERNACIONAL: {}, 'geral': None, 'nomes': {}}
    ultimo = None   # o lançamento anterior, para pegar a linha de baixo (categoria)

    for linha in _linhas(faixa):
        texto = ' '.join(w['text'] for w in linha).strip()
        if not texto:
            continue
        compacto = _compacto(texto)
        cartao = estado['cartao']

        secao = _secao_da_linha(compacto)
        if secao:
            if secao != estado['secao']:
                estado['cartao'] = None
            estado['secao'] = secao
            ultimo = None
            continue

        if 'totaldoslancamentosatuais' in compacto:
            valor = next((para_decimal(w['text']) for w in reversed(linha)
                          if RE_VALOR.match(w['text'])), None)
            if valor is not None:
                declarados['geral'] = valor
            continue

        if estado['secao'] == FUTURAS:
            # Parcelas das próximas faturas: não é cobrança deste mês.
            continue

        marca_cartao = RE_ABRE_CARTAO.search(texto)
        fecha = RE_FECHA_CARTAO.search(_sem_acento(texto))

        if fecha and marca_cartao:
            valor = next((para_decimal(w['text']) for w in reversed(linha)
                          if RE_VALOR.match(w['text'])), None)
            final = marca_cartao.group(1)
            if valor is not None:
                declarados[NACIONAL][final] = valor
            fechados.add((NACIONAL, final))
            # O total pode ter caído na linha seguinte; fica pendente.
            estado['cartao'] = None if valor is not None else ('FECHANDO', final)
            ultimo = None
            continue

        # Total que ficou sozinho embaixo do rótulo.
        if (isinstance(cartao, tuple) and len(linha) == 1
                and RE_VALOR.match(linha[0]['text'])):
            declarados[NACIONAL][cartao[1]] = para_decimal(linha[0]['text'])
            estado['cartao'] = None
            continue

        if marca_cartao:
            novo = marca_cartao.group(1)
            chave = (estado['secao'], novo)
            # Bloco reaberto na mesma seção = repetição no resumo; ignora até o próximo rótulo.
            estado['cartao'] = None if chave in ja_fechados or chave in fechados else novo
            nome = texto[:marca_cartao.start()].strip()
            if nome and estado['cartao'] and not _compacto(nome).startswith('lancamentos'):
                declarados['nomes'].setdefault(novo, nome)
            ultimo = None
            continue

        if not isinstance(cartao, str):
            continue

        if estado['secao'] == INTERNACIONAL:
            # Fechamento do bloco internacional: IOF entra como lançamento (é
            # cobrança do cartão) e o total confere a leitura.
            valor_linha = next((para_decimal(w['text']) for w in reversed(linha)
                                if RE_VALOR.match(w['text'])), None)
            if 'repassedeiof' in compacto and valor_linha is not None:
                achados.append({
                    'last4': cartao, 'data': ultimo['data'] if ultimo else referencia,
                    'valor': valor_linha, 'estabelecimento': 'Repasse de IOF (compras internacionais)',
                    'parcela': '', 'internacional': True, 'iof': True,
                    'categoria': 'IOF', 'cidade': '', 'detalhe': ''})
                ultimo = None
                continue
            if 'totallancamentosinter' in compacto:
                if valor_linha is not None:
                    declarados[INTERNACIONAL][cartao] = valor_linha
                fechados.add((INTERNACIONAL, cartao))
                estado['cartao'] = None
                ultimo = None
                continue
            if 'totaltransacoesinter' in compacto:
                continue
            if 'conversao' in compacto and ultimo is not None:
                cambio = re.search(r'(\d+,\d+)\s*$', texto)
                ultimo['detalhe'] = (ultimo.get('detalhe', '') + (f' · câmbio R$ {cambio.group(1)}' if cambio else '')).strip(' ·')
                continue

        data_word = next((w for w in linha if RE_DATA.match(w['text'])), None)
        if not data_word or data_word is not linha[0]:
            # A linha de baixo do lançamento: "ALIMENTAÇÃO .VITORIA" no nacional,
            # "MenloPark 25,00 USD 25,00" no internacional.
            if ultimo is not None and not ultimo.get('_completo'):
                if estado['secao'] == INTERNACIONAL:
                    # Cidade até o primeiro número; o resto é o valor na moeda de origem.
                    corte = next((i for i, w in enumerate(linha) if RE_VALOR.match(w['text'])), len(linha))
                    ultimo['cidade'] = ' '.join(w['text'] for w in linha[:corte]).title()
                    ultimo['detalhe'] = ' '.join(w['text'] for w in linha[corte:])
                elif '.' in texto and not any(RE_VALOR.match(w['text']) for w in linha):
                    categoria, _, cidade = texto.partition('.')
                    ultimo['categoria'] = categoria.strip().title()
                    ultimo['cidade'] = cidade.strip().title()
                ultimo['_completo'] = True
            continue
        # O valor nem sempre é a última palavra: às vezes um pedaço de texto
        # de outro elemento da página encosta na linha ('… 131,47 L'). Vale o
        # último que tem cara de valor.
        indice_valor = next((i for i in range(len(linha) - 1, 0, -1)
                             if RE_VALOR.match(linha[i]['text'])), None)
        if indice_valor is None:
            continue
        valor_word = linha[indice_valor]

        quando = _data_do_lancamento(data_word['text'], referencia)
        valor = para_decimal(valor_word['text'])
        if quando is None or valor is None:
            continue

        descricao = ' '.join(w['text'] for w in linha[1:indice_valor]).strip()
        parcela = ''
        pedaco = RE_PARCELA.search(descricao)
        if pedaco and int(pedaco.group(2)) > 1:
            parcela = f'{pedaco.group(1)}/{pedaco.group(2)}'
            descricao = descricao[:pedaco.start()].strip()

        ultimo = {'last4': cartao, 'data': quando, 'valor': valor,
                  'estabelecimento': descricao[:200], 'parcela': parcela,
                  'internacional': estado['secao'] == INTERNACIONAL, 'iof': False,
                  'categoria': '', 'cidade': '', 'detalhe': ''}
        achados.append(ultimo)
    for item in achados:
        item.pop('_completo', None)
    return achados, declarados, fechados, estado


def _datas_do_cabecalho(pdf):
    """Vencimento e emissão impressos na primeira página (quando houver)."""
    from datetime import datetime
    texto = pdf.pages[0].extract_text() or '' if pdf.pages else ''
    datas = {}
    for chave, regex in (('vencimento', RE_VENCIMENTO), ('emissao', RE_EMISSAO)):
        achado = regex.search(texto)
        if achado:
            try:
                datas[chave] = datetime.strptime(achado.group(1), '%d/%m/%Y').date()
            except ValueError:
                pass
    return datas


def ler_fatura(arquivo, referencia=None):
    """Lê o PDF e devolve os lançamentos por cartão, com a conferência do total.

    Sem ``referencia``, o mês vem do vencimento impresso na fatura — é ele que
    diz o ano de um "04/11" (compra parcelada do ano anterior).
    """
    import pdfplumber

    lancamentos = []
    declarados = {NACIONAL: {}, INTERNACIONAL: {}}
    nomes, total_geral = {}, None
    # Bloco (seção, final) já fechado. A fatura repete os blocos no resumo do
    # fim; reprocessar duplicaria tudo. Deduplicar por conteúdo NÃO serve:
    # três cobranças iguais da Vivo no mesmo dia são três cobranças de verdade.
    ja_fechados = set()
    estado = {'cartao': None, 'secao': NACIONAL}

    with pdfplumber.open(arquivo) as pdf:
        cabecalho = _datas_do_cabecalho(pdf)
        if referencia is None:
            base = cabecalho.get('vencimento') or date.today()
            referencia = base.replace(day=1)
        for pagina in pdf.pages:
            palavras = pagina.extract_words(x_tolerance=1.5, y_tolerance=2)
            limites = _colunas_de_data(palavras)
            if not limites:
                continue
            for faixa in _faixas(palavras, limites):
                # O bloco de um cartão atravessa a coluna e a página: o cartão
                # corrente vai junto, senão a segunda metade do bloco vira
                # lançamento sem dono e some.
                achados, totais, fechados, estado = _lancamentos_da_faixa(
                    faixa, referencia, ja_fechados, estado)
                for secao in (NACIONAL, INTERNACIONAL):
                    for last4, valor in totais[secao].items():
                        if valor is not None:
                            declarados[secao][last4] = valor
                for last4, nome in totais['nomes'].items():
                    nomes.setdefault(last4, nome)
                if totais['geral'] is not None:
                    total_geral = totais['geral']
                lancamentos.extend(achados)
                ja_fechados |= fechados

    cartoes = {}
    finais = ({l['last4'] for l in lancamentos} | set(declarados[NACIONAL])
              | set(declarados[INTERNACIONAL]))
    for last4 in sorted(finais):
        do_cartao = [l for l in lancamentos if l['last4'] == last4]
        nacional = [l for l in do_cartao if not l['internacional']]
        internacional = [l for l in do_cartao if l['internacional']]
        decl_nac = declarados[NACIONAL].get(last4)
        decl_int = declarados[INTERNACIONAL].get(last4)
        somado = sum((l['valor'] for l in do_cartao), ZERO)
        lido_int = sum((l['valor'] for l in internacional), ZERO)
        partes = [v for v in (decl_nac, decl_int) if v is not None]
        # Sem o total nacional não dá para conferir: o bloco está incompleto.
        declarado = sum(partes, ZERO) if decl_nac is not None or (decl_int is not None and not nacional) else None
        cartoes[last4] = {
            'nome': nomes.get(last4, ''),
            'declarado': declarado,
            'declarado_nacional': decl_nac,
            'declarado_internacional': decl_int,
            'lido': somado,
            'lido_nacional': somado - lido_int,
            'lido_internacional': lido_int,
            'lancamentos': len(do_cartao),
            'internacionais': len(internacional),
            'confere': declarado is not None and somado == declarado,
            'diferenca': (somado - declarado) if declarado is not None else None,
        }

    total_lido = sum((l['valor'] for l in lancamentos), ZERO)
    return {
        'lancamentos': lancamentos,
        'cartoes': cartoes,
        'referencia': referencia,
        'vencimento': cabecalho.get('vencimento'),
        'emissao': cabecalho.get('emissao'),
        'total_declarado': total_geral,
        'total_lido': total_lido,
        'confere_total': total_geral is not None and total_lido == total_geral,
        'confere': bool(cartoes) and all(c['confere'] for c in cartoes.values()),
    }


# ─── Conciliação ─────────────────────────────────────────────────────────────

# Quanto a data pode diferir entre a fatura e o lançamento do portal. A fatura
# registra quando a compra foi processada, que nem sempre é o dia em que a
# pessoa gastou.
TOLERANCIA_DIAS = 3


def _chave_estabelecimento(texto):
    """Nome comparável: sem acento, sem pontuação, sem espaço, em maiúsculas.

    A fatura vem sem espaços ('PADARIANOVAREPUBLICA') e o portal, com eles.
    Comparar sem separador algum é o que faz os dois se encontrarem.
    """
    return re.sub(r'[^A-Z0-9]', '', _sem_acento(texto).upper())


def _parecidos(a, b):
    """Um nome contém o outro — suficiente para 'PADARIA NOVA REPUBLICA'
    casar com 'PADARIANOVAREPUBLICA*SP'."""
    ka, kb = _chave_estabelecimento(a), _chave_estabelecimento(b)
    if not ka or not kb:
        return False
    menor, maior = sorted((ka, kb), key=len)
    return len(menor) >= 5 and menor in maior


def janela_do_portal(lancamentos):
    """(início, fim) dos gastos do portal a comparar com estes lançamentos.

    A janela sai das compras do mês — não das parcelas. Uma parcela 11/12 traz a
    data da compra original, de quase um ano atrás: puxar a janela até lá
    enchia o "sem cobrança" com tudo o que foi lançado no portal desde então.
    """
    from datetime import timedelta

    if not lancamentos:
        return None, None
    # Estorno de centavos de parcela antiga ('04/08 TINTOLAC -0,04') também traz
    # data velha: só compra (valor positivo) define a janela.
    do_mes = [i['data'] for i in lancamentos
              if not i.get('iof') and i['valor'] > 0
              and (not i.get('parcela') or i['parcela'].startswith('01/'))]
    datas = do_mes or [i['data'] for i in lancamentos]
    folga = timedelta(days=TOLERANCIA_DIAS)
    return min(datas) - folga, max(datas) + folga


def categoria_do_lancamento(item):
    if item.get('iof'):
        return 'IOF'
    if item.get('internacional'):
        return 'Internacional'
    return item.get('categoria') or 'Sem categoria'


def por_categoria(lancamentos):
    """Total da fatura por categoria, do maior para o menor, com o percentual."""
    somas = {}
    for item in lancamentos:
        chave = categoria_do_lancamento(item)
        somas[chave] = somas.get(chave, ZERO) + item['valor']
    total = sum(somas.values(), ZERO)
    linhas = sorted(somas.items(), key=lambda kv: -kv[1])
    return [{'categoria': nome, 'valor': valor,
             'percentual': round(float(valor * 100 / total), 1) if total else 0}
            for nome, valor in linhas]


def parcelas(item):
    """'02/03' -> (2, 3); sem parcela -> None."""
    pedaco = re.match(r'^(\d{1,2})/(\d{1,2})$', item.get('parcela') or '')
    if not pedaco:
        return None
    atual, total = int(pedaco.group(1)), int(pedaco.group(2))
    return (atual, total) if total > 1 else None


def compra_antiga(item):
    """Parcela 02/xx em diante: a compra foi feita (e lançada) num mês anterior."""
    p = parcelas(item)
    return bool(p and p[0] > 1)


def _valor_bate(item, gasto):
    """Mesmo valor — ou, numa parcela, o portal com a compra inteira (parcela × total).

    O portal costuma registrar a compra uma vez, pelo total; a fatura cobra a
    parcela. Arredondamento de centavo por parcela é tolerado.
    """
    if gasto.valor == item['valor']:
        return True
    p = parcelas(item)
    return bool(p and abs(gasto.valor - item['valor'] * p[1]) <= Decimal('0.01') * p[1])


def conciliar(lancamentos_fatura, gastos, janela=None):
    """Cruza a fatura com os gastos lançados no portal.

    A regra de casamento é, em ordem: **mesmo valor** (numa parcela, também o
    total da compra) e data dentro da tolerância; entre os candidatos, ganha o
    de valor exato, depois o de estabelecimento parecido e, por fim, o de data
    mais próxima. Numa parcela a data da fatura é a da compra original, então
    ``gastos`` pode trazer lançamentos de meses atrás para elas — e ``janela``
    diz quais gastos contam como "sem cobrança" (os de fora só servem de par). Valor é o critério duro de propósito —
    conciliar por nome parecido com valor diferente esconderia justamente o
    erro que se quer achar.

    Devolve quatro listas:

    * ``conferidos``     — bateram valor e data
    * ``divergentes``    — mesmo estabelecimento e data próxima, valor diferente
    * ``so_na_fatura``   — cobrado e não lançado no portal
    * ``so_no_extrato``  — lançado no portal e ausente da fatura
    """
    from datetime import timedelta

    pendentes = list(gastos)
    conferidos, divergentes, so_na_fatura = [], [], []

    for item in lancamentos_fatura:
        candidatos = [
            g for g in pendentes
            if _valor_bate(item, g)
            and abs((g.data_gasto - item['data']).days) <= TOLERANCIA_DIAS
        ]
        if candidatos:
            candidatos.sort(key=lambda g: (
                g.valor != item['valor'],
                not _parecidos(g.estabelecimento, item['estabelecimento']),
                abs((g.data_gasto - item['data']).days),
            ))
            escolhido = candidatos[0]
            pendentes.remove(escolhido)
            conferidos.append({'fatura': item, 'gasto': escolhido,
                               'parcelado': escolhido.valor != item['valor']})
            continue

        # Sem valor igual: procura o mesmo estabelecimento por perto. Se achar,
        # é divergência de valor — o caso que mais interessa ao financeiro.
        perto = [
            g for g in pendentes
            if abs((g.data_gasto - item['data']).days) <= TOLERANCIA_DIAS
            and _parecidos(g.estabelecimento, item['estabelecimento'])
        ]
        if perto:
            perto.sort(key=lambda g: abs((g.data_gasto - item['data']).days))
            escolhido = perto[0]
            pendentes.remove(escolhido)
            divergencia = {'fatura': item, 'gasto': escolhido,
                           'diferenca': item['valor'] - escolhido.valor}
            p = parcelas(item)
            if p:
                # Parcela contra a compra inteira: a diferença que importa é a do total.
                divergencia['total_parcelado'] = item['valor'] * p[1]
                divergencia['diferenca'] = divergencia['total_parcelado'] - escolhido.valor
            divergentes.append(divergencia)
            continue

        so_na_fatura.append(item)

    if janela and janela[0]:
        # Gasto antigo trazido só para casar com parcela não é "sem cobrança".
        pendentes = [g for g in pendentes if janela[0] <= g.data_gasto <= janela[1]]
        # "Lançado no portal" é o que foi lançado no período desta fatura.
        gastos = [g for g in gastos if janela[0] <= g.data_gasto <= janela[1]]

    total_fatura = sum((i['valor'] for i in lancamentos_fatura), ZERO)
    total_extrato = sum((g.valor for g in gastos), ZERO)
    total_conferido = sum((c['fatura']['valor'] for c in conferidos), ZERO)

    # Uma linha por item, na ordem da data, para a tabela única da tela e a
    # planilha: a situação vira filtro, em vez de quatro tabelas separadas.
    linhas = (
        [{'situacao': 'conferido', 'fatura': c['fatura'], 'gasto': c['gasto'], 'diferenca': ZERO,
          'parcelado': c.get('parcelado', False)} for c in conferidos]
        + [{'situacao': 'divergente', 'fatura': d['fatura'], 'gasto': d['gasto'],
            'diferenca': d['diferenca'], 'total_parcelado': d.get('total_parcelado')} for d in divergentes]
        + [{'situacao': 'nao_lancado', 'fatura': i, 'gasto': None, 'diferenca': i['valor']}
           for i in so_na_fatura]
        + [{'situacao': 'sem_cobranca', 'fatura': None, 'gasto': g, 'diferenca': -g.valor}
           for g in pendentes]
    )
    linhas.sort(key=lambda l: (l['fatura']['data'] if l['fatura'] else l['gasto'].data_gasto))

    return {
        'linhas': linhas,
        'total_conferido': total_conferido,
        'percentual_conciliado': (round(float(total_conferido * 100 / total_fatura), 1)
                                  if total_fatura else 0),
        'por_categoria': por_categoria(lancamentos_fatura),
        'conferidos': conferidos,
        'divergentes': divergentes,
        'so_na_fatura': so_na_fatura,
        'so_no_extrato': sorted(pendentes, key=lambda g: g.data_gasto),
        'total_fatura': total_fatura,
        'total_extrato': total_extrato,
        'diferenca_total': total_fatura - total_extrato,
        'total_divergencia': sum((d['diferenca'] for d in divergentes), ZERO),
        'total_so_na_fatura': sum((i['valor'] for i in so_na_fatura), ZERO),
        'total_so_no_extrato': sum((g.valor for g in pendentes), ZERO),
    }
