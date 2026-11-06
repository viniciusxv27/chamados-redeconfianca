"""Importação da tabela de produtos (planilha oficial de preços da Vivo) para ItemPreco.

A importação **substitui** a tabela: só ficam os produtos destas três abas —

- ``SMARTPHONES`` e ``ELETRÔNICOS_LP_Conectados``: a **TABELA REGULAR**. Ela vem
  em blocos, um por grupo de plano (PRÉ, CONTROLE BTL, PÓS INDIVIDUAL… /
  MULTIVIVO, VIM 8GB…), e cada bloco tem o preço base, o "PIX e Vivo Pay" e as
  parcelas de 2x a 21x. Tudo isso vai para ``extra['tabela']`` — são as opções
  que o vendedor escolhe ao pôr o produto na venda (vendas/slv.py). "-" na
  célula = o produto não sai naquele grupo.
- ``ELETRÔNICOS_LP_Não Conectados``: o valor é o **PVP Base**.

O SKU vem da aba ``PRODUTOS`` (coluna ``Cod_Sap``), ligada pelo **Nome Comercial**.
Um produto costuma ter vários SKUs, um por cor: todos vão para ``extra['skus']`` e a
busca da venda acha o produto por qualquer um deles. Sem essa aba a importação segue
e quem já tinha SKU continua com ele.

O resto (planos, B2B, vitrine…) sai da tabela: planos, seguros e SVAs são
cadastrados na Parametrização. Produto que saiu da planilha é apagado; se já
foi usado numa venda, fica inativo (a venda continua apontando para ele).

[Confiabilidade.NF003] Tudo numa transação: se algo falha, a tabela vigente
fica exatamente como estava.
"""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import ItemPreco

ABAS_COM_TABELA = ('SMARTPHONES', 'ELETRÔNICOS_LP_Conectados')
ABA_PVP = 'ELETRÔNICOS_LP_Não Conectados'
SHEETS_ALVO = ABAS_COM_TABELA + (ABA_PVP,)
COND_BASE = 'Preço'                    # a coluna do grupo: preço base (cartão sem juros)
COND_PIX = 'PIX e Vivo Pay'
ABA_SKUS = 'PRODUTOS'
# Produtos cadastrados à mão que a importação não apaga nem inativa (comparados sem
# acento nem maiúscula). "Sim Card 5g" sumia a cada tabela nova — 06/11/2026.
MANTER_NA_IMPORTACAO = ('sim card 5g',)
COLUNAS_SKU = ('cod sap', 'codigo sap', 'cod material', 'sku')   # em ordem de preferência
CHAVES_SKU = ('skus', 'ID DPGC')       # o que vem da aba PRODUTOS dentro de extra (não é mudança de preço)


def _texto(valor):
    return ' '.join(str(valor if valor is not None else '').split())


