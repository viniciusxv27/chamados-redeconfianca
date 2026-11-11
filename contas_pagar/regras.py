"""Regras do Contas a Pagar — a lógica pura, sem banco nenhum.

Portadas do sistema antigo (app.py do "Contas a Pagar · Rede Confiança"), com os
mesmos nomes e o mesmo resultado. As três tratativas são as três últimas colunas
da aba Base_VivoGo da planilha "Base_Contas_Pagar - Calculos.xlsx":

1. **Agging**: Despesa contém o texto 1 ("Compra") *e* Fornecedor contém o texto 2
   ("TELEFONICA BRASIL") → ``AGGING``; senão, a própria Despesa. Diferencia
   maiúsculas/minúsculas, como o PROCURAR do Excel.
2. **Banco pagador**: agência/conta do campo Débito × contas bancárias cadastradas
   (sem match → "Não Informado").
3. **TipoMacro**: Agging × tabela de categorias, sem diferenciar maiúsculas
   (sem match → "Não Classificado").

Saldo final = Aplicação + Conta garantida + Saldo − Pagamentos + Transferência.

Bancos são dicts com ``id, banco, agencia, conta, ordem, ativo`` (como as linhas
do SQLite antigo); assim o que vem do Postgres e o que vem do dados.db passam
pelo mesmo código.
"""
import csv
import io
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

NAO_INFORMADO = 'Não Informado'
NAO_CLASSIFICADO = 'Não Classificado'

AGGING_DESPESA_PADRAO = 'Compra'
AGGING_FORNECEDOR_PADRAO = 'TELEFONICA BRASIL'

COLUNAS_OBRIGATORIAS = ('Data de vencimento', 'Valor líquido')

CENTAVO = Decimal('0.01')
ZERO = Decimal('0')


def dinheiro(v):
    """Decimal com 2 casas (meio centavo arredonda para cima, como o DECIMAL do MySQL)."""
    if not isinstance(v, Decimal):
        v = Decimal(str(v or 0))
    return v.quantize(CENTAVO, rounding=ROUND_HALF_UP)


# ── Leitura da BASE VIVOGO ─────────────────────────────────────────────────
def parse_num(v):
    """Número no formato brasileiro ("1.234,56", "R$ 65") ou do Excel → Decimal."""
    if v is None:
        return ZERO
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip().replace('R$', '').replace(' ', '')
    if not s:
        return ZERO
    if ',' in s or re.fullmatch(r'-?\d{1,3}(\.\d{3})+', s):
        s = s.replace('.', '').replace(',', '.')
    try:
        return Decimal(s)
    except InvalidOperation:
        return ZERO


def parse_data(v):
    """``date`` ou ``None`` (aceita dd/mm/aaaa, ISO, com hora e dd/mm/aa)."""
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d/%m/%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S', '%d/%m/%y',
                '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _json_seguro(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


class ArquivoInvalido(ValueError):
    pass


def ler_arquivo(nome, conteudo):
    """Lê CSV (; ou ,) ou XLSX e devolve lista de dicts com os cabeçalhos originais."""
    nome_l = (nome or '').lower()
    if nome_l.endswith(('.xlsx', '.xlsm')):
        import openpyxl
        try:
            wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
        except Exception as exc:  # noqa: BLE001
            raise ArquivoInvalido('Não deu para abrir a planilha: o arquivo não é um .xlsx válido.') from exc
        ws = wb.worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        cab = [str(c).strip() if c is not None else '' for c in rows[0]]
        out = []
        for r in rows[1:]:
            if not any(x not in (None, '') for x in r):
                continue
            out.append({cab[i]: ('' if i >= len(r) or r[i] is None else _json_seguro(r[i]))
                        for i in range(len(cab)) if cab[i]})
        return out
    texto = None
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            texto = conteudo.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not texto:
        return []
    primeira = texto.split('\n', 1)[0]
    sep = ';' if primeira.count(';') >= primeira.count(',') else ','
    leitor = csv.DictReader(io.StringIO(texto), delimiter=sep)
    return [{(k or '').strip(): (v or '').strip() if isinstance(v, str) else '' for k, v in row.items()}
            for row in leitor]


def validar_linhas(linhas):
    if not linhas:
        raise ArquivoInvalido('Arquivo vazio ou em formato não reconhecido.')
    if any(c not in linhas[0] for c in COLUNAS_OBRIGATORIAS):
        raise ArquivoInvalido("O arquivo não parece ser o relatório 'rel_contas_a_pagar_e_ja_pagas' "
                              "(colunas 'Data de vencimento' e 'Valor líquido' não encontradas).")


def col(row, *nomes):
    for n in nomes:
        if n in row:
            return row[n]
    return ''


