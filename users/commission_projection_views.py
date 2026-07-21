"""Médias de comissão baseadas na projeção do simulador.

Página irmã de ``/users/commission/`` (que mostra o realizado das planilhas):
aqui os valores vêm do motor de ``/simulator/`` no modo Projeção, agregados em
médias que se ajustam conforme os filtros de coordenador, loja e papel.

Escopo de visão espelha o de ``/users/commission/``:
- superadmin: rede inteira
- coordenador: usuários das lojas sob sua coordenação
- gerente: usuários da própria loja
- demais (CN/recepcionista): apenas o próprio, com a média da loja como referência
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from simulator.averages import (
    ROLE_LABELS,
    ROLE_ORDER,
    get_projection_dataset,
    summarize,
)
from simulator.services import ROLE_CONSULTOR, get_coordinator_sectors

from .commission_views import MONTH_NAMES_PT, get_user_role


def _scope_rows(user, viewer_role, rows):
    """Recorta o dataset da rede para o que o usuário pode ver.

    Retorna ``(linhas_listadas, linhas_de_referencia)``. As de referência não
    são exibidas: servem só para a média de comparação de quem enxerga apenas
    o próprio número.
    """
    if viewer_role == 'superadmin':
        return rows, []

    if viewer_role == 'coordenador':
        sector_ids = {s.id for s in get_coordinator_sectors(user)}
        scoped = [
            row for row in rows
            if row['id'] == user.id or (row['sector_id'] and row['sector_id'] in sector_ids)
        ]
        return scoped, []

    if viewer_role == 'gerente':
        scoped = [
            row for row in rows
            if row['id'] == user.id
            or (user.sector_id and row['sector_id'] == user.sector_id)
        ]
        return scoped, []

    # CN / recepcionista: vê só o próprio número, com a loja como referência.
    own = [row for row in rows if row['id'] == user.id]
    store = [
        row for row in rows
        if user.sector_id
        and row['sector_id'] == user.sector_id
        and row['role_key'] == ROLE_CONSULTOR
    ]
    return own, store


@login_required
def commission_projection_view(request):
    """Médias de comissão projetadas, com filtros que recalculam a média."""
    user = request.user
    viewer_role = get_user_role(user)
    dataset = get_projection_dataset(force_refresh=request.GET.get('refresh') == '1')

    rows, reference_rows = _scope_rows(user, viewer_role, dataset['rows'])

    # Referência de loja para quem só enxerga o próprio valor.
    store_summary = summarize(reference_rows) if reference_rows else None

    # Opções de filtro derivadas do que está em tela.
    coordinators = sorted({r['coordinator'] for r in rows if r['coordinator']})
    sectors = sorted({r['sector'] for r in rows if r['sector']})
    role_counts = {}
    for row in rows:
        role_counts[row['role_key']] = role_counts.get(row['role_key'], 0) + 1
    role_filters = [
        {'key': key, 'label': ROLE_LABELS[key], 'count': role_counts[key]}
        for key in ROLE_ORDER if role_counts.get(key)
    ]

    generated_at = dataset['generated_at']
    context = {
        'user': user,
        'role': viewer_role,
        'rows': rows,
        'summary': summarize(rows),
        'store_summary': store_summary,
        'coordinator_options': coordinators,
        'sector_options': sectors,
        'role_filters': role_filters,
        'status': dataset['status'],
        'is_stale': dataset['is_stale'],
        'generated_at': generated_at,
        'reference_label': (
            f"{MONTH_NAMES_PT.get(generated_at.month, generated_at.month)} {generated_at.year}"
            if generated_at else ''
        ),
        'no_data_count': sum(1 for r in rows if not r['has_data']),
    }
    return render(request, 'users/commission_projecao.html', context)
