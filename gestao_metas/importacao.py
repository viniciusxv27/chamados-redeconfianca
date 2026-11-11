"""Entradas do Gerenciamento de metas: tudo vira uma prévia para conferir antes de gravar.

1. **Base de produtos / Base de serviços** (Vivo GO, xlsx ou csv) — soma o
   realizado por loja, mês e pilar. Os nomes de coluna seguem as tabelas
   ``vendas_produto`` / ``vendas_servicos`` do banco de vendas (que são
   carregadas dessas mesmas bases), aceitando acento, caixa e "_" ou espaço.
2. **Banco de vendas** — a mesma soma, lida direto do MySQL do Vivo GO (só
   leitura), um mês por vez.
3. **Arquivo de metas da Vivo** — a META VIVO de cada loja em cada pilar.
4. **Planilha antiga** — o arquivo "METAS <mês>" montado à mão (uma aba por
   pilar), para o módulo já nascer com o histórico.

Regras do realizado (conferidas contra a planilha de novembro/2026):
- Serviços: só ``Venda ativa`` = 1 e ``Status do serviço`` = Confirmado; o valor
  é a coluna ``Receita``.
- Fixa: R$ = todo o pilar Fixa; QTD TV = serviço técnico "Alta TV"; QTD FIXA =
  "Alta Banda Larga" + "Portabilidade Fixa".
- Produtos: ``Valor líquido de venda do produto`` por pilar (Smartphone,
  Eletrônicos, Essenciais).
"""
import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from . import servicos
from .models import (ABA_DO_PILAR, PILARES_PRODUTO, PILARES_SERVICO, Competencia, ImportacaoMetas, LojaMeta,
                     MetaLoja, Realizado)
from .servicos import normalizar, sem_acento, somar_meses

LIMITE_ARQUIVO = 60 * 1024 * 1024

PILAR_DO_TEXTO = {
    'MOVEL': 'movel', 'FIXA': 'fixa', 'SVA': 'sva', 'SEGURO': 'seguros', 'SEGUROS': 'seguros',
    'SMARTPHONE': 'smartphone', 'SMARTPHONES': 'smartphone', 'ELETRONICOS': 'eletronicos',
    'ELETRONICO': 'eletronicos', 'ESSENCIAIS': 'essenciais', 'ESSENCIAL': 'essenciais',
}
TECNICO_TV = {'ALTA TV'}
TECNICO_FIXA = {'ALTA BANDA LARGA', 'PORTABILIDADE FIXA'}
NAO_SAO_LOJAS = {'REDE', 'TOTAL', 'TOTAL GERAL', 'RJ', 'ES', 'SP', 'SEDE ADMINISTRATIVA'}


def cab(texto):
    """Cabeçalho comparável: sem acento, maiúsculas, "_" vira espaço."""
    return ' '.join(sem_acento(texto).upper().replace('_', ' ').split())


def pilar_do_texto(texto):
    return PILAR_DO_TEXTO.get(cab(texto))


# ---------------------------------------------------------------------------
# Leitura de planilha / csv em linhas
# ---------------------------------------------------------------------------
def _linhas_do_arquivo(arquivo, nome):
    """Gera listas de células. xlsx: a primeira aba com dados; csv: ; ou , e utf-8 / latin-1."""
    nome = (nome or '').lower()
    if nome.endswith('.csv') or nome.endswith('.txt'):
        bruto = arquivo.read()
        for codificacao in ('utf-8-sig', 'latin-1'):
            try:
                texto = bruto.decode(codificacao)
                break
            except UnicodeDecodeError:
                continue
        amostra = texto[:5000]
        sep = ';' if amostra.count(';') > amostra.count(',') else ','
        if amostra.count('\t') > max(amostra.count(';'), amostra.count(',')):
            sep = '\t'
        return [list(l) for l in csv.reader(io.StringIO(texto), delimiter=sep)], ['csv']
    import openpyxl
    try:
        wb = openpyxl.load_workbook(arquivo, read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError('Não consegui abrir o arquivo como planilha Excel (.xlsx): {}'.format(exc))
    abas = wb.sheetnames
    for ws in wb.worksheets:
        linhas = [list(l) for l in ws.iter_rows(values_only=True)]
        if any(any(c not in (None, '') for c in l) for l in linhas[:30]):
            wb.close()
            return linhas, abas
    wb.close()
    return [], abas


def _data(valor):
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, (int, float)) and 20000 < valor < 80000:  # número de série do Excel
        return date(1899, 12, 30) + timedelta(days=int(valor))
    texto = str(valor or '').strip()[:10]
    for formato in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%y'):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    return None


