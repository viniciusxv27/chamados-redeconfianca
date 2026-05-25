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
    """Busca as vendas do dia anterior (`target_date`, padrão = ontem)."""
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
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    `Numero_da_venda`,
                    `Plano`,
                    COALESCE(`receita_calculada`, `Receita`, 0) AS valor,
                    `CPF`,
                    `Numero_de_Acesso`,
                    `data_da_venda`,
                    `pilar`,
                    `Nome_do_vendedor`,
                    `PDV`,
                    `Serviço_Técnico`
                FROM vendas_servicos
                WHERE data_da_venda = %s
                  AND Venda_ativa = '1'
                """,
                (target_date,),
            )
            cols = [
                'numero_da_venda', 'produto', 'valor', 'cpf', 'numero_acesso',
                'data_da_venda', 'pilar', 'vendedor', 'pdv', 'servicos',
            ]
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return rows


def sync_d1(*, target_date: Optional[date] = None) -> dict:
    """Insere as vendas de D-1 no banco local (acumula vendas não ajustadas).

    - Cria novas linhas (não atualiza existentes, conforme requisito de acúmulo).
    - Aplica regra de duplicidade: mesmo CPF, número da venda, plano, data,
      número de acesso e serviços → marca como `is_duplicate=True`.
    - Expira automaticamente vendas com `acordo_deadline < now`.
    """
    raw = fetch_d1_from_mysql(target_date=target_date)
    created = 0
    for r in raw:
        numero = (r.get('numero_da_venda') or '').strip()
        if not numero:
            continue

        # Dedup check: já existe linha com mesma combinação?
        match = VendaD1.objects.filter(
            numero_da_venda=numero,
            cpf=(r.get('cpf') or '').strip(),
            produto=(r.get('produto') or '').strip(),
            data_da_venda=r.get('data_da_venda'),
            numero_acesso=(r.get('numero_acesso') or '').strip(),
            servicos=(r.get('servicos') or '').strip(),
        ).first()

        if match:
            # Cria a nova como duplicata da existente (acompanhamento).
            obj = VendaD1.objects.create(
                numero_da_venda=numero,
                produto=(r.get('produto') or '').strip(),
                valor=Decimal(str(r.get('valor') or 0)),
                cpf=(r.get('cpf') or '').strip(),
                numero_acesso=(r.get('numero_acesso') or '').strip(),
                data_da_venda=r.get('data_da_venda'),
                pilar=(r.get('pilar') or '').strip(),
                vendedor=(r.get('vendedor') or '').strip(),
                pdv=(r.get('pdv') or '').strip(),
                servicos=(r.get('servicos') or '').strip(),
                is_duplicate=True,
                duplicate_of=match,
            )
        else:
            VendaD1.objects.create(
                numero_da_venda=numero,
                produto=(r.get('produto') or '').strip(),
                valor=Decimal(str(r.get('valor') or 0)),
                cpf=(r.get('cpf') or '').strip(),
                numero_acesso=(r.get('numero_acesso') or '').strip(),
                data_da_venda=r.get('data_da_venda'),
                pilar=(r.get('pilar') or '').strip(),
                vendedor=(r.get('vendedor') or '').strip(),
                pdv=(r.get('pdv') or '').strip(),
                servicos=(r.get('servicos') or '').strip(),
            )
        created += 1

    expired = expire_deadlines()

    return {
        'created': created,
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
