"""Médias de comissão a partir da projeção do simulador.

Calcula o ganho projetado (mesmo motor de ``/simulator/`` no modo Projeção) de
toda a rede de uma vez, para a tela de médias em ``/users/commission/projecao/``.

O cálculo completo custa dezenas de segundos — planilhas, MySQL e metas do
Power BI para ~140 pessoas — então nunca roda dentro da requisição do usuário:

- as consultas de realizado do mês são resolvidas em lote por ``realized_prefetch``
  (6 consultas agrupadas no lugar de 3 por pessoa);
- o resultado fica em cache e é servido imediatamente, mesmo vencido, enquanto
  uma thread em segundo plano recalcula (stale-while-revalidate);
- só a primeira carga (cache vazio) mostra tela de "calculando".

Cada visão filtra o dataset da rede pelo seu escopo, então coordenador e gerente
reaproveitam o mesmo cálculo do superadmin.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any, Dict, List, Optional

from django.core.cache import cache
from django.utils import timezone

from users.models import User

from .services import (
    ROLE_APART,
    ROLE_CONSULTOR,
    ROLE_COORDENADOR,
    ROLE_GERENTE,
    VIEW_PROJECAO,
    compute_aparte_simulation,
    compute_consultor_simulation,
    compute_coordenador_simulation,
    compute_gerente_simulation,
    get_all_aparte_users,
    get_all_consultors,
    get_all_coordinators,
    get_all_gerentes,
    get_all_snipers,
    get_factor_set,
    get_store_name_from_user,
)
from .sql_realizado import realized_prefetch


logger = logging.getLogger(__name__)


ROLE_LABELS = {
    ROLE_CONSULTOR: 'Consultor',
    ROLE_GERENTE: 'Gerente',
    ROLE_COORDENADOR: 'Coordenador',
    ROLE_APART: 'A parte',
}

# Ordem de exibição dos papéis nos filtros da tela.
ROLE_ORDER = [ROLE_COORDENADOR, ROLE_GERENTE, ROLE_CONSULTOR, ROLE_APART]

DATASET_KEY = 'simulator_projection_dataset'
ERROR_KEY = 'simulator_projection_last_error'

# O realizado vem do MySQL com corte D-1 (só vendas até ontem), então o número
# projetado não muda ao longo do dia: recalcular de hora em hora só gastaria
# ~1 min de CPU para chegar ao mesmo resultado. O dataset vale o dia inteiro e
# é refeito na primeira visita do dia seguinte (ou no botão "Recalcular").
DATASET_TTL = 7 * 86400  # sobrevive a fins de semana sem acesso
ERROR_BACKOFF = 300      # após falha, espera antes de tentar de novo


# ---------------------------------------------------------------------------
# Cálculo
# ---------------------------------------------------------------------------

def build_roster() -> List[Dict[str, Any]]:
    """Todos os usuários comissionáveis com o papel usado no cálculo.

    Mesma precedência da seleção de ``/simulator/`` para superadmin: "A parte"
    ganha de todos, sniper calcula como coordenador, depois gerente/consultor.
    """
    aparte_users = get_all_aparte_users()
    coordinators = get_all_coordinators()
    gerentes = get_all_gerentes()
    consultors = get_all_consultors()
    snipers = get_all_snipers()

    aparte_ids = {u.id for u in aparte_users}
    coordinator_ids = {u.id for u in coordinators}

    roster: List[Dict[str, Any]] = []
    seen: set = set()

    def add(user: User, role: str) -> None:
        if user.id in seen:
            return
        seen.add(user.id)
        roster.append({'user': user, 'role': role})

    for user in aparte_users:
        add(user, ROLE_APART)
    for user in coordinators:
        if user.id not in aparte_ids:
            add(user, ROLE_COORDENADOR)
    for user in snipers:
        # Sniper usa o cálculo de coordenador (75% do coordenador atribuído).
        if user.id not in coordinator_ids and user.id not in aparte_ids:
            add(user, ROLE_COORDENADOR)
    for user in gerentes:
        if user.id not in aparte_ids:
            add(user, ROLE_GERENTE)
    for user in consultors:
        if user.id not in aparte_ids:
            add(user, ROLE_CONSULTOR)

    return roster


def _factor_data_for(role: str, cache_by_role: Dict[str, Any]) -> Any:
    """Fatores do papel, resolvidos uma vez por lote (evita 1 query por usuário)."""
    if role not in cache_by_role:
        cache_by_role[role] = get_factor_set(role).data
    return cache_by_role[role]


def _compute_simulation(user: User, role: str, factors: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Roda o simulador no modo Projeção para um usuário."""
    if role == ROLE_APART:
        from users.models import AParteCommissionConfig
        config = AParteCommissionConfig.objects.filter(user=user).first()
        return compute_aparte_simulation(user, config, {}, view_mode=VIEW_PROJECAO)

    factor_data = _factor_data_for(role, factors)
    if role == ROLE_CONSULTOR:
        return compute_consultor_simulation(user, factor_data, {}, view_mode=VIEW_PROJECAO)
    if role == ROLE_GERENTE:
        return compute_gerente_simulation(user, factor_data, {}, view_mode=VIEW_PROJECAO)
    if role == ROLE_COORDENADOR:
        return compute_coordenador_simulation(user, factor_data, {}, view_mode=VIEW_PROJECAO)
    return None