def _num(valor):
    try:
        n = servicos.numero(valor)
    except ValueError:
        return None
    return float(n) if n is not None else None


ATIVA_SIM = {'1', 'SIM', 'S', 'TRUE', 'VERDADEIRO', 'ATIVA', 'ATIVO', '1.0'}


# ---------------------------------------------------------------------------
# 1. Bases de vendas
# ---------------------------------------------------------------------------
def _achar_coluna(cabecalhos, exatos=(), contem=(), evitar=()):
    for i, c in enumerate(cabecalhos):
        if c in exatos:
            return i
    for termo in contem:
        for i, c in enumerate(cabecalhos):
            if termo in c and not any(e in c for e in evitar):
                return i
    return None


def ler_base(arquivo, nome):
    """Lê uma base (produtos ou serviços) e devolve {'tipo', 'agregado', 'linhas_lidas', 'ignoradas', ...}."""
    linhas, _ = _linhas_do_arquivo(arquivo, nome)
    if not linhas:
        raise ValueError('O arquivo está vazio.')
    inicio = None
    for i, linha in enumerate(linhas[:30]):
        cabs = [cab(c) for c in linha]
        if 'PILAR' in cabs and any(c == 'PDV' or c.startswith('PDV') for c in cabs):
            inicio = i
            break
    if inicio is None:
        raise ValueError('Não achei o cabeçalho com as colunas PDV e Pilar nas primeiras linhas.')
    cabs = [cab(c) for c in linhas[inicio]]
    col = {
        'pdv': _achar_coluna(cabs, exatos=('PDV',), contem=('PDV',), evitar=('ID', 'UF')),
        'data': _achar_coluna(cabs, exatos=('DATA DA VENDA',), contem=('DATA DA VENDA', 'DATA VENDA')),
        'pilar': _achar_coluna(cabs, exatos=('PILAR',)),
        'valor_produto': _achar_coluna(cabs, contem=('VALOR LIQUIDO DE VENDA DO PRODUTO', 'VALOR LIQUIDO')),
        'receita': _achar_coluna(cabs, exatos=('RECEITA',), contem=('RECEITA CALCULADA',)),
        'ativa': _achar_coluna(cabs, exatos=('VENDA ATIVA',)),
        'status': _achar_coluna(cabs, exatos=('STATUS DO SERVICO',)),
        'tecnico': _achar_coluna(cabs, exatos=('SERVICO TECNICO',)),
    }
    if col['receita'] is not None and (col['tecnico'] is not None or col['status'] is not None):
        tipo, col_valor, aceitos = 'servicos', col['receita'], PILARES_SERVICO
    elif col['valor_produto'] is not None:
        tipo, col_valor, aceitos = 'produtos', col['valor_produto'], PILARES_PRODUTO
    else:
        raise ValueError('Não reconheci a base: falta "Receita" (base de serviços) ou '
                         '"Valor líquido de venda do produto" (base de produtos).')
    if col['data'] is None:
        raise ValueError('Falta a coluna "Data da venda".')
    agregado = defaultdict(lambda: [0.0, 0, 0])
    ignoradas = Counter()
    lidas = 0
    for linha in linhas[inicio + 1:]:
        if not linha or all(c in (None, '') for c in linha):
            continue
        lidas += 1

        def cel(chave):
            i = col[chave]
            return linha[i] if (i is not None and i < len(linha)) else None

        if col['ativa'] is not None and cab(cel('ativa')) not in ATIVA_SIM:
            ignoradas['venda inativa'] += 1
            continue
        if tipo == 'servicos' and col['status'] is not None and cab(cel('status')) != 'CONFIRMADO':
            ignoradas['serviço não confirmado'] += 1
            continue
        pilar = pilar_do_texto(cel('pilar'))
        if pilar not in aceitos:
            ignoradas['pilar fora da meta ({})'.format(str(cel('pilar') or 'vazio').strip())] += 1
            continue
        dia = _data(cel('data'))
        if not dia:
            ignoradas['sem data da venda'] += 1
            continue
        pdv = ' '.join(str(cel('pdv') or '').split())
        if not pdv:
            ignoradas['sem PDV'] += 1
            continue
        chave = (pdv.upper(), dia.strftime('%Y-%m'), pilar)
        soma = agregado[chave]
        soma[0] += _num(cel('receita') if tipo == 'servicos' else cel('valor_produto')) or 0.0
        if pilar == 'fixa':
            tecnico = cab(cel('tecnico'))
            if tecnico in TECNICO_TV:
                soma[1] += 1
            elif tecnico in TECNICO_FIXA:
                soma[2] += 1
    if not agregado:
        raise ValueError('Nenhuma venda aproveitável no arquivo ({} linhas lidas).'.format(lidas))
    return {'tipo': tipo, 'agregado': agregado, 'linhas_lidas': lidas, 'ignoradas': dict(ignoradas)}