# ── Banco pagador ──────────────────────────────────────────────────────────
RE_CONTA = re.compile(r'AG:\s*(\d+)\s*CC:\s*([\d\.]+)', re.I)


def so_digitos(s):
    return re.sub(r'\D', '', str(s or '').split('-')[0]).lstrip('0')


def resolver_banco(debito, bancos):
    """Equivalente ao PROCV Débito → Dados!A136:B147 (Banco pagador)."""
    m = RE_CONTA.search(str(debito or ''))
    if not m:
        return None
    ag, cc = m.group(1).lstrip('0'), m.group(2).replace('.', '').lstrip('0')
    candidato = None
    for b in bancos:
        if b['conta'] and so_digitos(b['conta']) == cc:
            if b['agencia'] and b['agencia'].lstrip('0') == ag:
                return b
            if not b['agencia'] and candidato is None:
                candidato = b
    return candidato


def nome_banco(b):
    """O nome que vai para o MySQL e o painel: "BANCO - AG: 301 CC: 203409"."""
    if not b:
        return NAO_INFORMADO
    partes = [b['banco']]
    if b['agencia'] or b['conta']:
        partes.append('AG: %s CC: %s' % (b['agencia'] or '-', b['conta'] or '-'))
    return ' - '.join(partes)


def separar_banco(nome):
    """("SICOOB - REDE CONFIANÇA", "AG: 301 CC: 203409") — para mostrar em duas linhas."""
    nome = nome or ''
    i = nome.find(' - AG:')
    return (nome[:i], nome[i + 3:]) if i > 0 else (nome, '')


# ── As três tratativas ─────────────────────────────────────────────────────
def mapa_categorias(pares):
    """[(tipo, categoria)] → {TIPO EM MAIÚSCULAS: categoria} (CORRESP do Excel ignora maiúsculas)."""
    return {str(tipo).strip().upper(): categoria for tipo, categoria in pares}


def tratar(row, bancos, cats, cfg):
    """As 3 colunas calculadas da aba Base_VivoGo: (Agging, banco ou None, TipoMacro)."""
    despesa = str(col(row, 'Despesa'))
    fornecedor = str(col(row, 'Fornecedor'))
    k1 = cfg.get('agging_despesa_contem', AGGING_DESPESA_PADRAO)
    k2 = cfg.get('agging_fornecedor_contem', AGGING_FORNECEDOR_PADRAO)
    agging = 'AGGING' if (k1 and k1 in despesa and k2 and k2 in fornecedor) else despesa
    banco = resolver_banco(col(row, 'Débito', 'Debito'), bancos)
    tipo_macro = cats.get(agging.strip().upper(), NAO_CLASSIFICADO)
    if tipo_macro.strip().lower() == NAO_CLASSIFICADO.lower():
        tipo_macro = NAO_CLASSIFICADO
    return agging, banco, tipo_macro


def titulo_tratado(tid, vencimento, valor, excluido, row, bancos, cats, cfg):
    """Um título do rascunho com as colunas do relatório e as 3 tratativas aplicadas."""
    agging, banco, tipo_macro = tratar(row, bancos, cats, cfg)
    return {
        'id': tid,
        'vencimento': vencimento.isoformat() if vencimento else '',
        'valor': dinheiro(valor),
        'excluido': bool(excluido),
        'situacao': col(row, 'Situação'),
        'fornecedor': col(row, 'Fornecedor'),
        'filial': col(row, 'Filial'),
        'observacoes': col(row, 'Observações') or col(row, 'Obs. despesa'),
        'despesa': col(row, 'Despesa'),
        'plano': col(row, 'Plano de Contas'),
        'forma': col(row, 'Forma pagamento'),
        'documento': col(row, 'Nº do documento / Linha de pagamento'),
        'movimento': col(row, 'Nº do movimento'),
        'debito': col(row, 'Débito'),
        'cnpj': col(row, 'CNPJ/CPF Fornecedor'),
        'nota_fiscal': col(row, 'Nota fiscal'),
        'agging': agging,
        'banco_id': banco['id'] if banco else None,
        'banco': nome_banco(banco),
        'tipo_macro': tipo_macro,
    }


def e_a_pagar(titulo):
    return str(titulo.get('situacao') or 'A Pagar').lower().startswith('a pagar')


def selecionados(titulos, venc_ini, venc_fim, incluir_excluidos=False):
    """Os títulos que vão para o "pagar hoje": vencimento no intervalo, A Pagar e não desmarcados."""
    ini, fim = _iso(venc_ini), _iso(venc_fim)
    return [t for t in titulos
            if (incluir_excluidos or not t['excluido']) and t['vencimento']
            and ini <= t['vencimento'] <= fim and e_a_pagar(t)]


def _iso(v):
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v or '')