def _build_row(entry: Dict[str, Any], factors: Dict[str, Any]) -> Dict[str, Any]:
    user: User = entry['user']
    role: str = entry['role']

    name = user.get_full_name() or user.email
    # Rótulo canônico da loja: o setor cadastrado ("Loja Montserrat") e o PDV da
    # planilha ("MONTSERRAT") são a mesma loja — sem normalizar, o filtro
    # mostraria as duas como opções separadas. É o mesmo nome que o motor de
    # cálculo usa para casar metas de PDV.
    sector = get_store_name_from_user(user)

    row = {
        'id': user.id,
        'name': name,
        'initials': (f"{(user.first_name or '')[:1]}{(user.last_name or '')[:1]}".strip().upper()
                     or (user.email or '?')[:1].upper()),
        'role_key': role,
        'role_label': ROLE_LABELS.get(role, role),
        'sector': sector,
        'sector_id': user.sector_id,
        'coordinator': '',
        'gain': 0.0,
        'has_data': False,
    }

    try:
        simulation = _compute_simulation(user, role, factors)
    except Exception:
        logger.exception('Falha ao projetar comissão de %s (%s)', name, role)
        simulation = None

    if simulation and not simulation.get('error'):
        totals = simulation.get('totals') or {}
        row['gain'] = float(totals.get('ganho_total') or 0.0)
        row['coordinator'] = (simulation.get('coordinator') or '').strip().upper()
        # Sem setor cadastrado, o PDV da planilha é a única pista da loja.
        row['sector'] = sector or (simulation.get('pdv') or '').strip().upper()
        row['has_data'] = True

    return row


def _fill_missing_coordinators(rows: List[Dict[str, Any]]) -> None:
    """Completa o coordenador de quem não tem, usando o da loja.

    A coluna COORDENAÇÃO só está preenchida para parte das pessoas na planilha,
    mas coordenação é propriedade da loja: sem completar, ~40% da rede ficaria
    de fora do filtro por coordenador. Afeta só rótulo e filtro — o cálculo da
    comissão já foi feito e não é tocado aqui.
    """
    by_store: Dict[str, Counter] = {}
    for row in rows:
        if row['coordinator'] and row['sector']:
            by_store.setdefault(row['sector'], Counter())[row['coordinator']] += 1

    for store, votes in by_store.items():
        if len(votes) > 1:
            logger.warning('Loja %s aparece com mais de um coordenador: %s', store, dict(votes))

    for row in rows:
        if row['coordinator'] or not row['sector']:
            continue
        votes = by_store.get(row['sector'])
        if votes:
            row['coordinator'] = votes.most_common(1)[0][0]


def build_projection_rows(roster: List[Dict[str, Any]],
                          force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Ganho projetado de cada usuário do roster, ordenado do maior para o menor."""
    now = timezone.now()
    factors: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []

    # Um único prefetch cobre todas as consultas de realizado do lote.
    with realized_prefetch(now.year, now.month, force_refresh=force_refresh):
        for entry in roster:
            rows.append(_build_row(entry, factors))

    _fill_missing_coordinators(rows)
    rows.sort(key=lambda r: r['gain'], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Cache com atualização em segundo plano
# ---------------------------------------------------------------------------

_BUILD_LOCK = threading.Lock()
_building = False


def _build_dataset(force_refresh: bool = False) -> Dict[str, Any]:
    rows = build_projection_rows(build_roster(), force_refresh=force_refresh)
    now = timezone.now()
    dataset = {'rows': rows, 'generated_at': now, 'built_on': timezone.localdate()}
    cache.set(DATASET_KEY, dataset, DATASET_TTL)
    cache.delete(ERROR_KEY)
    return dataset


def _refresh_in_background(force_refresh: bool = False) -> bool:
    """Dispara o recálculo numa thread. Retorna False se já havia uma rodando."""
    global _building
    with _BUILD_LOCK:
        if _building:
            return False
        _building = True

    def run() -> None:
        global _building
        try:
            _build_dataset(force_refresh=force_refresh)
        except Exception:
            logger.exception('Falha ao recalcular as médias de comissão projetada')
            # Evita abrir thread nova a cada request enquanto o erro persistir.
            cache.set(ERROR_KEY, True, ERROR_BACKOFF)
        finally:
            # Thread própria abre suas conexões de banco; precisa devolvê-las.
            from django.db import connections
            connections.close_all()
            with _BUILD_LOCK:
                _building = False

    threading.Thread(target=run, daemon=True, name='commission-projection').start()
    return True


def get_projection_dataset(force_refresh: bool = False) -> Dict[str, Any]:
    """Dataset da rede, calculado uma vez por dia e servido do cache.

    - Cache vazio: devolve ``status='building'`` (a tela mostra "calculando").
    - Cache de um dia anterior: devolve os dados de ontem imediatamente e
      recalcula em segundo plano — ninguém espera pelo cálculo.
    - Mesmo dia: devolve do cache, sem recalcular.
    """
    dataset = cache.get(DATASET_KEY)
    is_stale = dataset is not None and dataset.get('built_on') != timezone.localdate()
    backing_off = cache.get(ERROR_KEY) is not None

    if force_refresh or ((dataset is None or is_stale) and not backing_off):
        _refresh_in_background(force_refresh=force_refresh)

    if dataset is None:
        return {'rows': [], 'generated_at': None, 'built_on': None,
                'status': 'building', 'is_stale': True}

    return {
        'rows': dataset['rows'],
        'generated_at': dataset['generated_at'],
        'built_on': dataset.get('built_on'),
        'status': 'ready',
        'is_stale': is_stale or force_refresh,
    }


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Total, média e extremos de um conjunto de linhas."""
    gains = [r['gain'] for r in rows]
    total = sum(gains)
    count = len(gains)
    return {
        'count': count,
        'total': total,
        'average': (total / count) if count else 0.0,
        'max': max(gains) if gains else 0.0,
        'min': min(gains) if gains else 0.0,
    }