def _realizados_da_soma(agregado, tipo_base):
    """{(pdv, mes, pilar): [valor, tv, fixa]} → linhas da prévia [pdv, mes, pilar, valor, tv, fixa, total]."""
    saida = []
    for (pdv, mes, pilar), (valor, tv, fx) in sorted(agregado.items()):
        if pilar == 'fixa':
            saida.append([pdv, mes, pilar, round(valor, 2), tv, fx, None])
        else:
            saida.append([pdv, mes, pilar, round(valor, 2), None, None, None])
    return saida


def previa_bases(arquivos, user, substituir=True):
    """Uma prévia para um ou mais arquivos de base (produtos e serviços juntos)."""
    realizados, nomes, tipos, ignoradas, lidas = [], [], [], Counter(), 0
    for arquivo in arquivos:
        if getattr(arquivo, 'size', 0) > LIMITE_ARQUIVO:
            raise ValueError('{}: arquivo grande demais (máx. 60 MB).'.format(arquivo.name))
        lido = ler_base(arquivo, arquivo.name)
        realizados += _realizados_da_soma(lido['agregado'], lido['tipo'])
        nomes.append(arquivo.name)
        tipos.append(lido['tipo'])
        ignoradas.update(lido['ignoradas'])
        lidas += lido['linhas_lidas']
    rotulos = {'servicos': 'serviços', 'produtos': 'produtos'}
    dados = _montar_previa_realizado(realizados)
    dados.update({'linhas_lidas': lidas, 'ignoradas': dict(ignoradas), 'bases': [rotulos[t] for t in tipos]})
    return ImportacaoMetas.objects.create(
        tipo=ImportacaoMetas.BASE, arquivo_nome=', '.join(nomes)[:255], dados=dados, substituir=substituir,
        criado_por=user, resumo='{} linha(s) · base de {}'.format(lidas, ' e '.join(rotulos[t] for t in tipos))[:400])


def _montar_previa_realizado(realizados):
    mapa = servicos.MapaLojas()
    nao = sorted({r[0] for r in realizados if mapa.achar(r[0]) is None and normalizar(r[0]) not in NAO_SAO_LOJAS})
    return {
        'realizados': realizados, 'nao_reconhecidas': nao,
        'meses': sorted({r[1] for r in realizados}), 'pilares': sorted({r[2] for r in realizados}),
    }


# ---------------------------------------------------------------------------
# 2. Banco de vendas (MySQL do Vivo GO, só leitura)
# ---------------------------------------------------------------------------
def buscar_mes_no_banco(mes):
    """Soma do mês por PDV e pilar, direto do banco de vendas. Mesmas regras das bases."""
    import pymysql
    from simulator.sql_realizado import _mysql_config

    inicio = servicos.primeiro_dia(mes)
    fim = somar_meses(inicio, 1)
    agregado = defaultdict(lambda: [0.0, 0, 0])
    conn = pymysql.connect(**_mysql_config())
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT pdv, pilar, SUM(`valor_líquido_de_venda_do_produto`) FROM vendas_produto '
                'WHERE data_da_venda >= %s AND data_da_venda < %s GROUP BY pdv, pilar', (inicio, fim))
            for pdv, pilar, total in cur.fetchall():
                chave = pilar_do_texto(pilar)
                if chave in PILARES_PRODUTO and pdv:
                    agregado[(str(pdv).strip().upper(), inicio.strftime('%Y-%m'), chave)][0] += float(total or 0)
            cur.execute(
                "SELECT PDV, pilar, `Serviço_Técnico`, SUM(COALESCE(Receita, 0)), COUNT(*) FROM vendas_servicos "
                "WHERE data_da_venda >= %s AND data_da_venda < %s AND Venda_ativa = '1' "
                "AND `Status_do_Serviço` = 'Confirmado' GROUP BY PDV, pilar, `Serviço_Técnico`", (inicio, fim))
            for pdv, pilar, tecnico, total, qtd in cur.fetchall():
                chave = pilar_do_texto(pilar)
                if chave not in PILARES_SERVICO or not pdv:
                    continue
                soma = agregado[(str(pdv).strip().upper(), inicio.strftime('%Y-%m'), chave)]
                soma[0] += float(total or 0)
                if chave == 'fixa':
                    t = cab(tecnico)
                    if t in TECNICO_TV:
                        soma[1] += int(qtd or 0)
                    elif t in TECNICO_FIXA:
                        soma[2] += int(qtd or 0)
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return agregado


