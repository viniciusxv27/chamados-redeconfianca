"""Realized sales lookup from MySQL (rede_confianca_data).

Queries `vendas_produtos` and `vendas_servicos` for current month and
returns aggregated values per simulator pillar key.
"""

from __future__ import annotations

import os
import unicodedata
from decimal import Decimal
from typing import Dict, Iterable, Optional
from urllib.parse import unquote, urlparse

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
    try:
        import pymysql
    except ImportError:
        return dict(EMPTY_RESULT)

    from datetime import timedelta
    now = timezone.now()
    year = year or now.year
    month = month or now.month
    # Corte D-1: considera apenas vendas até ontem (dados do dia atual
    # ainda não estão fechados/consolidados na origem).
    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

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