def saldo_final(aplicacao, garantida, saldo, pagamentos, transferencia):
    return dinheiro(aplicacao) + dinheiro(garantida) + dinheiro(saldo) - dinheiro(pagamentos) + dinheiro(transferencia)


# ── Publicação ─────────────────────────────────────────────────────────────
def _txt(v, limite=None):
    if v is None or v == '':
        return ''
    s = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
    return s[:limite] if limite else s


def linhas_para_publicar(data_pag, sel, bancos, saldos, agora):
    """(linhas de cp_titulos_programados, linhas de cp_saldos_bancos) — igual ao publicar() antigo.

    ``bancos``: todas as contas cadastradas, na ordem (ordem, id), ativas ou não.
    ``saldos``: {banco_id: {aplicacao, garantida, saldo, transferencia}}.
    """
    pag = {}
    for t in sel:
        pag[t['banco_id']] = pag.get(t['banco_id'], ZERO) + dinheiro(t['valor'])
    linhas_saldo = []
    for i, b in enumerate(bancos):
        if not b['ativo'] and b['id'] not in pag:
            continue
        s = saldos.get(b['id'], {})
        apl, gar, sld, trf = (dinheiro(s.get('aplicacao')), dinheiro(s.get('garantida')),
                              dinheiro(s.get('saldo')), dinheiro(s.get('transferencia')))
        p = pag.get(b['id'], ZERO)
        if not any((apl, gar, sld, trf, p)):
            continue
        tot = apl + gar + sld
        linhas_saldo.append((data_pag, nome_banco(b)[:255], i, apl, gar, sld, tot, p, trf, tot - p + trf, agora))
    if pag.get(None):
        p = pag[None]
        linhas_saldo.append((data_pag, NAO_INFORMADO, 9999, ZERO, ZERO, ZERO, ZERO, p, ZERO, -p, agora))
    linhas_titulos = [
        (data_pag, t['vencimento'] or None, _txt(t['fornecedor'], 255), _txt(t['cnpj'], 40), _txt(t['filial'], 120),
         _txt(t['observacoes']), _txt(t['plano'], 255), _txt(t['despesa'], 255), _txt(t['agging'], 255),
         _txt(t['tipo_macro'], 120), _txt(t['forma'], 80), _txt(t['movimento'], 40),
         _txt(t['documento']).lstrip("'")[:255], _txt(t['nota_fiscal'], 80), _txt(t['debito'], 120),
         _txt(t['banco'], 255), dinheiro(t['valor']), agora)
        for t in sel]
    return linhas_titulos, linhas_saldo


# ── Painel ─────────────────────────────────────────────────────────────────
def _f(v):
    return dinheiro(v or 0)


def _dia(v):
    if v is None or v == '':
        return ''
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:10]


def montar_painel(ini, fim, titulos, saldos, bancos_cad=()):
    """O dashboard() antigo: títulos do período e o acompanhamento por banco.

    ``titulos``/``saldos``: linhas lidas do MySQL (cp_titulos_programados /
    cp_saldos_bancos), já filtradas pelo período e ordenadas por data.
    """
    tits = []
    for t in titulos:
        tits.append({'data_pagamento': _dia(t['data_pagamento']), 'vencimento': _dia(t.get('vencimento')),
                     'fornecedor': t.get('fornecedor') or '', 'filial': t.get('filial') or '',
                     'observacoes': t.get('observacoes') or '', 'tipo_macro': t.get('tipo_macro') or '',
                     'banco': t.get('banco') or '', 'valor': _f(t.get('valor')), 'despesa': t.get('despesa') or ''})
    por_banco = {}
    for s in saldos:
        b = por_banco.get(s['banco'])
        if b is None:  # saldo de abertura = primeiro dia do período
            b = por_banco[s['banco']] = {'banco': s['banco'], 'ordem': s.get('ordem'), 'aplicacao': _f(s.get('aplicacao')),
                                         'garantida': _f(s.get('conta_garantida')), 'saldo': _f(s.get('saldo')),
                                         'pagamentos': ZERO, 'transferencia': ZERO}
        b['pagamentos'] += _f(s.get('pagamentos'))
        b['transferencia'] += _f(s.get('transferencia'))
        b['saldo_final'] = _f(s.get('saldo_final'))  # último dia
    # Todas as contas cadastradas e ativas aparecem, mesmo sem movimento no período
    ordem_cad = {}
    for i, b in enumerate(bancos_cad):
        nome = nome_banco(b)
        ordem_cad[nome] = i
        if b['ativo'] and nome not in por_banco:
            por_banco[nome] = {'banco': nome, 'ordem': i, 'aplicacao': ZERO, 'garantida': ZERO, 'saldo': ZERO,
                               'pagamentos': ZERO, 'transferencia': ZERO, 'saldo_final': ZERO}
    for nome, b in por_banco.items():
        b['ordem'] = 99999 if nome == NAO_INFORMADO else ordem_cad.get(nome, 5000 + (b['ordem'] or 0))
    linhas = sorted(por_banco.values(), key=lambda x: x['ordem'])
    for b in linhas:
        b['total'] = b['aplicacao'] + b['garantida'] + b['saldo']
        b['nome'], b['conta'] = separar_banco(b['banco'])
    dias = sorted({t['data_pagamento'] for t in tits} | {_dia(s['data_pagamento']) for s in saldos})
    return {'ini': _iso(ini), 'fim': _iso(fim), 'dias': dias, 'titulos': tits, 'bancos': linhas}