def previa_banco(meses, user, substituir=True):
    realizados, falhas = [], []
    for mes in sorted(set(meses)):
        try:
            realizados += _realizados_da_soma(buscar_mes_no_banco(mes), 'banco')
        except Exception as exc:  # noqa: BLE001 — banco fora do ar: diz qual mês falhou
            falhas.append('{}: {}'.format(servicos.rotulo_mes(mes), str(exc)[:160]))
    if not realizados:
        raise ValueError('O banco de vendas não respondeu. ' + ' · '.join(falhas))
    dados = _montar_previa_realizado(realizados)
    dados['falhas'] = falhas
    return ImportacaoMetas.objects.create(
        tipo=ImportacaoMetas.BANCO, arquivo_nome='Banco de vendas (Vivo GO)', dados=dados, substituir=substituir,
        criado_por=user,
        resumo='{} mês(es): {}'.format(len(dados['meses']), ', '.join(dados['meses']))[:400])


# ---------------------------------------------------------------------------
# Aplicar realizado (bases e banco)
# ---------------------------------------------------------------------------
def _gravar_realizado(loja, mes, pilar, valor, tv, fx, total, fonte, substituir):
    existente = Realizado.objects.filter(loja=loja, mes=mes, pilar=pilar).first()
    if existente and not substituir and (existente.valor is not None or existente.quantidade is not None):
        return False
    campos = {'valor': None if valor is None else Decimal(str(round(valor, 2))), 'fonte': fonte}
    if pilar == 'fixa':
        if tv is not None or fx is not None:
            campos.update(qtd_tv=tv, qtd_fixa=fx, qtd_total=None)
        elif total is not None:
            campos.update(qtd_total=total)
            if existente and (existente.qtd_tv is not None or existente.qtd_fixa is not None):
                campos.pop('qtd_total')
        if valor is None and existente:
            campos['valor'] = existente.valor
    Realizado.objects.update_or_create(loja=loja, mes=mes, pilar=pilar, defaults=campos)
    return True


def _aplicar_realizados(imp, fonte):
    mapa = servicos.MapaLojas()
    gravados = 0
    for pdv, mes, pilar, valor, tv, fx, total in imp.dados.get('realizados', []):
        loja = mapa.achar(pdv)
        if loja is None:
            continue
        if _gravar_realizado(loja, servicos.mes_do_codigo(mes), pilar, valor, tv, fx, total, fonte, imp.substituir):
            gravados += 1
    return gravados


# ---------------------------------------------------------------------------
# 3. Arquivo de metas da Vivo
# ---------------------------------------------------------------------------
PILAR_NO_CABECALHO = [('MOVEL', 'movel'), ('FIXA', 'fixa'), ('SVA', 'sva'), ('SEGURO', 'seguros'),
                      ('SMARTPHONE', 'smartphone'), ('ELETRONICO', 'eletronicos'), ('ESSENCIA', 'essenciais')]


