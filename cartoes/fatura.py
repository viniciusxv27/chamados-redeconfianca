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
# Parcela colada no fim da descrição: 'COMPERIMPORTACAO02/10'
RE_PARCELA = re.compile(r'\s*(\d{2})/(\d{2})\s*$')

# Distância máxima entre dois x da mesma coluna de datas.
TOLERANCIA_COLUNA = 20


def _sem_acento(texto):
    return unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()


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
            if vizinho is None or w['x0'] - vizinho['x1'] > 30:
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


def _lancamentos_da_faixa(faixa, referencia, ja_fechados=(), cartao=None):
    """Percorre uma coluna de cima para baixo, trocando de cartão nos rótulos.

    ``ja_fechados`` traz os cartões cujo bloco já terminou: se um deles reabrir,
    é o resumo do fim da fatura e não deve ser contado de novo. ``cartao`` é o
    bloco que vinha aberto da coluna anterior.
    """
    achados, declarados, fechados = [], {}, set()

    for linha in _linhas(faixa):
        texto = ' '.join(w['text'] for w in linha).strip()
        if not texto:
            continue

        marca_cartao = RE_ABRE_CARTAO.search(texto)
        fecha = RE_FECHA_CARTAO.search(_sem_acento(texto))

        if fecha and marca_cartao:
            valor = next((para_decimal(w['text']) for w in reversed(linha)
                          if RE_VALOR.match(w['text'])), None)
            if valor is not None:
                declarados[marca_cartao.group(1)] = valor
            else:
                # O total caiu na linha seguinte; fica pendente.
                declarados.setdefault(marca_cartao.group(1), None)
            fechados.add(marca_cartao.group(1))
            cartao = ('FECHANDO', marca_cartao.group(1))
            continue

        # Total que ficou sozinho embaixo do rótulo.
        if (isinstance(cartao, tuple) and len(linha) == 1
                and RE_VALOR.match(linha[0]['text'])):
            declarados[cartao[1]] = para_decimal(linha[0]['text'])
            cartao = None
            continue

        if marca_cartao:
            novo = marca_cartao.group(1)
            # Bloco reaberto = repetição no resumo; ignora até o próximo rótulo.
            cartao = None if novo in ja_fechados or novo in fechados else novo
            continue

        if not isinstance(cartao, str):
            continue

        data_word = next((w for w in linha if RE_DATA.match(w['text'])), None)
        if not data_word or data_word is not linha[0]:
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

        achados.append({'last4': cartao, 'data': quando, 'valor': valor,
                        'estabelecimento': descricao[:200], 'parcela': parcela})
    return achados, declarados, fechados, cartao


def ler_fatura(arquivo, referencia=None):
    """Lê o PDF e devolve os lançamentos por cartão, com a conferência do total."""
    import pdfplumber

    referencia = referencia or date.today().replace(day=1)

    lancamentos, declarados = [], {}
    # Cartão cujo bloco já foi fechado. A fatura repete os blocos no resumo do
    # fim; reprocessar duplicaria tudo. Deduplicar por conteúdo NÃO serve:
    # três cobranças iguais da Vivo no mesmo dia são três cobranças de verdade.
    ja_fechados = set()
    cartao_atual = None

    with pdfplumber.open(arquivo) as pdf:
        for pagina in pdf.pages:
            palavras = pagina.extract_words(x_tolerance=1.5, y_tolerance=2)
            limites = _colunas_de_data(palavras)
            if not limites:
                continue
            for faixa in _faixas(palavras, limites):
                # O bloco de um cartão atravessa a coluna e a página: o cartão
                # corrente vai junto, senão a segunda metade do bloco vira
                # lançamento sem dono e some.
                achados, totais, fechados, cartao_atual = _lancamentos_da_faixa(
                    faixa, referencia, ja_fechados, cartao_atual)
                for last4, valor in totais.items():
                    if valor is not None:
                        declarados[last4] = valor
                lancamentos.extend(achados)
                ja_fechados |= fechados

    cartoes = {}
    for last4 in sorted({l['last4'] for l in lancamentos} | set(declarados)):
        do_cartao = [l for l in lancamentos if l['last4'] == last4]
        somado = sum((l['valor'] for l in do_cartao), ZERO)
        declarado = declarados.get(last4)
        cartoes[last4] = {
            'declarado': declarado,
            'lido': somado,
            'lancamentos': len(do_cartao),
            'confere': declarado is not None and somado == declarado,
            'diferenca': (somado - declarado) if declarado is not None else None,
        }

    return {
        'lancamentos': lancamentos,
        'cartoes': cartoes,
        'referencia': referencia,
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


def conciliar(lancamentos_fatura, gastos):
    """Cruza a fatura com os gastos lançados no portal.

    A regra de casamento é, em ordem: **mesmo valor** e data dentro da
    tolerância; entre os candidatos, ganha o de estabelecimento parecido e,
    depois, o de data mais próxima. Valor é o critério duro de propósito —
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
            if g.valor == item['valor']
            and abs((g.data_gasto - item['data']).days) <= TOLERANCIA_DIAS
        ]
        if candidatos:
            candidatos.sort(key=lambda g: (
                not _parecidos(g.estabelecimento, item['estabelecimento']),
                abs((g.data_gasto - item['data']).days),
            ))
            escolhido = candidatos[0]
            pendentes.remove(escolhido)
            conferidos.append({'fatura': item, 'gasto': escolhido})
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
            divergentes.append({
                'fatura': item, 'gasto': escolhido,
                'diferenca': item['valor'] - escolhido.valor,
            })
            continue

        so_na_fatura.append(item)

    total_fatura = sum((i['valor'] for i in lancamentos_fatura), ZERO)
    total_extrato = sum((g.valor for g in gastos), ZERO)

    return {
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
