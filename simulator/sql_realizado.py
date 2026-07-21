"""Realized sales lookup from MySQL (rede_confianca_data).

Queries `vendas_produtos` and `vendas_servicos` for current month and
returns aggregated values per simulator pillar key.
"""

from __future__ import annotations

import os
import threading
import unicodedata
from contextlib import contextmanager
from decimal import Decimal
from typing import Dict, Iterable, Optional
from urllib.parse import unquote, urlparse

from django.core.cache import cache
from django.utils import timezone


DEFAULT_MYSQL_URI = (
    'mysql://redeconfiancaadm:redeconfianca2025@'
    'painel.dev.redeconfianca.com.br:3306/vivogo'
)

# Mapeia o pilar bruto vindo do MySQL → chave usada pelo simulador.
PILAR_TO_KEY = {
    'MOVEL': 'movel',
    'FIXA': 'fixa',
    'SMARTPHONE': 'smartphones',
    'SMARTPHONES': 'smartphones',
    'ELETRONICOS': 'eletronicos',
    'ESSENCIAIS': 'essenciais',
    'SEGURO': 'seguros',
    'SEGUROS': 'seguros',
    'SVA': 'sva',
}

EMPTY_RESULT: Dict[str, float] = {
    'movel': 0.0,
    'fixa': 0.0,
    'smartphones': 0.0,
    'eletronicos': 0.0,
    'essenciais': 0.0,
    'seguros': 0.0,
    'sva': 0.0,
    'fixa_qty': 0.0,
}


def _normalize(value) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return ' '.join(text.upper().split())


def _mysql_config() -> dict:
    uri = os.getenv('MYSQL_URI', DEFAULT_MYSQL_URI)
    parsed = urlparse(uri)
    return {
        'host': parsed.hostname,
        'port': parsed.port or 3306,
        'user': unquote(parsed.username or ''),
        'password': unquote(parsed.password or ''),
        'database': parsed.path.lstrip('/'),
        'charset': 'utf8mb4',
        'connect_timeout': 10,
    }


def _to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    text = str(value).strip().replace('R$', '').replace(' ', '')
    if not text:
        return 0.0
    if ',' in text and '.' in text:
        text = text.replace('.', '').replace(',', '.')
    else:
        text = text.replace(',', '.')
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _coordinator_pdvs(cursor, coord_name: str) -> list[str]:
    """Devolve lista de PDVs (já normalizados em UPPER) para um coordenador.

    Observação: o banco ``vivogo`` não possui as tabelas ``coordenador`` /
    ``coordenador_pdv``. Mantemos a função por compatibilidade, mas devolvemos
    lista vazia em caso de erro (o caller deve passar ``pdvs=[...]`` extraídos
    da planilha quando filtrar por coordenador).
    """
    if not coord_name:
        return []
    target = _normalize(coord_name)
    try:
        cursor.execute(
            """
            SELECT cp.pdv
            FROM coordenador c
            JOIN coordenador_pdv cp ON cp.coordenador_id = c.id
            WHERE UPPER(c.nome) LIKE %s OR UPPER(c.nome) = %s
            """,
            (f'{target}%', target),
        )
    except Exception:
        return []
    pdvs = [_normalize(row[0]) for row in cursor.fetchall() if row and row[0]]
    return [p for p in pdvs if p]


# ---------------------------------------------------------------------------
# Prefetch em lote
#
# ``get_realized_sales_from_mysql`` abre uma conexão por chamada, e o simulador
# faz 3 chamadas por usuário (individual + PDV + coordenação). Para telas que
# calculam a rede inteira isso vira centenas de conexões. O prefetch resolve o
# mês inteiro em 6 consultas agrupadas e deixa os mapas num contexto de thread;
# enquanto ele estiver ativo, as consultas individuais são servidas de memória.
# ---------------------------------------------------------------------------

_STATE = threading.local()