def ler_meta_vivo(arquivo):
    """Acha a aba com uma coluna de loja (PDV/LOJA/FILIAL) e uma coluna por pilar, ou o formato longo
    (PDV, PILAR, META). Abas com coluna de consultor são de meta individual e ficam de fora."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(arquivo, data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError('Não consegui abrir o arquivo como planilha Excel (.xlsx): {}'.format(exc))
    abas = sorted(wb.worksheets, key=lambda ws: (0 if 'PDV' in cab(ws.title) else 1))
    for ws in abas:
        linhas = [list(l) for l in ws.iter_rows(values_only=True)]
        for i, linha in enumerate(linhas[:15]):
            cabs = [cab(c) for c in linha]
            if any('CONSULTOR' in c or 'VENDEDOR' in c for c in cabs):
                break
            col_loja = _achar_coluna(cabs, exatos=('PDV', 'PDV REDE', 'LOJA', 'FILIAL'),
                                     contem=('PDV', 'LOJA', 'FILIAL'), evitar=('ID', 'UF', 'CN'))
            if col_loja is None:
                continue
            col_pilar = {}
            for j, c in enumerate(cabs):
                if j == col_loja or '%' in c or 'REAL' == c:
                    continue
                for termo, pilar in PILAR_NO_CABECALHO:
                    if termo in c and pilar not in col_pilar:
                        col_pilar[pilar] = j
                        break
            metas = []
            if len(col_pilar) >= 2:
                for linha_dado in linhas[i + 1:]:
                    loja = ' '.join(str(linha_dado[col_loja] or '').split()) if col_loja < len(linha_dado) else ''
                    if not loja:
                        continue
                    for pilar, j in col_pilar.items():
                        valor = _num(linha_dado[j]) if j < len(linha_dado) else None
                        if valor is not None:
                            metas.append([loja, pilar, valor])
            elif 'PILAR' in cabs:
                col_p = cabs.index('PILAR')
                col_v = _achar_coluna(cabs, exatos=('META', 'META VIVO', 'VALOR'), contem=('META',))
                if col_v is None:
                    continue
                for linha_dado in linhas[i + 1:]:
                    if max(col_loja, col_p, col_v) >= len(linha_dado):
                        continue
                    loja = ' '.join(str(linha_dado[col_loja] or '').split())
                    pilar = pilar_do_texto(linha_dado[col_p])
                    valor = _num(linha_dado[col_v])
                    if loja and pilar and valor is not None:
                        metas.append([loja, pilar, valor])
            if metas:
                wb.close()
                return metas, ws.title
    wb.close()
    raise ValueError('Não achei uma aba com a coluna da loja (PDV) e as colunas dos pilares '
                     '(Móvel, Fixa, SVA, Seguro, Smartphone, Eletrônicos, Essenciais).')


def previa_meta_vivo(arquivo, competencia, user):
    metas, aba = ler_meta_vivo(arquivo)
    mapa = servicos.MapaLojas()
    nao = sorted({m[0] for m in metas if mapa.achar(m[0]) is None and normalizar(m[0]) not in NAO_SAO_LOJAS})
    return ImportacaoMetas.objects.create(
        tipo=ImportacaoMetas.META_VIVO, competencia=competencia, arquivo_nome=arquivo.name[:255],
        dados={'metas_vivo': metas, 'aba': aba, 'nao_reconhecidas': nao,
               'pilares': sorted({m[1] for m in metas})},
        criado_por=user, resumo='Aba "{}" · {} valor(es)'.format(aba, len(metas))[:400])


# ---------------------------------------------------------------------------
# 4. Planilha antiga ("METAS 11.2026 Novembro")
# ---------------------------------------------------------------------------
def _pilar_da_aba(titulo):
    t = cab(titulo)
    for termo, pilar in PILAR_NO_CABECALHO:
        if termo in t:
            return pilar
    return None


def _mes_de(valor):
    if isinstance(valor, (datetime, date)):
        return date(valor.year, valor.month, 1)
    return None


def ler_planilha_antiga(arquivo):
    """Lê a planilha no layout de novembro/2026. Devolve lojas, realizado, metas e a competência."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(arquivo, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError('Não consegui abrir o arquivo como planilha Excel (.xlsx): {}'.format(exc))
    votos_vivo = defaultdict(Counter)
    ordem = {}
    realizados = {}
    metas = {}
    anteriores = {}
    ano_anterior = {}
    ultimo_mes = None
    pilares_lidos = []
    for ws in wb.worksheets:
        pilar = _pilar_da_aba(ws.title)
        if not pilar:
            continue
        fixa = pilar == 'fixa'
        linha_cab = None
        for r in range(1, 8):
            if cab(ws.cell(r, 2).value) == 'PDV REDE':
                linha_cab = r
                break
        if linha_cab is None:
            continue
        pilares_lidos.append(pilar)
        colunas = {}
        meses_valor, meses_qtd = {}, {}
        for c in range(3, ws.max_column + 1):
            titulo = cab(ws.cell(linha_cab, c).value)
            mes = _mes_de(ws.cell(linha_cab, c).value)
            if mes is None and not titulo:
                # Célula mesclada com a de cima (na FIXA, o último mês do R$ fica na linha 2).
                mes = _mes_de(ws.cell(linha_cab - 1, c).value)
            if mes:
                meses_valor[c] = mes
            elif titulo in ('META VIVO', 'META MES ANTERIOR', 'REAL ANO ANTERIOR', 'QTD CNS', 'DECISAO',
                            'NOVA META') and titulo not in colunas:
                colunas[titulo] = c
            elif fixa and titulo in ('QTD TV', 'QTD FIXA'):
                # O mês fica na linha de cima, mesclado sobre o par TV/FIXA.
                mes_par = _mes_de(ws.cell(linha_cab - 1, c).value) or _mes_de(ws.cell(linha_cab - 1, c - 1).value)
                if mes_par:
                    meses_qtd[c] = (mes_par, 'tv' if titulo == 'QTD TV' else 'fixa')
        for mes in list(meses_valor.values()) + [m for m, _ in meses_qtd.values()]:
            ultimo_mes = mes if (ultimo_mes is None or mes > ultimo_mes) else ultimo_mes
        for r in range(linha_cab + 1, ws.max_row + 1):
            nome = ' '.join(str(ws.cell(r, 2).value or '').split())
            vivo = ' '.join(str(ws.cell(r, 1).value or '').split())
            if not nome or normalizar(nome) in NAO_SAO_LOJAS or normalizar(vivo) == 'REDE':
                continue
            chave = nome.upper()
            ordem.setdefault(chave, r)
            if vivo:
                votos_vivo[chave][vivo] += 1
            for c, mes in meses_valor.items():
                valor = ws.cell(r, c).value
                if isinstance(valor, (int, float)):
                    realizados.setdefault((chave, mes, pilar), [None, None, None, None])[0] = float(valor)
            for c, (mes, tipo) in meses_qtd.items():
                valor = ws.cell(r, c).value
                if isinstance(valor, (int, float)):
                    item = realizados.setdefault((chave, mes, pilar), [None, None, None, None])
                    item[1 if tipo == 'tv' else 2] = int(round(valor))

            def valor_de(titulo):
                c = colunas.get(titulo)
                v = ws.cell(r, c).value if c else None
                return float(v) if isinstance(v, (int, float)) else None

            meta = ws.cell(r, 3).value
            meta = float(meta) if isinstance(meta, (int, float)) and meta else None
            item = {'meta': meta, 'meta_vivo': valor_de('META VIVO') or None}
            if fixa:
                cns = valor_de('QTD CNS')
                item['qtd_cns'] = int(cns) if cns else None
                decisao = cab(ws.cell(r, colunas['DECISAO']).value) if 'DECISAO' in colunas else ''
                item['decisao'] = 'BAIXO' if decisao == 'BAIXO' else 'CIMA'
            metas[(chave, pilar)] = item
            anterior = valor_de('META MES ANTERIOR')
            if anterior:
                anteriores[(chave, pilar)] = anterior
            real_ano = valor_de('REAL ANO ANTERIOR')
            if real_ano is not None:
                ano_anterior[(chave, pilar)] = real_ano
    wb.close()
    if not pilares_lidos or ultimo_mes is None:
        raise ValueError('Não reconheci o layout: espero abas por pilar (MÓVEL, FIXA, SVA…) com "PDV REDE" '
                         'na coluna B e os meses no cabeçalho.')
    competencia = somar_meses(ultimo_mes, 1)
    mes_ano = somar_meses(competencia, -12)
    # "REAL ANO ANTERIOR" só entra onde o histórico não tem o mês (na Fixa é quantidade).
    for (chave, pilar), valor in ano_anterior.items():
        item = realizados.setdefault((chave, mes_ano, pilar), [None, None, None, None])
        if pilar == 'fixa':
            if item[1] is None and item[2] is None:
                item[3] = int(round(valor))
        elif item[0] is None:
            item[0] = valor
    # A coluna A escorrega em algumas abas (o PDV Vivo de uma loja na linha de outra): cada nome Vivo fica com
    # a loja em que ele mais aparece.
    dono = {}
    for chave, votos in votos_vivo.items():
        for vivo, n in votos.items():
            if vivo not in dono or n > votos_vivo[dono[vivo]][vivo]:
                dono[vivo] = chave
    lojas = []
    for chave in sorted(ordem, key=lambda k: ordem[k]):
        proprios = [(n, v) for v, n in votos_vivo[chave].items() if dono.get(v) == chave]
        lojas.append({'nome': chave, 'pdv_vivo': max(proprios)[1] if proprios else '', 'ordem': ordem[chave]})
    return {
        'competencia': competencia.strftime('%Y-%m'),
        'lojas': lojas,
        'pilares': pilares_lidos,
        'realizados': [[k[0], k[1].strftime('%Y-%m'), k[2], v[0], v[1], v[2], v[3]]
                       for k, v in sorted(realizados.items(), key=lambda kv: (kv[0][1], kv[0][2], kv[0][0]))],
        'metas': [[k[0], k[1], v.get('meta'), v.get('meta_vivo'), v.get('qtd_cns'), v.get('decisao')]
                  for k, v in metas.items()],
        'anteriores': [[k[0], k[1], v] for k, v in anteriores.items()],
    }


