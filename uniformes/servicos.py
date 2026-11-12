"""Regras do estoque de uniformes.

Toda mudança de saldo passa por aqui: trava os itens (`select_for_update`, em
ordem de id para duas telas não se travarem uma à outra), relê o saldo já
travado, confere que nada fica negativo e grava o movimento e o saldo novo na
mesma transação. Tela nenhuma escreve `ItemUniforme.saldo` direto.
"""
from __future__ import annotations

import unicodedata
from collections import OrderedDict, defaultdict

from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, Sum, When
from django.utils import timezone

from .models import ItemUniforme, MovimentoUniforme

M = MovimentoUniforme


class ErroEstoque(ValueError):
    """Lançamento recusado (sem saldo, quantidade inválida, motivo faltando…)."""


# ---------------------------------------------------------------------------
# Normalização
# ---------------------------------------------------------------------------
def norm(texto) -> str:
    """Sem acento, minúsculo e com espaço simples — para comparar nomes."""
    texto = unicodedata.normalize('NFKD', str(texto or ''))
    return ' '.join(''.join(c for c in texto if not unicodedata.combining(c)).lower().split())


def limpar_tipo(tipo) -> str:
    tipo = ' '.join(str(tipo or '').split())[:40]
    return tipo[:1].upper() + tipo[1:] if tipo else ''


def limpar_tamanho(tamanho) -> str:
    tamanho = str(tamanho if tamanho is not None else '').strip()
    if tamanho.endswith('.0') and tamanho[:-2].isdigit():   # 40.0 da planilha vira 40
        tamanho = tamanho[:-2]
    return ' '.join(tamanho.upper().split())[:10]


def limpar_descricao(descricao) -> str:
    return ' '.join(str(descricao or '').split())[:120]


def chave_item(tipo, tamanho, descricao) -> tuple:
    return (norm(tipo), norm(limpar_tamanho(tamanho)), norm(descricao))


def quantidade_valida(valor) -> int:
    """Inteiro > 0 vindo de formulário/planilha ("2", "2,0", 2.0)."""
    try:
        numero = float(str(valor).strip().replace(',', '.'))
    except (TypeError, ValueError):
        raise ErroEstoque('Informe uma quantidade em número.')
    if numero != int(numero) or numero <= 0:
        raise ErroEstoque('A quantidade precisa ser um número inteiro maior que zero.')
    return int(numero)


# ---------------------------------------------------------------------------
# Itens
# ---------------------------------------------------------------------------
def achar_item(tipo, tamanho, descricao=''):
    """Item igual ignorando maiúsculas/acentos, ou None."""
    procurada = chave_item(tipo, tamanho, descricao)
    candidatos = ItemUniforme.objects.filter(tamanho__iexact=limpar_tamanho(tamanho))
    for item in candidatos:
        if chave_item(item.tipo, item.tamanho, item.descricao) == procurada:
            return item
    return None


def criar_item(*, tipo, tamanho='', descricao='', estoque_minimo=0, por=None):
    tipo, tamanho, descricao = limpar_tipo(tipo), limpar_tamanho(tamanho), limpar_descricao(descricao)
    if not tipo:
        raise ErroEstoque('Informe o tipo da peça (camisa, calça…).')
    existente = achar_item(tipo, tamanho, descricao)
    if existente:
        if not existente.ativo:
            existente.ativo = True
            existente.atualizado_por = por
            existente.save(update_fields=['ativo', 'atualizado_por', 'atualizado_em'])
        return existente, False
    item = ItemUniforme.objects.create(
        tipo=tipo, tamanho=tamanho, descricao=descricao,
        estoque_minimo=max(0, int(estoque_minimo or 0)), criado_por=por, atualizado_por=por)
    return item, True


# ---------------------------------------------------------------------------
# Movimentos
# ---------------------------------------------------------------------------
def _travar(ids):
    """Itens travados até o fim da transação, já com o saldo atual."""
    itens = ItemUniforme.objects.select_for_update().filter(id__in=sorted(set(ids))).order_by('id')
    travados = {item.id: item for item in itens}
    faltando = set(ids) - set(travados)
    if faltando:
        raise ErroEstoque('Item de uniforme não encontrado.')
    return travados


def _juntar(linhas):
    """[(item_id, qtd), …] → {item_id: qtd somada}, na ordem em que vieram."""
    juntas = OrderedDict()
    for item_id, qtd in linhas:
        qtd = quantidade_valida(qtd)
        juntas[int(item_id)] = juntas.get(int(item_id), 0) + qtd
    if not juntas:
        raise ErroEstoque('Escolha pelo menos uma peça.')
    return juntas