def filtrar_titulos(titulos, banco=None, categoria=None, filial=None, busca=''):
    """Filtros do painel (clicar num banco, categoria ou filial; caixa de busca). Somam-se (E)."""
    q = (busca or '').strip().lower()
    saida = []
    for t in titulos:
        if banco and t['banco'] != banco:
            continue
        if categoria and t['tipo_macro'] != categoria:
            continue
        if filial and t['filial'] != filial:
            continue
        if q and q not in ' '.join(str(t[k]) for k in ('fornecedor', 'filial', 'observacoes', 'despesa',
                                                         'banco', 'tipo_macro')).lower():
            continue
        saida.append(t)
    return saida


def agrupar(titulos, chave):
    """[(rótulo, quantidade, valor)] do maior para o menor valor."""
    soma, qtd = {}, {}
    for t in titulos:
        k = t[chave] or '—'
        soma[k] = soma.get(k, ZERO) + t['valor']
        qtd[k] = qtd.get(k, 0) + 1
    return sorted(((k, qtd[k], v) for k, v in soma.items()), key=lambda x: (-x[2], x[0]))


def totais_bancos(linhas):
    tot = {k: sum((b[k] for b in linhas), ZERO)
           for k in ('aplicacao', 'garantida', 'saldo', 'pagamentos', 'transferencia', 'saldo_final', 'total')}
    tot['em_conta'] = tot['saldo'] + tot['garantida']
    return tot


# ── Períodos do painel (Hoje / Semana / Quinzena / Mês e as setas) ─────────
MODOS = ('hoje', 'semana', 'quinzena', 'mes', 'custom')


def _ultimo_dia(ano, mes):
    prox = date(ano + (mes == 12), mes % 12 + 1, 1)
    return prox - timedelta(days=1)


def periodo(modo, ref, hoje=None):
    """(ini, fim) do modo, contendo a data ``ref``."""
    if modo == 'hoje':
        return ref, ref
    if modo == 'semana':  # segunda a domingo
        i = ref - timedelta(days=ref.weekday())
        return i, i + timedelta(days=6)
    if modo == 'quinzena':
        if ref.day <= 15:
            return date(ref.year, ref.month, 1), date(ref.year, ref.month, 15)
        return date(ref.year, ref.month, 16), _ultimo_dia(ref.year, ref.month)
    if modo == 'mes':
        return date(ref.year, ref.month, 1), _ultimo_dia(ref.year, ref.month)
    return ref, ref


def deslocar(modo, ini, fim, direcao, hoje):
    """As setas: (modo, ini, fim) do período anterior (-1) ou seguinte (+1)."""
    if modo in ('hoje', 'custom'):
        n = (fim - ini).days + 1
        ini, fim = ini + timedelta(days=direcao * n), fim + timedelta(days=direcao * n)
        modo = 'hoje' if (ini == fim == hoje) else 'custom'
        return modo, ini, fim
    if modo == 'semana':
        ini, fim = periodo('semana', ini + timedelta(days=7 * direcao))
    elif modo == 'quinzena':
        ini, fim = periodo('quinzena', fim + timedelta(days=1) if direcao > 0 else ini - timedelta(days=1))
    elif modo == 'mes':
        ini, fim = periodo('mes', fim + timedelta(days=1) if direcao > 0 else ini - timedelta(days=1))
    return modo, ini, fim


# ── Configurações: importar a aba "Dados" ──────────────────────────────────
def ler_aba_dados(conteudo):
    """[(tipo, categoria)] da aba "Dados" (colunas A:B, da linha 2 até a primeira vazia)."""
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise ArquivoInvalido('Não deu para abrir a planilha: envie um .xlsx.') from exc
    ws = wb['Dados'] if 'Dados' in wb.sheetnames else wb.worksheets[0]
    pares = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        if not r or r[0] in (None, ''):
            break
        if len(r) > 1 and r[1]:
            pares.append((str(r[0]).strip(), str(r[1]).strip()))
    return pares