def previa_planilha(arquivo, user, competencia_codigo=None, substituir=False):
    dados = ler_planilha_antiga(arquivo)
    if competencia_codigo:
        dados['competencia'] = competencia_codigo
    mapa = servicos.MapaLojas()
    dados['novas_lojas'] = [l['nome'] for l in dados['lojas'] if mapa.achar(l['nome']) is None]
    dados['meses'] = sorted({r[1] for r in dados['realizados']})
    return ImportacaoMetas.objects.create(
        tipo=ImportacaoMetas.PLANILHA, arquivo_nome=getattr(arquivo, 'name', 'planilha.xlsx')[:255], dados=dados,
        substituir=substituir, criado_por=user,
        resumo='Competência {} · {} loja(s) · {} pilar(es) · {} a {}'.format(
            dados['competencia'], len(dados['lojas']), len(dados['pilares']),
            dados['meses'][0] if dados['meses'] else '—', dados['meses'][-1] if dados['meses'] else '—')[:400])


# ---------------------------------------------------------------------------
# Aplicar
# ---------------------------------------------------------------------------
def _set_meta(competencia, loja, pilar, campos, user, origem, substituir):
    m, _ = MetaLoja.objects.get_or_create(competencia=competencia, loja=loja, pilar=pilar)
    mudou = []
    for campo, valor in campos.items():
        if valor is None:
            continue
        atual = getattr(m, campo)
        if atual not in (None, '') and not substituir and campo != 'decisao':
            continue
        if campo in ('meta', 'meta_vivo', 'meta_anterior_manual'):
            valor = Decimal(str(round(valor, 4 if campo == 'meta_vivo' else 2)))
        if atual != valor:
            setattr(m, campo, valor)
            mudou.append((campo, atual, valor))
    if mudou:
        m.atualizado_por = user
        m.save()
        for campo, antes, depois in mudou:
            servicos.registrar(competencia, loja, pilar, campo, antes, depois, user, origem)
    return m


