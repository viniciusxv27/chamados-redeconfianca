"""Serviços do módulo Fibras: sync do MySQL e notificações."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.utils import timezone

from simulator.sql_realizado import _mysql_config

from .models import Fibra, FibraStatusHistory, PlanilhaOrdemInconsistente


User = get_user_model()


def _normalize(value) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return ' '.join(text.upper().split())


def _digits_only(value) -> str:
    """Retorna apenas os dígitos de um identificador (remove letras, '-', espaços)."""
    if value is None:
        return ''
    return re.sub(r'\D+', '', str(value))


def fetch_fibras_from_mysql(
    *, year: Optional[int] = None, month: Optional[int] = None,
) -> list[dict]:
    """Lê as vendas de Fibra do MySQL do mês corrente.

    Fibra = ``vendas_servicos`` com ``pilar = 'FIXA'``.
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
                    `ID_da_venda`,
                    `Nº_protocolo`,
                    `CPF_do_cliente`,
                    `Nome_do_cliente`,
                    `endereço_do_cliente`,
                    `Nº_acesso`,
                    `Plano_novo`,
                    COALESCE(`receita_calculada`, `Receita`, 0) AS valor,
                    `PDV`,
                    `Nome_do_vendedor`,
                    `data_da_venda`,
                    `pilar`,
                    `Serviço_Técnico`,
                    `Venda_ativa`
                FROM vendas_servicos
                WHERE YEAR(data_da_venda) = %s
                  AND MONTH(data_da_venda) = %s
                  AND UPPER(`pilar`) = 'FIXA'
                  AND `Serviço_Técnico` = 'Alta Banda Larga'
                """,
                (year, month),
            )
            cols = [
                'numero_da_venda', 'numero_protocolo', 'cpf', 'cliente', 'endereco', 'numero_acesso',
                'plano', 'valor', 'pdv', 'vendedor', 'data_da_venda',
                'pilar', 'servico_tecnico', 'venda_ativa',
            ]
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return rows


def fetch_fibras_by_protocols(protocols: Iterable[str]) -> list[dict]:
    """Busca no MySQL todas as vendas cujos ``Nº_protocolo`` estejam em ``protocols``.

    Não filtra por mês/pilar — usa só o protocolo para garantir o cruzamento
    direto com o que veio da planilha.
    """
    protocols = [p for p in {str(p).strip() for p in protocols} if p]
    if not protocols:
        return []

    try:
        import pymysql  # type: ignore
    except ImportError:
        return []

    try:
        conn = pymysql.connect(**_mysql_config())
    except Exception:
        return []

    rows: list[dict] = []
    cols = [
        'numero_da_venda', 'numero_protocolo', 'cpf', 'cliente', 'endereco', 'numero_acesso',
        'plano', 'valor', 'pdv', 'vendedor', 'data_da_venda',
        'pilar', 'servico_tecnico', 'venda_ativa',
    ]
    try:
        with conn.cursor() as cur:
            CHUNK = 500
            for i in range(0, len(protocols), CHUNK):
                chunk = protocols[i:i + CHUNK]
                placeholders = ','.join(['%s'] * len(chunk))
                cur.execute(
                    f"""
                    SELECT
                        `ID_da_venda`,
                        `Nº_protocolo`,
                        `CPF_do_cliente`,
                        `Nome_do_cliente`,
                        `endereço_do_cliente`,
                        `Nº_acesso`,
                        `Plano_novo`,
                        COALESCE(`receita_calculada`, `Receita`, 0) AS valor,
                        `PDV`,
                        `Nome_do_vendedor`,
                        `data_da_venda`,
                        `pilar`,
                        `Serviço_Técnico`,
                        `Venda_ativa`
                    FROM vendas_servicos
                    WHERE `Nº_protocolo` IN ({placeholders})
                    """,
                    chunk,
                )
                for row in cur.fetchall():
                    rows.append(dict(zip(cols, row)))
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return rows


def _campos_do_mysql(r: dict) -> dict:
    """Os campos da Fibra que vêm do Vivo GO (sem status: esse é da ilha)."""
    return {
        'numero_protocolo': (str(r.get('numero_protocolo') or '')).strip(),
        'cpf': (r.get('cpf') or '').strip() if r.get('cpf') else '',
        'cliente': (r.get('cliente') or '').strip() if r.get('cliente') else '',
        'endereco': (r.get('endereco') or '').strip() if r.get('endereco') else '',
        'numero_acesso': (str(r.get('numero_acesso') or '')).strip(),
        'plano': (r.get('plano') or '').strip() if r.get('plano') else '',
        'valor': Decimal(str(r.get('valor') or 0)),
        'pdv': (r.get('pdv') or '').strip() if r.get('pdv') else '',
        'vendedor': (r.get('vendedor') or '').strip() if r.get('vendedor') else '',
        'data_da_venda': r['data_da_venda'].date() if hasattr(r.get('data_da_venda'), 'hour') else r.get('data_da_venda'),
        'pilar': (r.get('pilar') or '').strip() if r.get('pilar') else '',
        'servico_tecnico': (r.get('servico_tecnico') or '').strip() if r.get('servico_tecnico') else '',
    }


def _upsert_fibra_from_mysql_row(r: dict) -> tuple[Optional[Fibra], bool]:
    """Cria ou atualiza uma Fibra a partir de um dict do MySQL.

    Retorna ``(fibra, created)``. Se o número da venda for vazio devolve
    ``(None, False)``.
    """
    numero = (str(r.get('numero_da_venda') or '')).strip()
    if not numero:
        return None, False

    venda_ativa = (str(r.get('venda_ativa') or '')).strip()
    cancelada = venda_ativa == '0'

    common = _campos_do_mysql(r)

    existing = Fibra.objects.filter(numero_da_venda=numero).first()
    if existing:
        for field, value in common.items():
            setattr(existing, field, value)
        if cancelada and existing.status != Fibra.STATUS_CANCELADO:
            existing.status = Fibra.STATUS_CANCELADO
        existing.save()
        return existing, False

    initial_status = Fibra.STATUS_CANCELADO if cancelada else Fibra.STATUS_AGENDADO
    fibra = Fibra.objects.create(
        numero_da_venda=numero,
        status=initial_status,
        **common,
    )
    return fibra, True


def sync_fibras(*, year: Optional[int] = None, month: Optional[int] = None) -> dict:
    """Atualiza/insere registros de Fibra a partir do MySQL.

    Para vendas já existentes localmente NÃO sobrescreve ``status`` nem
    ``retorno_myrella`` (Myrella já pode ter mexido). Exceção: se a venda
    foi cancelada no MySQL (``Venda_ativa='0'``), o status local vira
    ``STATUS_CANCELADO`` automaticamente.

    Retorna estatísticas (created, updated).
    """
    raw = fetch_fibras_from_mysql(year=year, month=month)
    # Em lote: uma consulta para os que já existem, bulk_create/bulk_update para o
    # resto. Linha a linha eram ~2 consultas por venda no banco remoto — 3 mil
    # vendas levavam 3 minutos e estouravam a requisição.
    linhas = {}
    for r in raw:
        numero = (str(r.get('numero_da_venda') or '')).strip()
        if numero:
            linhas[numero] = r
    existentes = {f.numero_da_venda: f for f in Fibra.objects.filter(numero_da_venda__in=list(linhas))}
    novas, alteradas = [], []
    campos = list(_campos_do_mysql({}).keys())
    for numero, r in linhas.items():
        comum = _campos_do_mysql(r)
        cancelada = (str(r.get('venda_ativa') or '')).strip() == '0'
        f = existentes.get(numero)
        if f is None:
            novas.append(Fibra(numero_da_venda=numero,
                               status=Fibra.STATUS_CANCELADO if cancelada else Fibra.STATUS_AGENDADO, **comum))
            continue
        mudou = False
        for campo, valor in comum.items():
            if getattr(f, campo) != valor:
                setattr(f, campo, valor)
                mudou = True
        if cancelada and f.status != Fibra.STATUS_CANCELADO:
            f.status = Fibra.STATUS_CANCELADO
            mudou = True
        if mudou:
            alteradas.append(f)
    Fibra.objects.bulk_create(novas, batch_size=500)
    if alteradas:
        Fibra.objects.bulk_update(alteradas, campos + ['status'], batch_size=500)
    if existentes:
        # O save() de antes carimbava last_synced_at em toda venda vista; mantém.
        Fibra.objects.filter(numero_da_venda__in=list(existentes)).update(last_synced_at=timezone.now())
    created, updated = len(novas), len(linhas) - len(novas)
    return {'created': created, 'updated': updated, 'total_in_source': len(raw)}


def meses_entre(ini: date, fim: date) -> list[tuple[int, int]]:
    meses, ano, mes = [], ini.year, ini.month
    while (ano, mes) <= (fim.year, fim.month):
        meses.append((ano, mes))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return meses


def sync_fibras_periodo(ini: Optional[date] = None, fim: Optional[date] = None) -> dict:
    """Sincroniza todos os meses do período (e o mês corrente).

    O sync_fibras() traz só um mês. O cruzamento usa o período inteiro da Esteira
    (ex.: 01/08 a 05/10): com só o mês corrente sincronizado, as vendas de agosto
    e setembro não existiam na Base e toda a Esteira desses meses aparecia como
    "venda não lançada".
    """
    hoje = timezone.localdate()
    ini, fim = ini or hoje, fim or hoje
    meses = meses_entre(min(ini, fim), max(ini, fim))
    if (hoje.year, hoje.month) not in meses:
        meses.append((hoje.year, hoje.month))
    total = {'created': 0, 'updated': 0, 'total_in_source': 0, 'meses': len(meses)}
    for ano, mes in meses[-24:]:  # trava de segurança: no máximo 2 anos por vez
        stats = sync_fibras(year=ano, month=mes)
        for k in ('created', 'updated', 'total_in_source'):
            total[k] += stats[k]
    return total


# ---------------------------------------------------------------------------
# Filtragem por usuário (consultor / coordenador / gerente)
# ---------------------------------------------------------------------------

def is_gerente(user) -> bool:
    """Gerente = hierarchy PADRAO e está no grupo de comunicação 'GERENTES'."""
    if getattr(user, 'hierarchy', '') != 'PADRAO':
        return False
    try:
        return user.communication_groups.filter(name__iexact='GERENTES').exists()
    except Exception:
        return False


def _user_loja_names(user) -> list[str]:
    """Nome(s) de loja do gerente, a partir de ``user.sector`` e ``user.sectors``."""
    names: list[str] = []
    sector = getattr(user, 'sector', None)
    if sector and getattr(sector, 'name', ''):
        names.append(sector.name.strip())
    try:
        for s in user.sectors.all():
            n = (getattr(s, 'name', '') or '').strip()
            if n and n not in names:
                names.append(n)
    except Exception:
        pass
    return names


def fibras_for_user(user) -> 'models.QuerySet[Fibra]':
    """Devolve um queryset das fibras visíveis para o usuário.

    - Padrão consultor: apenas as fibras em que ele é o vendedor.
    - Gerente (PADRAO + grupo GERENTES): todas as fibras do(s) seu(s) setor(es)
      (match por PDV contendo o nome da loja).
    - Coordenador/Admin/SuperAdmin: todas (filtros aplicados na view).
    """
    from django.db.models import Q

    qs = Fibra.objects.all()
    hierarchy = getattr(user, 'hierarchy', 'PADRAO')
    if hierarchy != 'PADRAO':
        return qs

    if is_gerente(user):
        lojas = _user_loja_names(user)
        if not lojas:
            return qs.none()
        cond = Q()
        for nome in lojas:
            cond |= Q(pdv__icontains=nome)
        return qs.filter(cond)

    nome = _normalize(user.get_full_name() or user.username)
    ids = [
        f.id for f in qs.only('id', 'vendedor')
        if _normalize(f.vendedor) == nome
    ]
    return qs.filter(id__in=ids)


# ---------------------------------------------------------------------------
# Importação da planilha diária
# ---------------------------------------------------------------------------

# Mapeia palavras-chave do texto bruto de status na planilha para o status
# interno do Fibra. A correspondência é por substring após normalização,
# avaliada na ordem declarada (a primeira que casar vence).
_PLANILHA_STATUS_MAP = (
    # Códigos numéricos da planilha "STATUS INSTALACAO"
    ('1.',          Fibra.STATUS_INSTALADO),   # 1.1 INSTALADA
    ('3.5',         Fibra.STATUS_AGENDADO),    # 3.5 AGENDADA
    ('3.8',         Fibra.STATUS_CANCELADO),   # 3.8 CANCELADA
    ('3.6',         Fibra.STATUS_PROBLEMA),    # 3.6 NENHUM REGISTRO ENCONTRADO NO WFM
    ('3.4',         Fibra.STATUS_PROBLEMA),    # 3.4 PENDENTE TECNICA
    ('3.1',         Fibra.STATUS_PENDENTE),    # 3.1 PENDENTE ENRIQUECIMENTO
    ('3.2',         Fibra.STATUS_PENDENTE),    # 3.2 PENDENTE AGENDAMENTO
    ('3.3',         Fibra.STATUS_PENDENTE),    # 3.3 PENDENTE RETENÇÃO
    ('2.',          Fibra.STATUS_CANCELADO),   # 2.x CANCELAMENTO
    # Fallback por palavras-chave
    ('INSTALAD',    Fibra.STATUS_INSTALADO),
    ('CONCLU',      Fibra.STATUS_INSTALADO),
    ('ATIVAD',      Fibra.STATUS_INSTALADO),
    ('AGENDAD',     Fibra.STATUS_AGENDADO),
    ('CANCELAD',    Fibra.STATUS_CANCELADO),
    ('FRAUDE',      Fibra.STATUS_CANCELADO),
    ('PENDENTE',    Fibra.STATUS_PENDENTE),
    ('AGUARDAN',    Fibra.STATUS_PENDENTE),
    ('NENHUM',      Fibra.STATUS_PROBLEMA),
    ('PROBLEM',     Fibra.STATUS_PROBLEMA),
    ('IMPRODUT',    Fibra.STATUS_PROBLEMA),
)


def _map_planilha_status(raw: str) -> Optional[str]:
    n = _normalize(raw)
    if not n:
        return None
    for needle, status in _PLANILHA_STATUS_MAP:
        if needle in n:
            return status
    return None


def import_planilha_xlsx(file_obj, *, by_user=None) -> dict:
    """Importa a Esteira de Vendas Vivo (ANALÍTICO DE VENDAS ou Base analítica – FIXA) e roda o cruzamento completo.

    1. Guarda a planilha (só as colunas do cruzamento) no lugar da anterior.
    2. Sincroniza as vendas de Fixa do MySQL (a Base de Vendas).
    3. Cruza Base de Vendas x Esteira com o motor da ferramenta "Validação de
       Vendas Go" (protocolo exato/aproximado, loja, data ±1, quantidade
       loja/dia, ajustes manuais) e grava o resultado em cada fibra; a ORDEM
       vinculada dita o status do kanban. Ver ``fibras/validacao.py``.
    4. Remove as fibras que sumiram da planilha e não tiveram tratativa.
    """
    from .validacao import executar, importar_esteira

    imp = importar_esteira(file_obj, nome=getattr(file_obj, 'name', ''), by_user=by_user)
    try:
        from .validacao import escopo_padrao
        escopo = escopo_padrao()
        sync_stats = sync_fibras_periodo(date.fromisoformat(escopo['ini']) - timedelta(days=1),
                                         date.fromisoformat(escopo['fim']) + timedelta(days=1))
    except Exception:
        sync_stats = {}
    inicio = timezone.now()
    antes = PlanilhaOrdemInconsistente.objects.count()
    cruz = executar(None, by_user=by_user, origem='importacao')
    k = cruz.kpis
    pruned = _prune_stale_fibras(import_ts=inicio)
    depois = PlanilhaOrdemInconsistente.objects.count()
    return {
        'formato': getattr(imp, 'formato', ''),
        'rows': imp.linhas,
        'matched': k.get('pedidos', 0) - k.get('nao_localizada', 0) - k.get('b2b', 0),
        'updated_status': FibraStatusHistory.objects.filter(alterado_em__gte=inicio,
                                                            retorno__startswith='Esteira Vivo:').count(),
        'created_from_mysql': int(sync_stats.get('created') or 0),
        'not_found': [],
        'not_found_count': k.get('nao_lancada', 0),
        'nao_localizadas': k.get('nao_localizada', 0),
        'kpis': k,
        'inconsistencias_novas': max(0, depois - antes),
        'inconsistencias_resolvidas': max(0, antes - depois),
        'removidas': pruned,
    }


def _prune_stale_fibras(*, import_ts) -> int:
    """Remove fibras criadas só pela planilha que sumiram da planilha atual.

    Critérios para apagar:
      - pilar vazio (fibra criada só via planilha — a do Vivo GO nunca é apagada);
      - já tinha sido vista em uma planilha antes (``last_planilha_at`` não nulo)
        e essa última visita é anterior à importação atual (``import_ts``).

    Critérios de PROTEÇÃO (NÃO apaga):
      - ``retorno_myrella`` preenchido (a ilha já interagiu);
      - existe ``FibraIncidente`` em status ``aberto`` ou ``em_tratativa``;
      - existe ``FibraStatusHistory`` com ``alterado_por`` não nulo
        (alteração manual de status feita por alguém);
      - existe ``FibraChatMessage`` (chat reverso teve mensagem).
    """
    from django.db.models import Q, Exists, OuterRef

    from .models import (
        FibraChatMessage, FibraIncidente, FibraStatusHistory,
    )

    # Só some o que nasceu da planilha (sem pilar). Venda que veio do Vivo GO
    # (pilar FIXA) existe de verdade: apagá-la porque não casou neste
    # cruzamento tirava da Base todo mês fora do período da Esteira importada
    # (jul–set/2026 sumiram assim) e a divergência explodia.
    base = Fibra.objects.filter(
        last_planilha_at__isnull=False,
        last_planilha_at__lt=import_ts,
    ).filter(pilar='')

    if not base.exists():
        return 0

    tratativa = FibraIncidente.objects.filter(
        fibra_id=OuterRef('pk'),
        status__in=[FibraIncidente.STATUS_ABERTO, FibraIncidente.STATUS_EM_TRATATIVA],
    )
    historico_manual = FibraStatusHistory.objects.filter(
        fibra_id=OuterRef('pk'),
        alterado_por__isnull=False,
    )
    chat_msg = FibraChatMessage.objects.filter(chat__fibra_id=OuterRef('pk'))

    candidatas = (
        base.annotate(
            _tratativa=Exists(tratativa),
            _hist=Exists(historico_manual),
            _chat=Exists(chat_msg),
        )
        .exclude(_tratativa=True)
        .exclude(_hist=True)
        .exclude(_chat=True)
        .exclude(retorno_myrella__gt='')
    )

    deleted, _detail = candidatas.delete()
    return int(deleted)


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