def _gravar(item, *, tipo, quantidade, delta, por, colaborador=None, data=None,
            observacao='', nota_fiscal='', lote=None, agora=None):
    novo = item.saldo + delta
    if novo < 0:
        raise ErroEstoque(f'Sem saldo de {item.nome}: há {item.saldo} em estoque e o lançamento tira {-delta}.')
    item.saldo = novo
    item.atualizado_por = por
    item.save(update_fields=['saldo', 'atualizado_por', 'atualizado_em'])
    extra = {'lote': lote} if lote else {}
    return MovimentoUniforme.objects.create(
        item=item, tipo=tipo, quantidade=quantidade, delta=delta, saldo_apos=novo,
        colaborador=colaborador, data=data, observacao=(observacao or '').strip(),
        nota_fiscal=(nota_fiscal or '').strip()[:60], registrado_por=por,
        registrado_em=agora or timezone.now(), **extra)


@transaction.atomic
def registrar_entrada(*, item_id, quantidade, por, data=None, nota_fiscal='', observacao=''):
    quantidade = quantidade_valida(quantidade)
    item = _travar([item_id])[int(item_id)]
    return _gravar(item, tipo=M.ENTRADA, quantidade=quantidade, delta=quantidade, por=por,
                   data=data or timezone.localdate(), nota_fiscal=nota_fiscal, observacao=observacao)


@transaction.atomic
def registrar_retirada(*, linhas, colaborador, por, data_entrega=None, observacao=''):
    """Tira do estoque e põe na mão do colaborador. Tudo ou nada.

    `data_entrega` pode ficar vazia e ser preenchida depois (o dia da retirada
    já fica gravado em `registrado_em`).
    """
    if colaborador is None:
        raise ErroEstoque('Escolha para quem é a retirada.')
    juntas = _juntar(linhas)
    travados = _travar(list(juntas))
    lote, agora, feitos = None, timezone.now(), []
    for item_id, qtd in juntas.items():
        mov = _gravar(travados[item_id], tipo=M.RETIRADA, quantidade=qtd, delta=-qtd, por=por,
                      colaborador=colaborador, data=data_entrega, observacao=observacao,
                      lote=lote, agora=agora)
        lote = mov.lote
        feitos.append(mov)
    return feitos


def _soma_posse():
    """Entregue conta +, devolvido conta − (só retirada e devolução mexem na posse)."""
    return Sum(Case(When(tipo=M.RETIRADA, then=F('quantidade')),
                    When(tipo=M.DEVOLUCAO, then=-F('quantidade')),
                    default=0, output_field=IntegerField()))


def posse(colaborador, item_ids=None) -> dict:
    """{item_id: peças com o colaborador} (entregues − devolvidas), só o que for > 0."""
    qs = MovimentoUniforme.objects.filter(colaborador=colaborador, tipo__in=[M.RETIRADA, M.DEVOLUCAO])
    if item_ids is not None:
        qs = qs.filter(item_id__in=item_ids)
    linhas = qs.values('item_id').annotate(total=_soma_posse())
    return {linha['item_id']: linha['total'] for linha in linhas if linha['total'] > 0}


@transaction.atomic
def registrar_devolucao(*, linhas, colaborador, por, data=None, observacao=''):
    """Volta para o estoque o que estava com o colaborador (ex.: desligamento)."""
    if colaborador is None:
        raise ErroEstoque('Escolha quem está devolvendo.')
    juntas = _juntar(linhas)
    travados = _travar(list(juntas))   # trava antes de contar: duas devoluções não passam da posse
    com_ele = posse(colaborador, list(juntas))
    for item_id, qtd in juntas.items():
        if qtd > com_ele.get(item_id, 0):
            raise ErroEstoque(f'{colaborador.get_full_name() or colaborador.email} tem '
                              f'{com_ele.get(item_id, 0)} de {travados[item_id].nome}; não dá para devolver {qtd}.')
    lote, agora, feitos = None, timezone.now(), []
    for item_id, qtd in juntas.items():
        mov = _gravar(travados[item_id], tipo=M.DEVOLUCAO, quantidade=qtd, delta=qtd, por=por,
                      colaborador=colaborador, data=data or timezone.localdate(),
                      observacao=observacao, lote=lote, agora=agora)
        lote = mov.lote
        feitos.append(mov)
    return feitos


@transaction.atomic
def registrar_ajuste(*, item_id, contagem, por, motivo, data=None):
    """Acerta o saldo para o que foi contado na prateleira. Motivo obrigatório.

    ``data``: o dia da contagem (a importação da conferência manda a "DATA AJUSTE" da planilha).
    """
    motivo = (motivo or '').strip()
    if not motivo:
        raise ErroEstoque('Diga o motivo do ajuste (ex.: inventário de novembro, peça danificada).')
    try:
        contagem = int(str(contagem).strip())
    except (TypeError, ValueError):
        raise ErroEstoque('Informe a quantidade contada em número inteiro.')
    if contagem < 0:
        raise ErroEstoque('A contagem não pode ser negativa.')
    item = _travar([item_id])[int(item_id)]
    delta = contagem - item.saldo
    if delta == 0:
        raise ErroEstoque(f'O saldo de {item.nome} já é {contagem}; nada a ajustar.')
    return _gravar(item, tipo=M.AJUSTE, quantidade=abs(delta), delta=delta, por=por,
                   data=data or timezone.localdate(), observacao=motivo)


