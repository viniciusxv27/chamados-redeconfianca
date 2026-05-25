"""Serviços do módulo Fibras: sync do MySQL e notificações."""
from __future__ import annotations

import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.utils import timezone

from simulator.sql_realizado import _mysql_config

from .models import Fibra, FibraStatusHistory


User = get_user_model()


def _normalize(value) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return ' '.join(text.upper().split())


def fetch_fibras_from_mysql(
    *, year: Optional[int] = None, month: Optional[int] = None,
) -> list[dict]:
    """Lê as vendas de Fibra do MySQL do mês corrente.

    Considera Pilar=FIXA E Serviço_Técnico em ('Alta Banda Larga', 'Alta TV').
    """
    try:
        import pymysql  # type: ignore
    except ImportError:
        return []

    now = timezone.now()
    year = year or now.year
    month = month or now.month

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
                    `CPF`,
                    `Nome_do_cliente`,
                    `Endereço`,
                    `Numero_de_Acesso`,
                    `Plano`,
                    COALESCE(`receita_calculada`, `Receita`, 0) AS valor,
                    `PDV`,
                    `Nome_do_vendedor`,
                    `data_da_venda`,
                    `pilar`,
                    `Serviço_Técnico`
                FROM vendas_servicos
                WHERE YEAR(data_da_venda) = %s
                  AND MONTH(data_da_venda) = %s
                  AND UPPER(`Serviço_Técnico`) IN ('ALTA BANDA LARGA', 'ALTA TV')
                """,
                (year, month),
            )
            cols = [
                'numero_da_venda', 'cpf', 'cliente', 'endereco', 'numero_acesso',
                'plano', 'valor', 'pdv', 'vendedor', 'data_da_venda',
                'pilar', 'servico_tecnico',
            ]
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return rows


def sync_fibras(*, year: Optional[int] = None, month: Optional[int] = None) -> dict:
    """Atualiza/insere registros de Fibra a partir do MySQL.

    Não sobrescreve `status` nem `retorno_myrella` para vendas já existentes.
    Retorna estatísticas (created, updated).
    """
    raw = fetch_fibras_from_mysql(year=year, month=month)
    created = 0
    updated = 0
    for r in raw:
        numero = (r.get('numero_da_venda') or '').strip()
        if not numero:
            continue
        defaults = {
            'cpf': (r.get('cpf') or '').strip(),
            'cliente': (r.get('cliente') or '').strip(),
            'endereco': (r.get('endereco') or '').strip(),
            'numero_acesso': (r.get('numero_acesso') or '').strip(),
            'plano': (r.get('plano') or '').strip(),
            'valor': Decimal(str(r.get('valor') or 0)),
            'pdv': (r.get('pdv') or '').strip(),
            'vendedor': (r.get('vendedor') or '').strip(),
            'data_da_venda': r.get('data_da_venda'),
            'pilar': (r.get('pilar') or '').strip(),
            'servico_tecnico': (r.get('servico_tecnico') or '').strip(),
        }
        obj, was_created = Fibra.objects.update_or_create(
            numero_da_venda=numero, defaults=defaults,
        )
        if was_created:
            created += 1
        else:
            updated += 1
    return {'created': created, 'updated': updated, 'total_in_source': len(raw)}


# ---------------------------------------------------------------------------
# Filtragem por usuário (consultor / coordenador / gerente)
# ---------------------------------------------------------------------------

def fibras_for_user(user) -> 'models.QuerySet[Fibra]':
    """Devolve um queryset das fibras visíveis para o usuário.

    - Padrão (consultor): apenas as fibras em que ele é o vendedor.
    - Gerente/Coordenador/Admin: todas (filtros aplicados na view).
    """
    qs = Fibra.objects.all()
    hierarchy = getattr(user, 'hierarchy', 'PADRAO')
    if hierarchy == 'PADRAO':
        nome = _normalize(user.get_full_name() or user.username)
        # comparação case-insensitive contra `vendedor`
        # (usa filtro Python como fallback se MySQL/SQLite divergem em UPPER)
        ids = [
            f.id for f in qs.only('id', 'vendedor')
            if _normalize(f.vendedor) == nome
        ]
        return qs.filter(id__in=ids)
    return qs


# ---------------------------------------------------------------------------
# Mudança de status + notificação ao vendedor
# ---------------------------------------------------------------------------

def change_status(fibra: Fibra, new_status: str, *, changed_by, retorno: str = '') -> None:
    if new_status not in dict(Fibra.STATUS_CHOICES):
        raise ValueError(f"Status inválido: {new_status}")
    old = fibra.status
    fibra.status = new_status
    if retorno:
        fibra.retorno_myrella = retorno
    fibra.save(update_fields=['status', 'retorno_myrella', 'last_synced_at'])

    FibraStatusHistory.objects.create(
        fibra=fibra,
        status_anterior=old,
        status_novo=new_status,
        retorno=retorno,
        alterado_por=changed_by,
    )

    _notify_vendor(fibra, old, new_status)


def _notify_vendor(fibra: Fibra, old_status: str, new_status: str) -> None:
    """Notifica o vendedor (se cadastrado no portal) sobre a mudança de status."""
    if old_status == new_status:
        return
    vendor_user = _find_vendor_user(fibra.vendedor)
    if not vendor_user:
        return

    labels = dict(Fibra.STATUS_CHOICES)
    msg_map = {
        Fibra.STATUS_INSTALADO: 'foi instalada',
        Fibra.STATUS_AGENDADO: 'foi reagendada',
        Fibra.STATUS_CANCELADO: 'foi cancelada',
        Fibra.STATUS_PENDENTE: 'voltou para pendente',
        Fibra.STATUS_PROBLEMA: 'foi sinalizada com problema',
    }
    acao = msg_map.get(new_status, f'mudou para {labels.get(new_status, new_status)}')
    title = f"Fibra {labels.get(new_status, new_status)}"
    message = (
        f"Sua fibra para o cliente {fibra.cliente or '—'}, "
        f"venda {fibra.numero_da_venda}, valor R$ {fibra.valor} {acao}."
    )

    try:
        from core.notifications import NotificationMixin  # type: ignore
        NotificationMixin.create_notification(
            user=vendor_user,
            title=title,
            message=message,
            notification_type='fibra_status',
            related_object_id=fibra.id,
            related_url=f"/fibras/{fibra.id}/",
        )
    except Exception:
        # Fallback: não bloqueia a operação se notificações estiverem indisponíveis.
        pass


def _find_vendor_user(vendedor_nome: str):
    if not vendedor_nome:
        return None
    target = _normalize(vendedor_nome)
    for u in User.objects.filter(is_active=True).only('id', 'first_name', 'last_name', 'username'):
        full = _normalize(f"{u.first_name} {u.last_name}".strip() or u.username)
        if full == target:
            return u
    return None
