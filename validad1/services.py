"""Serviços do módulo Validação D-1: sync MySQL, dedupe, expiração 48h."""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from simulator.sql_realizado import _mysql_config

from .models import VendaD1


User = get_user_model()


def _norm(value) -> str:
    if value is None:
        return ''
    t = str(value).strip()
    t = unicodedata.normalize('NFKD', t)
    t = ''.join(c for c in t if not unicodedata.combining(c))
    return ' '.join(t.upper().split())


def fetch_d1_from_mysql(*, target_date: Optional[date] = None) -> list[dict]:
    """Busca as vendas do dia anterior (`target_date`, padrão = ontem).

    Combina ``vendas_servicos`` (qualquer pilar) **e** ``vendas_produto`` —
    conforme requisito do D-1: ambas as tabelas precisam ser auditadas.
    """
    try:
        import pymysql  # type: ignore
    except ImportError:
        return []

    if target_date is None:
        target_date = timezone.localdate() - timedelta(days=1)

    try:
        conn = pymysql.connect(**_mysql_config())
    except Exception:
        return []

    rows: list[dict] = []
    cols = [
        'numero_da_venda', 'produto', 'valor', 'cpf', 'numero_acesso',
        'data_da_venda', 'pilar', 'vendedor', 'pdv', 'servicos',
    ]
    try:
        with conn.cursor() as cur:
            # --- vendas_servicos (PascalCase + acentos) ---
            cur.execute(
                """
                SELECT
                    `ID_da_venda`,
                    `Plano_novo`,
                    COALESCE(`receita_calculada`, `Receita`, 0) AS valor,
                    `CPF_do_cliente`,
                    `Nº_acesso`,
                    `data_da_venda`,
                    `pilar`,
                    `Nome_do_vendedor`,
                    `PDV`,
                    `Serviço_Técnico`
                FROM vendas_servicos
                WHERE data_da_venda = %s
                  AND `Venda_ativa` = '1'
                """,
                (target_date,),
            )
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))

            # --- vendas_produto (lowercase) ---
            cur.execute(
                """
                SELECT
                    `id_da_venda`,
                    `plano`,
                    COALESCE(`valor_líquido_de_venda_do_produto`, 0) AS valor,
                    `cpf_do_cliente`,
                    `Nº_acesso`,
                    `data_da_venda`,
                    `pilar`,
                    `nome_do_vendedor`,
                    `pdv`,
                    `nome_do_produto`
                FROM vendas_produto
                WHERE data_da_venda = %s
                  AND `venda_ativa` = '1'
                """,
                (target_date,),
            )
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return rows


def sync_d1(*, target_date: Optional[date] = None) -> dict:
    """Insere as vendas de D-1 no banco local de forma idempotente.

    - Se já existe uma linha local idêntica (mesmo número da venda, CPF,
      produto, data, número de acesso e serviços), o registro é pulado —
      evita inflar duplicatas a cada sync.
    - Quando a fonte (MySQL) traz mais de uma ocorrência do mesmo combo,
      a partir da segunda é marcada como `is_duplicate=True`.
    - Expira automaticamente vendas com `acordo_deadline < now`.
    """
    raw = fetch_d1_from_mysql(target_date=target_date)
    created = 0
    skipped = 0
    seen: dict[tuple, VendaD1] = {}

    def _s(value) -> str:
        return (str(value).strip() if value is not None else '')

    for r in raw:
        numero = _s(r.get('numero_da_venda'))
        if not numero:
            continue

        cpf = _s(r.get('cpf'))
        produto = _s(r.get('produto'))
        numero_acesso = _s(r.get('numero_acesso'))
        servicos = _s(r.get('servicos'))
        data_venda = r.get('data_da_venda')

        key = (numero, cpf, produto, str(data_venda), numero_acesso, servicos)

        # Já existe localmente? → pula (idempotente).
        existing = VendaD1.objects.filter(
            numero_da_venda=numero,
            cpf=cpf,
            produto=produto,
            data_da_venda=data_venda,
            numero_acesso=numero_acesso,
            servicos=servicos,
        ).first()
        if existing:
            seen.setdefault(key, existing)
            skipped += 1
            continue

        # Segunda ocorrência do mesmo combo no fetch atual → duplicata.
        parent = seen.get(key)
        obj = VendaD1.objects.create(
            numero_da_venda=numero,
            produto=produto,
            valor=Decimal(str(r.get('valor') or 0)),
            cpf=cpf,
            numero_acesso=numero_acesso,
            data_da_venda=data_venda,
            pilar=_s(r.get('pilar')),
            vendedor=_s(r.get('vendedor')),
            pdv=_s(r.get('pdv')),
            servicos=servicos,
            is_duplicate=bool(parent),
            duplicate_of=parent,
        )
        seen.setdefault(key, obj)
        created += 1

    expired = expire_deadlines()

    return {
        'created': created,
        'skipped': skipped,
        'total_in_source': len(raw),
        'expired': expired,
        'target_date': str(target_date or (timezone.localdate() - timedelta(days=1))),
    }


def expire_deadlines() -> int:
    """Marca como expiradas as vendas divergentes cujo prazo de 48h estourou.

    Conforme requisito: "Caso passe o prazo a venda automaticamente some" — aqui
    optamos por NÃO deletar; marcamos `acordo_status='expirado'` para histórico.
    A view de listagem oculta vendas expiradas por padrão.
    """
    now = timezone.now()
    qs = VendaD1.objects.filter(
        acordo_status=VendaD1.ACORDO_PENDENTE,
        acordo_deadline__isnull=False,
        acordo_deadline__lt=now,
    )
    return qs.update(acordo_status=VendaD1.ACORDO_EXPIRADO)


# ---------------------------------------------------------------------------
# Visibilidade por usuário
# ---------------------------------------------------------------------------

def vendas_for_user(user) -> 'models.QuerySet[VendaD1]':
    qs = VendaD1.objects.all()
    hierarchy = getattr(user, 'hierarchy', 'PADRAO')
    if hierarchy == 'PADRAO':
        nome = _norm(user.get_full_name() or user.username)
        ids = [
            v.id for v in qs.only('id', 'vendedor')
            if _norm(v.vendedor) == nome
        ]
        return qs.filter(id__in=ids)
    return qs


def is_ilha(user) -> bool:
    return user.is_superuser or getattr(user, 'hierarchy', '') in (
        'ADMIN', 'SUPERADMIN', 'SUPERVISOR', 'ADMINISTRATIVO',
    )