@transaction.atomic
def alterar_data_entrega(movimento_id, data, por):
    mov = MovimentoUniforme.objects.select_for_update().get(pk=movimento_id, tipo=M.RETIRADA)
    MovimentoUniforme.objects.filter(lote=mov.lote, tipo=M.RETIRADA).update(
        data=data, data_alterada_por=por, data_alterada_em=timezone.now())
    return mov.lote


# ---------------------------------------------------------------------------
# Leituras
# ---------------------------------------------------------------------------
def em_posse_por_item() -> dict:
    """{item_id: peças com colaboradores} na rede toda — uma consulta."""
    linhas = (MovimentoUniforme.objects.filter(tipo__in=[M.RETIRADA, M.DEVOLUCAO])
              .values('item_id').annotate(total=_soma_posse()))
    return {linha['item_id']: linha['total'] for linha in linhas}


def colaboradores_com_posse():
    """[(user_id, peças)] de quem está com alguma peça — uma consulta."""
    linhas = (MovimentoUniforme.objects.filter(tipo__in=[M.RETIRADA, M.DEVOLUCAO])
              .values('colaborador_id').annotate(total=_soma_posse())
              .filter(total__gt=0))
    return {linha['colaborador_id']: linha['total'] for linha in linhas}


def uniformes_da_ficha(colaborador) -> dict:
    """O que a ficha mostra: o que a pessoa tem hoje e cada entrega — uma consulta.

    `pecas`: [{item, quantidade, ultima_entrega}] só do que está com ela;
    `entregas`: lotes de retirada/devolução, do mais novo ao mais velho.
    """
    movs = list(MovimentoUniforme.objects
                .filter(colaborador=colaborador, tipo__in=[M.RETIRADA, M.DEVOLUCAO])
                .select_related('item', 'registrado_por')
                .order_by('registrado_em', 'id'))
    saldo, ultima, itens = defaultdict(int), {}, {}
    lotes = OrderedDict()
    for mov in movs:
        itens[mov.item_id] = mov.item
        if mov.tipo == M.RETIRADA:
            saldo[mov.item_id] += mov.quantidade
            ultima[mov.item_id] = mov.data or timezone.localtime(mov.registrado_em).date()
        else:
            saldo[mov.item_id] -= mov.quantidade
        lote = lotes.setdefault(mov.lote, {'lote': mov.lote, 'tipo': mov.tipo, 'rotulo': mov.rotulo_curto,
                                           'data': mov.data, 'registrado_em': mov.registrado_em,
                                           'registrado_por': mov.registrado_por, 'linhas': [],
                                           'primeiro_id': mov.id})
        lote['linhas'].append(mov)
    pecas = [{'item': itens[i], 'quantidade': q, 'ultima_entrega': ultima.get(i)}
             for i, q in saldo.items() if q > 0]
    pecas.sort(key=lambda p: (p['item'].tipo, p['item'].tamanho))
    return {
        'pecas': pecas,
        'total': sum(p['quantidade'] for p in pecas),
        'entregas': list(reversed(lotes.values())),
    }


def filtrar_movimentos(params):
    """Histórico filtrado pelos parâmetros da tela (GET)."""
    qs = (MovimentoUniforme.objects
          .select_related('item', 'colaborador', 'colaborador__sector', 'registrado_por'))
    tipo = (params.get('tipo') or '').upper()
    if tipo in dict(M.TIPO_CHOICES):
        qs = qs.filter(tipo=tipo)
    if (params.get('item') or '').isdigit():
        qs = qs.filter(item_id=int(params['item']))
    if (params.get('colaborador') or '').isdigit():
        qs = qs.filter(colaborador_id=int(params['colaborador']))
    if (params.get('por') or '').isdigit():
        qs = qs.filter(registrado_por_id=int(params['por']))
    from datetime import date
    for chave, campo in (('de', 'registrado_em__date__gte'), ('ate', 'registrado_em__date__lte')):
        try:
            qs = qs.filter(**{campo: date.fromisoformat(params.get(chave) or '')})
        except ValueError:
            pass
    busca = (params.get('q') or '').strip()
    if busca:
        qs = qs.filter(Q(colaborador__first_name__icontains=busca) | Q(colaborador__last_name__icontains=busca)
                       | Q(item__tipo__icontains=busca) | Q(observacao__icontains=busca)
                       | Q(nota_fiscal__icontains=busca))
    return qs.order_by('-registrado_em', '-id')


def filtrar_itens(params, so_ativos=True):
    qs = ItemUniforme.objects.all()
    if so_ativos and params.get('inativos') != '1':
        qs = qs.filter(ativo=True)
    if params.get('tipo'):
        qs = qs.filter(tipo__iexact=params['tipo'])
    if params.get('tamanho'):
        qs = qs.filter(tamanho__iexact=params['tamanho'])
    busca = (params.get('q') or '').strip()
    if busca:
        qs = qs.filter(Q(tipo__icontains=busca) | Q(descricao__icontains=busca) | Q(tamanho__iexact=busca))
    return qs.order_by('tipo', 'tamanho', 'descricao')