def _fetch_grouped(cur, group_col_produto: str, group_col_servico: str,
                   year: int, month: int, yesterday) -> Dict[str, Dict[str, float]]:
    """Roda as 3 consultas do mês agrupadas por vendedor OU por PDV.

    Espelha exatamente os filtros de ``get_realized_sales_from_mysql`` (corte
    D-1, venda ativa/confirmada, Fixa vinda só do Serviço Técnico).
    """
    data: Dict[str, Dict[str, float]] = {}

    def bucket(name) -> Optional[Dict[str, float]]:
        key = _normalize(name)
        if not key:
            return None
        return data.setdefault(key, dict(EMPTY_RESULT))

    base_where = (
        'YEAR(data_da_venda) = %s AND MONTH(data_da_venda) = %s '
        'AND data_da_venda <= %s'
    )
    params = (year, month, yesterday)

    # ---------- vendas_produto (Smartphone / Eletronicos / Essenciais) ----------
    cur.execute(
        f"""
        SELECT `{group_col_produto}`, pilar, SUM(`valor_líquido_de_venda_do_produto`)
        FROM vendas_produto
        WHERE {base_where}
        GROUP BY `{group_col_produto}`, pilar
        """,
        params,
    )
    for name, pilar, total in cur.fetchall():
        key = PILAR_TO_KEY.get(_normalize(pilar))
        row = bucket(name)
        if key and row is not None:
            row[key] = row.get(key, 0.0) + _to_float(total)

    # ---------- vendas_servicos (Movel / SVA / Seguros) ----------
    cur.execute(
        f"""
        SELECT `{group_col_servico}`, pilar, SUM(COALESCE(receita_calculada, Receita, 0))
        FROM vendas_servicos
        WHERE {base_where}
          AND Venda_ativa = '1' AND `Status_do_Serviço` = 'Confirmado'
        GROUP BY `{group_col_servico}`, pilar
        """,
        params,
    )
    for name, pilar, total in cur.fetchall():
        key = PILAR_TO_KEY.get(_normalize(pilar))
        # Fixa é definida exclusivamente pelo Serviço Técnico (consulta abaixo).
        if not key or key == 'fixa':
            continue
        row = bucket(name)
        if row is not None:
            row[key] = row.get(key, 0.0) + _to_float(total)

    # ---------- Pilar Fixa = Serviço Técnico 'Alta Banda Larga' / 'Alta TV' ----------
    cur.execute(
        f"""
        SELECT `{group_col_servico}`,
               SUM(COALESCE(receita_calculada, Receita, 0)) AS total_valor,
               COUNT(*) AS qtd
        FROM vendas_servicos
        WHERE {base_where}
          AND Venda_ativa = '1' AND `Status_do_Serviço` = 'Confirmado'
          AND UPPER(`Serviço_Técnico`) IN ('ALTA BANDA LARGA', 'ALTA TV')
        GROUP BY `{group_col_servico}`
        """,
        params,
    )
    for name, total_valor, qtd in cur.fetchall():
        row = bucket(name)
        if row is not None:
            row['fixa'] = row.get('fixa', 0.0) + _to_float(total_valor)
            row['fixa_qty'] = row.get('fixa_qty', 0.0) + float(qtd or 0)

    return data


def build_realized_maps(year: Optional[int] = None, month: Optional[int] = None) -> Dict:
    """Lê o mês inteiro em 6 consultas e devolve mapas por vendedor e por PDV.

    Chaves dos mapas já vêm normalizadas (upper, sem acento), iguais às usadas
    em ``get_realized_sales_from_mysql``.
    """
    from datetime import timedelta

    now = timezone.now()
    year = year or now.year
    month = month or now.month
    yesterday = timezone.localdate() - timedelta(days=1)

    empty = {'year': year, 'month': month, 'vendors': {}, 'pdvs': {}, 'ok': False}

    try:
        import pymysql
    except ImportError:
        return empty

    try:
        conn = pymysql.connect(**_mysql_config())
    except Exception:
        return empty

    try:
        with conn.cursor() as cur:
            vendors = _fetch_grouped(cur, 'nome_do_vendedor', 'Nome_do_vendedor', year, month, yesterday)
            pdvs = _fetch_grouped(cur, 'pdv', 'PDV', year, month, yesterday)
    except Exception:
        return empty
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {'year': year, 'month': month, 'vendors': vendors, 'pdvs': pdvs, 'ok': True}