@transaction.atomic
def aplicar(imp, user):
    """Grava a prévia. Devolve um texto curto com o que foi feito."""
    if imp.status != ImportacaoMetas.PREVIA:
        raise ValueError('Essa importação já foi {}.'.format(imp.get_status_display().lower()))
    dados = imp.dados or {}
    if imp.tipo in (ImportacaoMetas.BASE, ImportacaoMetas.BANCO):
        fonte = Realizado.BASE if imp.tipo == ImportacaoMetas.BASE else Realizado.BANCO
        gravados = _aplicar_realizados(imp, fonte)
        texto = '{} valor(es) de realizado gravado(s).'.format(gravados)
    elif imp.tipo == ImportacaoMetas.META_VIVO:
        if not imp.competencia:
            raise ValueError('Escolha a competência.')
        if imp.competencia.fechada:
            raise ValueError('A competência está fechada.')
        mapa = servicos.MapaLojas()
        gravados = 0
        origem = 'Importação meta Vivo'
        for nome, pilar, valor in dados.get('metas_vivo', []):
            loja = mapa.achar(nome)
            if loja is None:
                continue
            _set_meta(imp.competencia, loja, pilar, {'meta_vivo': valor}, user, origem, True)
            gravados += 1
        texto = '{} meta(s) Vivo gravada(s) em {}.'.format(gravados, servicos.rotulo_mes(imp.competencia.mes, True))
    elif imp.tipo == ImportacaoMetas.PLANILHA:
        texto = _aplicar_planilha(imp, user)
    else:
        raise ValueError('Tipo desconhecido.')
    imp.status = ImportacaoMetas.APLICADA
    imp.aplicado_em = timezone.now()
    imp.save(update_fields=['status', 'aplicado_em'])
    for comp in Competencia.objects.filter(status=Competencia.ABERTA):
        servicos.recalcular_fixa(comp, user, 'Recalculado após importação')
    return texto


