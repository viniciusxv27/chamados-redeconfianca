"""Importação da tabela de produtos (planilha oficial de preços da Vivo) para ItemPreco.

A importação **substitui** a tabela: só ficam os produtos destas três abas —

- ``SMARTPHONES`` e ``ELETRÔNICOS_LP_Conectados``: a **TABELA REGULAR**. Ela vem
  em blocos, um por grupo de plano (PRÉ, CONTROLE BTL, PÓS INDIVIDUAL… /
  MULTIVIVO, VIM 8GB…), e cada bloco tem o preço base, o "PIX e Vivo Pay" e as
  parcelas de 2x a 21x. Tudo isso vai para ``extra['tabela']`` — são as opções
  que o vendedor escolhe ao pôr o produto na venda (vendas/slv.py). "-" na
  célula = o produto não sai naquele grupo.
- ``ELETRÔNICOS_LP_Não Conectados``: o valor é o **PVP Base**.

O resto (planos, B2B, vitrine…) sai da tabela: planos, seguros e SVAs são
cadastrados na Parametrização. Produto que saiu da planilha é apagado; se já
foi usado numa venda, fica inativo (a venda continua apontando para ele).

[Confiabilidade.NF003] Tudo numa transação: se algo falha, a tabela vigente
fica exatamente como estava.
"""
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import ItemPreco

ABAS_COM_TABELA = ('SMARTPHONES', 'ELETRÔNICOS_LP_Conectados')
ABA_PVP = 'ELETRÔNICOS_LP_Não Conectados'
SHEETS_ALVO = ABAS_COM_TABELA + (ABA_PVP,)
COND_BASE = 'Preço'                    # a coluna do grupo: preço base (cartão sem juros)
COND_PIX = 'PIX e Vivo Pay'


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


def _ler_aba(ws, nome_aba, resumo):
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
    lidos = {aba: _ler_aba(wb[aba], aba, resumo) for aba in SHEETS_ALVO}
    agora = timezone.now()

    with transaction.atomic():
        from .models import VendaProduto, VendaServico
        manter = set()
        for aba, produtos in lidos.items():
            existentes = {i.nome: i for i in ItemPreco.objects.filter(categoria=aba)}
            novos, mudados = [], []
            for nome, (valor, extra) in produtos.items():
                item = existentes.get(nome)
                if item is None:
                    novos.append(ItemPreco(categoria=aba, nome=nome, valor=valor, extra=extra, ativo=True, importado_em=agora))
                elif item.valor != valor or item.extra != extra or not item.ativo:
                    item.valor, item.extra, item.ativo, item.importado_em = valor, extra, True, agora
                    item.plano = item.sistema = item.grupamento = item.cod_sap = item.cod_sistema = ''
                    mudados.append(item)
                    manter.add(item.pk)
                else:
                    resumo['sem_mudanca'] += 1
                    manter.add(item.pk)
            ItemPreco.objects.bulk_update(mudados, ['valor', 'extra', 'ativo', 'importado_em', 'plano', 'sistema',
                                                    'grupamento', 'cod_sap', 'cod_sistema', 'updated_at'], batch_size=500)
            criados = ItemPreco.objects.bulk_create(novos, batch_size=500)
            manter.update(i.pk for i in criados)
            resumo['incluidos'] += len(novos)
            resumo['alterados'] += len(mudados)
            resumo['por_categoria'][aba] = len(produtos)
            resumo['importados'] += len(produtos)
        # O que não veio na planilha sai da tabela (inclusive planos e as outras abas).
        fora = ItemPreco.objects.exclude(pk__in=manter)
        usados = set(VendaProduto.objects.filter(preco__in=fora).values_list('preco_id', flat=True)) | \
            set(VendaServico.objects.filter(preco__in=fora).values_list('preco_id', flat=True))
        resumo['inativados'] = fora.filter(pk__in=usados, ativo=True).update(ativo=False)
        resumo['removidos'] = fora.exclude(pk__in=usados).delete()[0]
    return resumo