def get_realized_maps(year: Optional[int] = None, month: Optional[int] = None,
                      force_refresh: bool = False, ttl: int = 900) -> Dict:
    """``build_realized_maps`` com cache (15 min por padrão)."""
    now = timezone.now()
    year = year or now.year
    month = month or now.month
    cache_key = f'simulator_realized_maps_{year}_{month:02d}'

    if not force_refresh:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    maps = build_realized_maps(year, month)
    # Falha de conexão não é cacheada: a próxima requisição tenta de novo.
    if maps.get('ok'):
        cache.set(cache_key, maps, ttl)
    return maps


@contextmanager
def realized_prefetch(year: Optional[int] = None, month: Optional[int] = None,
                      force_refresh: bool = False):
    """Serve as consultas de realizado a partir de mapas pré-carregados.

    Dentro do bloco, ``get_realized_sales_from_mysql`` não abre conexão para
    filtros por vendedor/PDV — responde do mapa. Filtro por ``coord_name``
    (sem lista de PDVs) continua indo ao banco.
    """
    maps = get_realized_maps(year, month, force_refresh=force_refresh)
    previous = getattr(_STATE, 'maps', None)
    _STATE.maps = maps if maps.get('ok') else None
    try:
        yield maps
    finally:
        _STATE.maps = previous


def _lookup_prefetched(year: int, month: int, vendor: str, pdv: str,
                       pdvs: Optional[Iterable[str]], coord_name: str) -> Optional[Dict[str, float]]:
    """Resposta a partir do prefetch ativo, ou ``None`` para ir ao banco."""
    maps = getattr(_STATE, 'maps', None)
    if not maps or maps.get('year') != year or maps.get('month') != month:
        return None
    # Resolver PDVs pelo nome do coordenador depende de tabelas que não existem
    # no banco `vivogo`; mantém o caminho original nesse caso.
    if coord_name and not pdvs:
        return None

    if vendor:
        found = maps['vendors'].get(_normalize(vendor))
        return dict(found) if found else dict(EMPTY_RESULT)

    targets = [_normalize(p) for p in (pdvs or [])] if pdvs else ([_normalize(pdv)] if pdv else [])
    targets = [t for t in targets if t]
    if not targets:
        # Sem filtro = não retornar todas as vendas da rede.
        return dict(EMPTY_RESULT)

    total = dict(EMPTY_RESULT)
    for target in targets:
        found = maps['pdvs'].get(target)
        if not found:
            continue
        for key, value in found.items():
            total[key] = total.get(key, 0.0) + value
    return total