def _aplicar_planilha(imp, user):
    dados = imp.dados
    mapa = servicos.MapaLojas()
    vistas = set()
    for loja_dados in dados['lojas']:
        loja = mapa.achar(loja_dados['nome'])
        if loja is None:
            loja = mapa.criar(loja_dados['nome'], loja_dados.get('pdv_vivo'), loja_dados['ordem'] * 10)
        mudou = []
        if loja.ordem != loja_dados['ordem'] * 10:
            loja.ordem = loja_dados['ordem'] * 10
            mudou.append('ordem')
        if not loja.pdv_vivo and loja_dados.get('pdv_vivo'):
            loja.pdv_vivo = loja_dados['pdv_vivo']
            mudou.append('pdv_vivo')
        if not loja.ativa:
            loja.ativa = True
            mudou.append('ativa')
        if mudou:
            loja.save(update_fields=mudou)
        vistas.add(loja.id)
    gravados = 0
    for nome, mes, pilar, valor, tv, fx, total in dados['realizados']:
        loja = mapa.achar(nome)
        if loja and _gravar_realizado(loja, servicos.mes_do_codigo(mes), pilar, valor, tv, fx, total,
                                      Realizado.PLANILHA, imp.substituir):
            gravados += 1
    # Lojas do portal que não estão na planilha e nunca tiveram venda: ficam fora da tela.
    LojaMeta.objects.exclude(id__in=vistas).filter(realizados__isnull=True).update(ativa=False)

    mes = servicos.mes_do_codigo(dados['competencia'])
    competencia, _ = servicos.abrir_competencia(mes, user)
    origem = 'Importação planilha antiga'
    anteriores = {(n, p): v for n, p, v in dados.get('anteriores', [])}
    # O mês anterior entra como competência fechada com a meta que foi dada — vira "meta mês anterior".
    comp_ant = Competencia.objects.filter(mes=somar_meses(mes, -1)).first()
    if comp_ant is None and anteriores:
        comp_ant = Competencia.objects.create(mes=somar_meses(mes, -1), status=Competencia.FECHADA,
                                              criada_por=user, fechada_por=user, fechada_em=timezone.now(),
                                              observacao='Trazida da planilha antiga ({}).'.format(imp.arquivo_nome)[:300])
    for (nome, pilar), valor in anteriores.items():
        loja = mapa.achar(nome)
        if loja is None:
            continue
        if comp_ant is not None:
            campo = 'nova_meta' if pilar == 'fixa' else 'meta'
            valor_ant = int(round(valor)) if pilar == 'fixa' else valor
            m, _ = MetaLoja.objects.get_or_create(competencia=comp_ant, loja=loja, pilar=pilar)
            if getattr(m, campo) is None:
                setattr(m, campo, valor_ant if pilar == 'fixa' else Decimal(str(round(valor, 2))))
                m.save()
        if not competencia.fechada:
            _set_meta(competencia, loja, pilar, {'meta_anterior_manual': valor}, user, origem, imp.substituir)
    if not competencia.fechada:
        for nome, pilar, meta, meta_vivo, qtd_cns, decisao in dados['metas']:
            loja = mapa.achar(nome)
            if loja is None:
                continue
            campos = {'meta': meta, 'meta_vivo': meta_vivo}
            if pilar == 'fixa':
                campos.update(qtd_cns=qtd_cns, decisao=decisao)
            _set_meta(competencia, loja, pilar, campos, user, origem, imp.substituir)
    imp.competencia = competencia
    imp.save(update_fields=['competencia'])
    return '{} loja(s), {} valor(es) de realizado e as metas de {} gravados.'.format(
        len(vistas), gravados, servicos.rotulo_mes(mes, True))


def comparar_realizado(imp):
    """Para a tela de conferência: por mês, a grade loja × pilar com o novo valor e o que já está gravado."""
    mapa = servicos.MapaLojas()
    meses = {}
    atuais = {}
    for r in Realizado.objects.filter(mes__in=[servicos.mes_do_codigo(m) for m in imp.dados.get('meses', [])]):
        atuais[(r.loja_id, r.mes.strftime('%Y-%m'), r.pilar)] = r
    for pdv, mes, pilar, valor, tv, fx, total in imp.dados.get('realizados', []):
        loja = mapa.achar(pdv)
        grade = meses.setdefault(mes, {})
        nome = loja.nome if loja else pdv
        linha = grade.setdefault(nome, {'loja': loja, 'pilares': {}})
        atual = atuais.get((loja.id, mes, pilar)) if loja else None
        qtd = None
        if tv is not None or fx is not None:
            qtd = (tv or 0) + (fx or 0)
        elif total is not None:
            qtd = total
        linha['pilares'][pilar] = {
            'valor': valor, 'qtd': qtd, 'tv': tv, 'fixa': fx,
            'atual': float(atual.valor) if (atual and atual.valor is not None) else None,
            'atual_qtd': atual.quantidade if atual else None,
        }
    saida = []
    for mes in sorted(meses):
        linhas = sorted(meses[mes].items(), key=lambda kv: (kv[1]['loja'] is None,
                                                             kv[1]['loja'].ordem if kv[1]['loja'] else 0, kv[0]))
        saida.append({'mes': servicos.mes_do_codigo(mes), 'linhas': linhas})
    return saida


ABA = ABA_DO_PILAR
