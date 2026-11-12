"""Espelho do check-list da Rotina ADM no Kanban do Impulso (12/11/2026).

Pedido: o que fica concluído no "Check-list diário da loja" aparece no Kanban do
Impulso como tarefa concluída, para servir de base ao broche de analista destaque.

Desenho escolhido (o menos invasivo):

- **Nada é gravado no Impulso.** O cartão é montado na hora a partir das
  marcações (``MarcacaoChecklist``). Não existe ``Meta`` espelho — por isso não
  entra no CONFIAR, no percentual, no ranking, no desempate nem no fechamento do
  mês, e não há como duplicar: remarcar o mesmo item no mesmo dia é a mesma
  marcação (única por item e dia), e desmarcar apaga a marcação, então o cartão
  perde o item (ou some, se era o único).
- **Um cartão por pessoa, loja e dia** ("Rotina ADM · Check-list da Loja X · dd/mm"),
  com os itens que aquela pessoa marcou. Um cartão por item poria ~200 cartões por
  mês por analista na coluna Concluída.
- O cartão vai para quem marcou (``feito_por``): é ele o analista que entregou.
- Quem vê: a mesma régua de ``impulso.views._metas_do_usuario`` (superuser vê
  todos; gestor do Impulso vê os dele e a equipe; os demais, só os próprios), com
  os filtros de colaborador, nome, setor e mês do Kanban.
"""
from collections import defaultdict

from django.db.models import Count, Max

LIMITE = 31


def _quem_ve(user):
    """None = todos; senão o conjunto de ids de quem esta pessoa enxerga no Kanban."""
    from impulso.utils import get_colaboradores_do_gestor, is_impulso_manager
    if user.is_superuser:
        return None
    if is_impulso_manager(user):
        return set(get_colaboradores_do_gestor(user).values_list('id', flat=True)) | {user.id}
    return {user.id}


def cartoes_do_kanban(user, f=None, colaborador_id=None, limite=LIMITE):
    """Os cartões concluídos do check-list para a coluna Concluída, ou None quando não há nenhum.

    ``f`` é o dicionário de ``impulso.filtros.ler`` (nome, setor e mês do Kanban).
    """
    from impulso import filtros as filtros_impulso

    from .models import MarcacaoChecklist
    from .ranking import Previstos

    qs = MarcacaoChecklist.objects.filter(feito_por__isnull=False)
    pessoas = _quem_ve(user)
    if pessoas is not None:
        qs = qs.filter(feito_por_id__in=pessoas)
    if colaborador_id:
        qs = qs.filter(feito_por_id=colaborador_id)
    if f:
        if f.get('ativo'):
            ids = filtros_impulso.por(qs, f, 'feito_por').values('id')
            qs = MarcacaoChecklist.objects.filter(id__in=ids)
        qs = filtros_impulso.por_mes(qs, f, 'data')

    grupos = list(qs.values('feito_por_id', 'item__loja_id', 'data')
                  .annotate(n=Count('id'), ultimo=Max('feito_em'))
                  .order_by('-data', '-ultimo'))
    if not grupos:
        return None
    total = len(grupos)
    grupos = grupos[:limite]

    chaves = {(g['feito_por_id'], g['item__loja_id'], g['data']) for g in grupos}
    datas = {g['data'] for g in grupos}
    lojas = {g['item__loja_id'] for g in grupos}
    itens = defaultdict(list)
    marcados_loja = defaultdict(set)
    for m in (MarcacaoChecklist.objects.filter(data__in=datas, item__loja_id__in=lojas)
              .select_related('item', 'item__loja', 'feito_por').order_by('item__ordem', 'item__horario', 'item_id')):
        marcados_loja[(m.item.loja_id, m.data)].add(m.item_id)
        chave = (m.feito_por_id, m.item.loja_id, m.data)
        if chave in chaves:
            itens[chave].append(m)

    previstos = Previstos(lojas)
    cartoes = []
    for g in grupos:
        chave = (g['feito_por_id'], g['item__loja_id'], g['data'])
        marcacoes = itens.get(chave) or []
        if not marcacoes:
            continue
        loja = marcacoes[0].item.loja
        do_dia = previstos.do_dia(loja.id, g['data'], marcados_loja[(loja.id, g['data'])])
        feitos_loja = len(marcados_loja[(loja.id, g['data'])])
        cartoes.append({
            'pessoa': marcacoes[0].feito_por, 'proprio': g['feito_por_id'] == user.id,
            'loja': loja, 'data': g['data'], 'marcacoes': marcacoes, 'n': len(marcacoes),
            'loja_feitos': feitos_loja, 'loja_previstos': len(do_dia),
            'loja_completa': bool(do_dia) and feitos_loja >= len(do_dia),
        })
    from .permissoes import pode_gerir
    return {'cartoes': cartoes, 'total': total, 'restantes': max(total - len(cartoes), 0),
            've_ranking': pode_gerir(user)}