def get_realized_sales_from_mysql(
    *,
    vendor: str = '',
    pdv: str = '',
    pdvs: Optional[Iterable[str]] = None,
    coord_name: str = '',
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> Dict[str, float]:
    """Soma o realizado por pilar no mês corrente.

    Filtros (apenas um costuma ser fornecido):
    - ``vendor``: nome do consultor (compara com nome_do_vendedor / USUARIO_NOME).
    - ``pdv``: nome de uma única loja (gerente).
    - ``pdvs``: lista de PDVs (caso já se conheçam).
    - ``coord_name``: nome do coordenador → resolve PDVs via `coordenador_pdv`.

    Retorna ``{pilar: valor_em_reais, 'fixa_qty': contagem_de_vendas_fixa}``.
    Em caso de erro de conexão, devolve zeros (não bloqueia o simulador).
    """
    from datetime import timedelta
    now = timezone.now()
    year = year or now.year
    month = month or now.month
    # Corte D-1: considera apenas vendas até ontem (dados do dia atual
    # ainda não estão fechados/consolidados na origem).
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    # Dentro de um `realized_prefetch`, responde sem abrir conexão.
    prefetched = _lookup_prefetched(year, month, vendor, pdv, pdvs, coord_name)
    if prefetched is not None:
        return prefetched

    try:
        import pymysql
    except ImportError:
        return dict(EMPTY_RESULT)

    try:
        conn = pymysql.connect(**_mysql_config())
    except Exception:
        return dict(EMPTY_RESULT)

    result = dict(EMPTY_RESULT)
    try:
        with conn.cursor() as cur:
            target_pdvs: list[str] = []
            if coord_name:
                target_pdvs = _coordinator_pdvs(cur, coord_name)
                if not target_pdvs:
                    return result
            elif pdvs:
                target_pdvs = [_normalize(p) for p in pdvs if p]
            elif pdv:
                target_pdvs = [_normalize(pdv)]

            vendor_norm = _normalize(vendor)

            # Filtro de competência usa `data_da_venda` (presente em vendas_produto e vendas_servicos).
            # Corte D-1: considera apenas vendas até ontem.
            base_where = ['YEAR(data_da_venda) = %s', 'MONTH(data_da_venda) = %s', 'data_da_venda <= %s']
            base_params: list = [year, month, yesterday]

            if vendor_norm:
                base_where.append('UPPER({col}) = %s')
                base_params.append(vendor_norm)
            elif target_pdvs:
                placeholders = ','.join(['%s'] * len(target_pdvs))
                base_where.append(f'UPPER({{col}}) IN ({placeholders})')
                base_params.extend(target_pdvs)
            else:
                # Sem filtro = não retornar todas as vendas da rede
                return result

            # ---------- vendas_produto (Smartphone / Eletronicos / Essenciais) ----------
            where_p = ' AND '.join(w.format(col='nome_do_vendedor' if vendor_norm else 'pdv') for w in base_where)
            cur.execute(
                f"""
                SELECT pilar, SUM(`valor_líquido_de_venda_do_produto`)
                FROM vendas_produto
                WHERE {where_p}
                GROUP BY pilar
                """,
                tuple(base_params),
            )
            for pilar, total in cur.fetchall():
                key = PILAR_TO_KEY.get(_normalize(pilar))
                if key:
                    result[key] = result.get(key, 0.0) + _to_float(total)

            # ---------- vendas_servicos (Movel / SVA / Seguros) ----------
            # Filtros extras: Venda_ativa='1' AND Status_do_Serviço='Confirmado'.
            where_s = ' AND '.join(w.format(col='Nome_do_vendedor' if vendor_norm else 'PDV') for w in base_where)
            where_s += " AND Venda_ativa = '1' AND `Status_do_Serviço` = 'Confirmado'"
            cur.execute(
                f"""
                SELECT pilar,
                       SUM(COALESCE(receita_calculada, Receita, 0)) AS total_valor,
                       COUNT(*) AS qtd
                FROM vendas_servicos
                WHERE {where_s}
                GROUP BY pilar
                """,
                tuple(base_params),
            )
            for pilar, total_valor, qtd in cur.fetchall():
                key = PILAR_TO_KEY.get(_normalize(pilar))
                if not key:
                    continue
                # Pilar 'Fixa' agora é definido EXCLUSIVAMENTE pelo Serviço Técnico
                # ('Alta Banda Larga' / 'Alta TV') — ignora o pilar bruto do banco.
                if key == 'fixa':
                    continue
                result[key] = result.get(key, 0.0) + _to_float(total_valor)

            # ---------- Pilar Fixa = Serviço Técnico 'Alta Banda Larga' / 'Alta TV' ----------
            where_st = ' AND '.join(w.format(col='Nome_do_vendedor' if vendor_norm else 'PDV') for w in base_where)
            where_st += " AND Venda_ativa = '1' AND `Status_do_Serviço` = 'Confirmado'"
            cur.execute(
                f"""
                SELECT SUM(COALESCE(receita_calculada, Receita, 0)) AS total_valor,
                       COUNT(*) AS qtd
                FROM vendas_servicos
                WHERE {where_st}
                  AND UPPER(`Serviço_Técnico`) IN ('ALTA BANDA LARGA', 'ALTA TV')
                """,
                tuple(base_params),
            )
            row_st = cur.fetchone()
            if row_st:
                add_valor, add_qtd = row_st
                result['fixa'] = result.get('fixa', 0.0) + _to_float(add_valor)
                result['fixa_qty'] = result.get('fixa_qty', 0.0) + float(add_qtd or 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result


def get_network_realized_sales_from_mysql(
    *,
    year: Optional[int] = None,
    month: Optional[int] = None,
) -> Dict[str, float]:
    """Soma o realizado por pilar de TODA a rede (sem filtro de vendedor/PDV).

    Usado pelo comissionamento "A parte", que mede o atingimento da rede inteira.
    Mesma lógica de ``get_realized_sales_from_mysql`` (corte D-1, filtros de
    serviço), porém sem cláusula de filtro por vendedor/loja.

    Retorna ``{pilar: valor_em_reais, 'fixa_qty': contagem_de_vendas_fixa}``.
    Em caso de erro de conexão, devolve zeros (não bloqueia).
    """
    try:
        import pymysql
    except ImportError:
        return dict(EMPTY_RESULT)

    from datetime import timedelta
    now = timezone.now()
    year = year or now.year
    month = month or now.month
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    try:
        conn = pymysql.connect(**_mysql_config())
    except Exception:
        return dict(EMPTY_RESULT)

    result = dict(EMPTY_RESULT)
    base_where = 'YEAR(data_da_venda) = %s AND MONTH(data_da_venda) = %s AND data_da_venda <= %s'
    base_params = (year, month, yesterday)
    try:
        with conn.cursor() as cur:
            # ---------- vendas_produto (Smartphone / Eletronicos / Essenciais) ----------
            cur.execute(
                f"""
                SELECT pilar, SUM(`valor_líquido_de_venda_do_produto`)
                FROM vendas_produto
                WHERE {base_where}
                GROUP BY pilar
                """,
                base_params,
            )
            for pilar, total in cur.fetchall():
                key = PILAR_TO_KEY.get(_normalize(pilar))
                if key:
                    result[key] = result.get(key, 0.0) + _to_float(total)

            # ---------- vendas_servicos (Movel / SVA / Seguros) ----------
            cur.execute(
                f"""
                SELECT pilar, SUM(COALESCE(receita_calculada, Receita, 0)) AS total_valor
                FROM vendas_servicos
                WHERE {base_where}
                  AND Venda_ativa = '1' AND `Status_do_Serviço` = 'Confirmado'
                GROUP BY pilar
                """,
                base_params,
            )
            for pilar, total_valor in cur.fetchall():
                key = PILAR_TO_KEY.get(_normalize(pilar))
                if not key or key == 'fixa':
                    continue
                result[key] = result.get(key, 0.0) + _to_float(total_valor)

            # ---------- Pilar Fixa = Serviço Técnico 'Alta Banda Larga' / 'Alta TV' ----------
            cur.execute(
                f"""
                SELECT SUM(COALESCE(receita_calculada, Receita, 0)) AS total_valor,
                       COUNT(*) AS qtd
                FROM vendas_servicos
                WHERE {base_where}
                  AND Venda_ativa = '1' AND `Status_do_Serviço` = 'Confirmado'
                  AND UPPER(`Serviço_Técnico`) IN ('ALTA BANDA LARGA', 'ALTA TV')
                """,
                base_params,
            )
            row_st = cur.fetchone()
            if row_st:
                add_valor, add_qtd = row_st
                result['fixa'] = result.get('fixa', 0.0) + _to_float(add_valor)
                result['fixa_qty'] = result.get('fixa_qty', 0.0) + float(add_qtd or 0)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result