def _valor(v):
    """Número da célula → str com 2 casas; '-', vazio, '#N/A' → None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        try:
            d = Decimal(str(v)).quantize(Decimal('0.01'))
        except InvalidOperation:
            return None
        return str(d) if d > 0 else None
    texto = str(v).replace('R$', '').replace(' ', '').strip()
    if not texto or texto in ('-', '--') or texto.startswith('#'):
        return None
    if ',' in texto:
        texto = texto.replace('.', '').replace(',', '.')
    try:
        d = Decimal(texto).quantize(Decimal('0.01'))
    except InvalidOperation:
        return None
    return str(d) if d > 0 else None


def _linha_cabecalho(linhas, procurado='Nome Comercial', ate=10):
    for i, l in enumerate(linhas[:ate]):
        if any(_texto(c).lower() == procurado.lower() for c in l):
            return i
    raise ValueError(f'não achei a coluna "{procurado}"')


def _colunas_regular(linhas, i_cab):
    """Os blocos da TABELA REGULAR: [(grupo, {condição: índice da coluna})]."""
    marcas = linhas[i_cab - 1]
    inicio = next(j for j, v in enumerate(marcas) if _texto(v).upper() == 'TABELA REGULAR')
    fim = next((j for j, v in enumerate(marcas) if j > inicio and _texto(v)), len(marcas))
    cab = linhas[i_cab]
    blocos, atual = [], None
    for j in range(inicio, fim):
        rotulo = _texto(cab[j]) if j < len(cab) else ''
        if not rotulo:
            continue
        if re.fullmatch(r'\d+x', rotulo, re.I) or rotulo.upper().startswith('PIX'):
            if atual:
                atual[1][COND_PIX if rotulo.upper().startswith('PIX') else rotulo.lower()] = j
            continue
        atual = (rotulo, {COND_BASE: j})            # começo de um bloco: o nome do grupo
        blocos.append(atual)
    return blocos


def _rotulo(valor):
    """'Cod_Sap' → 'cod sap'; 'CÓD. SAP' → 'cod sap'."""
    texto = unicodedata.normalize('NFKD', _texto(valor))
    texto = ''.join(c for c in texto if not unicodedata.combining(c)).lower()
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', texto).split())


def _codigo(valor):
    """Célula de código → texto. O Excel guarda parte dos SKUs como número (22019876)."""
    if valor is None or isinstance(valor, bool):
        return ''
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = _texto(valor)
    return '' if texto in ('-', '--') or texto.startswith('#') else texto[:40]


def _ler_skus(wb):
    """Os SKUs da aba PRODUTOS: ({nome comercial em minúsculas: [{'sku', 'cor', 'dpgc'}]}, info).

    O mapa vem None quando não dá para ler (sem a aba ou sem a coluna): a importação
    dos preços não depende disso, e a tela mostra o motivo.
    """
    info = {'aba': ABA_SKUS, 'coluna': '', 'codigos': 0, 'com_sku': 0, 'sem_sku': 0, 'exemplos_sem_sku': [], 'problema': ''}
    if ABA_SKUS not in wb.sheetnames:
        info['problema'] = f'a planilha não tem a aba {ABA_SKUS}'
        return None, info
    linhas = [list(r) for r in wb[ABA_SKUS].iter_rows(values_only=True, max_col=30)]
    try:
        i_cab = _linha_cabecalho(linhas)
    except ValueError:
        info['problema'] = f'a aba {ABA_SKUS} não tem a coluna "Nome Comercial"'
        return None, info
    colunas = {}
    for j, rotulo in enumerate(linhas[i_cab]):
        colunas.setdefault(_rotulo(rotulo), j)
    achada = next((c for c in COLUNAS_SKU if c in colunas), None)
    if achada is None:
        info['problema'] = f'a aba {ABA_SKUS} não tem a coluna do SKU (Cod_Sap)'
        return None, info
    info['coluna'] = _texto(linhas[i_cab][colunas[achada]])
    c_sku, c_nome = colunas[achada], colunas['nome comercial']
    c_cor, c_dpgc = colunas.get('cor'), colunas.get('id dpgc')

    def celula(linha, j):
        return linha[j] if j is not None and j < len(linha) else None

    mapa, vistos = {}, set()
    for linha in linhas[i_cab + 1:]:
        nome, sku = _texto(celula(linha, c_nome)), _codigo(celula(linha, c_sku))
        if not nome or nome == '-' or not sku or (nome.casefold(), sku) in vistos:
            continue
        vistos.add((nome.casefold(), sku))
        variante = {'sku': sku, 'cor': _texto(celula(linha, c_cor))[:60]}
        dpgc = _codigo(celula(linha, c_dpgc))
        if dpgc:
            variante['dpgc'] = dpgc
        mapa.setdefault(nome.casefold(), []).append(variante)
    return mapa, info


def _sku_principal(extra):
    skus = extra.get('skus') or []
    return skus[0]['sku'][:40] if skus else ''


def _sem_sku(extra):
    return {k: v for k, v in extra.items() if k not in CHAVES_SKU}


def _ler_aba(ws, nome_aba, resumo, skus=None):
    linhas = [list(r) for r in ws.iter_rows(values_only=True)]
    i_cab = _linha_cabecalho(linhas)
    cab = [_texto(c) for c in linhas[i_cab]]
    col = {rotulo.upper(): j for j, rotulo in enumerate(cab) if rotulo}
    c_nome = col['NOME COMERCIAL']
    blocos = _colunas_regular(linhas, i_cab) if nome_aba in ABAS_COM_TABELA else None
    c_pvp = col.get('PVP BASE')
    produtos = {}
    for l in linhas[i_cab + 1:]:
        nome = _texto(l[c_nome] if c_nome < len(l) else '')
        if not nome or nome == '-':
            continue
        extra = {'PORTFÓLIO': _texto(l[col['PORTFÓLIO']]) if 'PORTFÓLIO' in col else '',
                 'CATEGORIA': _texto(l[col['CATEGORIA']]) if 'CATEGORIA' in col else '',
                 'MARCA': _texto(l[col['MARCA']]) if 'MARCA' in col else ''}
        oferta = col.get('OFERTA DE COMUNICAÇÃO')
        if oferta is not None:
            extra['OFERTA'] = _texto(l[oferta])
        if blocos is not None:
            tabela = {}
            for grupo, conds in blocos:
                valores = {c: _valor(l[j] if j < len(l) else None) for c, j in conds.items()}
                valores = {c: v for c, v in valores.items() if v}
                if valores.get(COND_BASE):          # "-" no preço do grupo: não sai nesse grupo
                    tabela[grupo] = valores
            if not tabela:
                resumo['rejeitados'].append({'aba': nome_aba, 'item': nome[:120], 'motivo': 'sem preço em nenhum grupo'})
                continue
            extra['tabela'] = tabela
            extra['grupos'] = list(tabela)
            valor = Decimal(next(iter(tabela.values()))[COND_BASE])
        else:
            bruto = l[c_pvp] if c_pvp is not None and c_pvp < len(l) else None
            pvp = _valor(bruto)
            if not pvp:
                resumo['rejeitados'].append({'aba': nome_aba, 'item': nome[:120],
                                             'motivo': f'PVP Base "{_texto(bruto)[:20] or "vazio"}" não é preço'})
                continue
            valor = Decimal(pvp)
        if nome[:200] in produtos:
            resumo['rejeitados'].append({'aba': nome_aba, 'item': nome[:120],
                                         'motivo': 'repetido na planilha (vale a primeira linha)'})
            continue
        variantes = (skus or {}).get(nome.casefold())
        if variantes:
            extra['skus'] = variantes
            dpgc = next((v['dpgc'] for v in variantes if v.get('dpgc')), '')
            if dpgc:
                extra['ID DPGC'] = dpgc
        produtos[nome[:200]] = (valor, extra)
    return produtos


def importar_tabela_precos(file_obj, sheets=None):
    """Substitui a tabela de produtos pelas três abas. Devolve o resumo da importação."""
    import openpyxl

    wb = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
    faltando = [a for a in SHEETS_ALVO if a not in wb.sheetnames]
    if faltando:
        raise ValueError(f'a planilha não tem a(s) aba(s): {", ".join(faltando)}')
    resumo = {'incluidos': 0, 'alterados': 0, 'sem_mudanca': 0, 'removidos': 0, 'inativados': 0,
              'rejeitados': [], 'por_categoria': {}, 'importados': 0, 'erros': []}
    skus, info_sku = _ler_skus(wb)
    lidos = {aba: _ler_aba(wb[aba], aba, resumo, skus) for aba in SHEETS_ALVO}
    if skus is not None:
        for produtos in lidos.values():
            for nome, (_valor_item, extra) in produtos.items():
                if extra.get('skus'):
                    info_sku['com_sku'] += 1
                    info_sku['codigos'] += len(extra['skus'])
                else:
                    info_sku['sem_sku'] += 1
                    if len(info_sku['exemplos_sem_sku']) < 10:
                        info_sku['exemplos_sem_sku'].append(nome[:80])
    resumo['sku'] = info_sku
    agora = timezone.now()

    with transaction.atomic():
        from .aviso_precos import calcular_variacao, precos_do_item
        from .models import VendaProduto, VendaServico
        # Retrato de antes, para o aviso do que subiu / baixou (vendas/aviso_precos.py).
        antes = {(i.categoria, i.nome): precos_do_item(i.valor, i.extra)
                 for i in ItemPreco.objects.filter(categoria__in=SHEETS_ALVO, ativo=True).only('categoria', 'nome', 'valor', 'extra')}
        manter = set()
        for aba, produtos in lidos.items():
            existentes = {i.nome: i for i in ItemPreco.objects.filter(categoria=aba)}
            novos, mudados = [], []
            for nome, (valor, extra) in produtos.items():
                item = existentes.get(nome)
                if item is None:
                    novos.append(ItemPreco(categoria=aba, nome=nome, valor=valor, extra=extra, ativo=True, importado_em=agora,
                                           cod_sap=_sku_principal(extra)))
                    continue
                manter.add(item.pk)
                antigo = item.extra if isinstance(item.extra, dict) else {}
                if skus is None:                # sem a aba PRODUTOS: não apaga o SKU que o produto já tem
                    extra.update({k: antigo[k] for k in CHAVES_SKU if k in antigo})
                principal = _sku_principal(extra) or item.cod_sap
                preco_mudou = item.valor != valor or _sem_sku(antigo) != _sem_sku(extra) or not item.ativo
                sku_mudou = {k: antigo.get(k) for k in CHAVES_SKU} != {k: extra.get(k) for k in CHAVES_SKU} \
                    or item.cod_sap != principal
                if preco_mudou or sku_mudou:
                    item.valor, item.extra, item.ativo, item.importado_em = valor, extra, True, agora
                    item.plano = item.sistema = item.grupamento = item.cod_sistema = ''
                    item.cod_sap = principal
                    # bulk_update não preenche o auto_now, e é por essa data que a busca sabe que precisa se refazer.
                    item.updated_at = agora
                    mudados.append(item)
                if preco_mudou:
                    resumo['alterados'] += 1    # só SKU novo não é "preço alterado"
                else:
                    resumo['sem_mudanca'] += 1
            ItemPreco.objects.bulk_update(mudados, ['valor', 'extra', 'ativo', 'importado_em', 'plano', 'sistema',
                                                    'grupamento', 'cod_sap', 'cod_sistema', 'updated_at'], batch_size=500)
            criados = ItemPreco.objects.bulk_create(novos, batch_size=500)
            manter.update(i.pk for i in criados)
            resumo['incluidos'] += len(novos)
            resumo['por_categoria'][aba] = len(produtos)
            resumo['importados'] += len(produtos)
        # O que não veio na planilha sai da tabela (inclusive planos e as outras abas),
        # menos os produtos fixos (MANTER_NA_IMPORTACAO).
        fixos = [i for i in ItemPreco.objects.exclude(pk__in=manter).only('id', 'nome', 'ativo')
                 if _rotulo(i.nome) in MANTER_NA_IMPORTACAO]
        manter.update(i.pk for i in fixos)
        resumo['mantidos'] = sorted({i.nome for i in fixos if i.ativo})
        fora = ItemPreco.objects.exclude(pk__in=manter)
        usados = set(VendaProduto.objects.filter(preco__in=fora).values_list('preco_id', flat=True)) | \
            set(VendaServico.objects.filter(preco__in=fora).values_list('preco_id', flat=True))
        resumo['inativados'] = fora.filter(pk__in=usados, ativo=True).update(ativo=False)
        resumo['removidos'] = fora.exclude(pk__in=usados).delete()[0]
    depois = {(aba, nome): precos_do_item(valor, extra)
              for aba, produtos in lidos.items() for nome, (valor, extra) in produtos.items()}
    resumo['variacao'] = calcular_variacao(antes, depois)
    return resumo
