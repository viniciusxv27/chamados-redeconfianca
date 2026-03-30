import io
import csv
import json
import unicodedata
import datetime
from decimal import Decimal, InvalidOperation
import pandas as pd
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.cache import cache
from django.http import JsonResponse, HttpResponse
from django.db import models
from django.db.models import Count, Min, Q, Sum
from django.core.paginator import Paginator
from django.utils import timezone

from .models import ExclusionRecord, Contestation, ContestationHistory, ContestationCartDraft
from users.models import SystemConfig, User


HIERARCHY_RANK = {
    'PADRAO': 0,
    'ADMINISTRATIVO': 1,
    'SUPERVISOR': 2,
    'ADMIN': 3,
    'SUPERADMIN': 4,
}

QUALITY_ISLAND_SECTOR_ID = 8

DEFAULT_EXCEL_BASE_EXCLUSAO_URL = "https://1drv.ms/x/c/871ee1819c7e2faa/IQBryBteOg4sS4cBwU1tIgKoATfi6qmYB8eRrIaTpyP8Qhc?e=pye3Sj"
SYNC_VALIDITY_DAYS = 3
SECTOR_PENDING_DEADLINE_DAYS = 1


def _can_manage_contestations(user):
    rank = HIERARCHY_RANK.get(user.hierarchy, 0)
    if rank >= HIERARCHY_RANK['ADMIN']:
        return True
    return _has_global_contestation_access(user)


def _has_global_contestation_access(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        config = SystemConfig.get_config()
        return config.contestacao_global_managers.filter(pk=user.pk).exists()
    except Exception:
        return False


def _can_view_all_contestation_scope(user):
    rank = HIERARCHY_RANK.get(user.hierarchy, 0)
    return rank >= HIERARCHY_RANK['SUPERADMIN'] or _has_global_contestation_access(user)


def _can_assign_global_contestation_managers(user):
    rank = HIERARCHY_RANK.get(user.hierarchy, 0)
    if rank >= HIERARCHY_RANK['SUPERADMIN']:
        return True
    if not user or not user.is_authenticated:
        return False

    # Liberação adicional para o setor Ilha de Qualidade (ID 8).
    if user.sector_id == QUALITY_ISLAND_SECTOR_ID:
        return True
    return user.sectors.filter(pk=QUALITY_ISLAND_SECTOR_ID).exists()


def _can_create_contestations(user):
    return user.can_create_contestations()


def _can_access_contestation_module(user):
    if not user or not user.is_authenticated:
        return False
    if _can_create_contestations(user):
        return True
    if user.hierarchy in ['ADMINISTRATIVO', 'ADMIN', 'SUPERADMIN']:
        return True
    if _has_global_contestation_access(user):
        return True
    if user.sector_id == QUALITY_ISLAND_SECTOR_ID:
        return True
    return user.sectors.filter(pk=QUALITY_ISLAND_SECTOR_ID).exists()


def _open_contestation_filter():
    return Q(status__in=['pending', 'accepted']) | Q(status='confirmed', payment_status='pending_payment')


def _normalize_sector_name(sector_name):
    """Remove 'LOJA' e normaliza o nome do setor para comparação com filial."""
    if not sector_name:
        return ''
    normalized = sector_name.strip().upper()
    # Remove variações de "LOJA" do início
    for prefix in ['LOJA ', 'LOJA_', 'LOJA-']:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
    return normalized.strip()


def _match_sector_to_filial(user_sectors, filial):
    """Verifica se a filial_name bate com algum setor do usuário."""
    if not filial or not user_sectors:
        return False
    
    filial_upper = filial.strip().upper()
    for sector in user_sectors:
        sector_norm = _normalize_sector_name(sector)
        # Tenta diferentes formas de comparação
        if sector_norm and (
            filial_upper == sector_norm or
            filial_upper.endswith(sector_norm) or
            sector_norm in filial_upper or
            filial_upper in sector_norm
        ):
            return True
    return False


def _normalize_person_name(value):
    if not value:
        return ''
    normalized = unicodedata.normalize('NFKD', str(value))
    ascii_only = ''.join(ch for ch in normalized if not unicodedata.combining(ch))
    return ' '.join(ascii_only.upper().strip().split())


def _build_user_name_candidates(user):
    candidates = []
    full_name = getattr(user, 'full_name', '')
    if full_name:
        candidates.append(full_name)
    composed = f'{user.first_name} {user.last_name}'.strip()
    if composed:
        candidates.append(composed)
    if getattr(user, 'username', ''):
        candidates.append(user.username)
    return {_normalize_person_name(name) for name in candidates if _normalize_person_name(name)}


def _format_sale_date_value(raw_value):
    """Normaliza DATA da planilha para exibição consistente na aplicação."""
    if raw_value is None:
        return ''

    if isinstance(raw_value, float) and pd.isna(raw_value):
        return ''

    if isinstance(raw_value, pd.Timestamp):
        parsed_dt = raw_value.to_pydatetime()
    elif isinstance(raw_value, datetime.datetime):
        parsed_dt = raw_value
    elif isinstance(raw_value, datetime.date):
        parsed_dt = datetime.datetime.combine(raw_value, datetime.time.min)
    else:
        raw_str = str(raw_value).strip()
        if not raw_str or raw_str.lower() == 'nan':
            return ''

        parsed_series = pd.to_datetime([raw_str], errors='coerce', dayfirst=True)
        parsed = parsed_series[0] if len(parsed_series) else pd.NaT
        if pd.isna(parsed):
            return raw_str
        parsed_dt = parsed.to_pydatetime()

    has_time = parsed_dt.time() != datetime.time.min
    if has_time:
        return parsed_dt.strftime('%d/%m/%Y %H:%M')
    return parsed_dt.strftime('%d/%m/%Y')


def _has_open_contestation_for_sector(filial):
    if not filial:
        return False
    return Contestation.objects.filter(exclusion__filial__iexact=filial).filter(_open_contestation_filter()).exists()


def _apply_sector_visibility_filter(contestation_qs, user):
    """Aplica visibilidade por setor para usuários abaixo de SUPERADMIN."""
    rank = HIERARCHY_RANK.get(user.hierarchy, 0)
    if rank >= HIERARCHY_RANK['SUPERADMIN'] or _has_global_contestation_access(user):
        return contestation_qs

    user_sectors = list(user.sectors.values_list('name', flat=True))
    if user.sector:
        user_sectors.append(user.sector.name)

    if not user_sectors:
        return contestation_qs.none()

    matching_ids = []
    for record in contestation_qs:
        if _match_sector_to_filial(user_sectors, record.exclusion.filial):
            matching_ids.append(record.id)
    return contestation_qs.filter(id__in=matching_ids) if matching_ids else contestation_qs.none()


def _apply_exclusion_visibility_filter(exclusion_qs, user):
    """Aplica visibilidade por setor para registros de exclusão."""
    rank = HIERARCHY_RANK.get(user.hierarchy, 0)
    if rank >= HIERARCHY_RANK['SUPERADMIN'] or _has_global_contestation_access(user):
        return exclusion_qs

    user_sectors = list(user.sectors.values_list('name', flat=True))
    if user.sector:
        user_sectors.append(user.sector.name)

    if not user_sectors:
        return exclusion_qs.none()

    matching_ids = []
    for record in exclusion_qs:
        if _match_sector_to_filial(user_sectors, record.filial):
            matching_ids.append(record.id)
    return exclusion_qs.filter(id__in=matching_ids) if matching_ids else exclusion_qs.none()


def _apply_exclusion_scope_for_user(exclusion_qs, user):
    """Aplica o mesmo escopo de visibilidade usado na exclusion_list."""
    can_view_all_scope = _can_view_all_contestation_scope(user)
    if can_view_all_scope:
        return exclusion_qs

    if user.hierarchy == 'PADRAO':
        user_name_candidates = _build_user_name_candidates(user)
        if not user_name_candidates:
            return exclusion_qs.none()

        matching_ids = []
        for record in exclusion_qs:
            gerente_name = _normalize_person_name(record.gerente)
            if gerente_name and gerente_name in user_name_candidates:
                matching_ids.append(record.id)
        return exclusion_qs.filter(id__in=matching_ids) if matching_ids else exclusion_qs.none()

    user_sectors = list(user.sectors.values_list('name', flat=True))
    if user.sector:
        user_sectors.append(user.sector.name)

    if not user_sectors:
        return exclusion_qs.none()

    matching_ids = []
    for record in exclusion_qs:
        if _match_sector_to_filial(user_sectors, record.filial):
            matching_ids.append(record.id)
    return exclusion_qs.filter(id__in=matching_ids) if matching_ids else exclusion_qs.none()


def _approval_mode_label(mode):
    if mode == 'approved_and_contested':
        return 'Aprovar e Contestar'
    if mode == 'approved':
        return 'Aprovar'
    return 'N/A'


def _status_label(status):
    mapping = {
        'pending': 'Pendente',
        'accepted': 'Aprovada',
        'rejected': 'Rejeitada',
        'confirmed': 'Confirmada',
        'denied': 'Negada',
    }
    return mapping.get(status, status or '-')


def _payment_status_label(status):
    mapping = {
        'not_applicable': 'N/A',
        'pending_payment': 'Aguardando Pagamento',
        'paid': 'Pago',
    }
    return mapping.get(status, status or '-')


def _serialize_cart_draft_item(draft):
    exclusion = draft.exclusion
    return {
        'exclusion_id': exclusion.pk,
        'reason': draft.reason or '',
        'has_attachment': bool(draft.attachment),
        'attachment_name': draft.attachment.name.split('/')[-1] if draft.attachment else '',
        'summary': {
            'vendedor': exclusion.vendedor or '-',
            'filial': exclusion.filial or '-',
            'valor': f'R$ {exclusion.receita:.2f}',
            'produto': exclusion.plano_produto or '-',
        },
        'updated_at': draft.updated_at.isoformat() if draft.updated_at else None,
    }


def _parse_currency_to_decimal(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace('R$', '').replace(' ', '')
    text = text.replace('.', '').replace(',', '.')
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _resolve_exclusion_for_draft_item(user, exclusion_id=None, summary=None):
    """Tenta resolver um item local para uma venda existente no escopo do usuário."""
    scoped_qs = _apply_exclusion_scope_for_user(ExclusionRecord.objects.all(), user)

    if exclusion_id:
        try:
            exclusion_id_int = int(exclusion_id)
        except (TypeError, ValueError):
            exclusion_id_int = None
        if exclusion_id_int:
            by_id = scoped_qs.filter(pk=exclusion_id_int).first()
            if by_id:
                return by_id

    summary = summary or {}
    vendedor = (summary.get('vendedor') or '').strip()
    filial = (summary.get('filial') or '').strip()
    produto = (summary.get('produto') or '').strip()
    valor = _parse_currency_to_decimal(summary.get('valor'))

    if not vendedor and not filial and not produto and valor is None:
        return None

    candidates = scoped_qs
    if vendedor and vendedor != '-':
        candidates = candidates.filter(vendedor__iexact=vendedor)
    if filial and filial != '-':
        candidates = candidates.filter(filial__iexact=filial)
    if produto and produto != '-':
        candidates = candidates.filter(plano_produto__iexact=produto)
    if valor is not None:
        candidates = candidates.filter(receita=valor)

    resolved = candidates.order_by('-imported_at', '-id').first()
    if resolved:
        return resolved

    # Fallback mais tolerante para produto com pequenas variacoes de escrita.
    candidates = scoped_qs
    if vendedor and vendedor != '-':
        candidates = candidates.filter(vendedor__iexact=vendedor)
    if filial and filial != '-':
        candidates = candidates.filter(filial__iexact=filial)
    if produto and produto != '-':
        candidates = candidates.filter(plano_produto__icontains=produto[:40])
    if valor is not None:
        candidates = candidates.filter(receita=valor)

    return candidates.order_by('-imported_at', '-id').first()



def _format_avg_duration(total_seconds, count):
    if count <= 0:
        return '0h'
    avg_seconds = total_seconds / count
    avg_hours = int(avg_seconds // 3600)
    return f'{avg_hours}h'


def _format_remaining_duration(total_seconds):
    total_seconds = max(int(total_seconds), 0)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    if days > 0:
        return f'{days}d {hours}h {minutes}m'
    if hours > 0:
        return f'{hours}h {minutes}m'
    return f'{minutes}m'


def _get_last_sync_at():
    last_sync = ContestationHistory.objects.filter(action='synced').only('created_at').order_by('-created_at').first()
    return last_sync.created_at if last_sync else None


def _get_sync_window_state():
    now = timezone.now()
    last_sync_at = _get_last_sync_at()
    if not last_sync_at:
        return {
            'has_sync': False,
            'is_blocked': True,
            'is_expired': True,
            'last_sync_at': None,
            'sync_deadline_at': None,
            'remaining_seconds': 0,
            'remaining_label': 'Expirado',
        }

    sync_deadline_at = last_sync_at + timezone.timedelta(days=SYNC_VALIDITY_DAYS)
    remaining_seconds = int((sync_deadline_at - now).total_seconds())
    is_expired = remaining_seconds <= 0
    return {
        'has_sync': True,
        'is_blocked': is_expired,
        'is_expired': is_expired,
        'last_sync_at': last_sync_at,
        'sync_deadline_at': sync_deadline_at,
        'remaining_seconds': max(remaining_seconds, 0),
        'remaining_label': _format_remaining_duration(remaining_seconds) if remaining_seconds > 0 else 'Expirado',
    }


def _get_excel_download_url(share_url):
    """Converte URL de compartilhamento OneDrive em URL de download."""
    if 'download=1' in share_url:
        return share_url
    if '?' in share_url:
        return share_url + '&download=1'
    return share_url + '?download=1'


def _download_exclusion_spreadsheet():
    """Baixa a planilha BASE_EXCLUSAO e retorna um DataFrame."""
    try:
        config = SystemConfig.get_config()
        url = (
            config.excel_contestacao_base_exclusao_url
            or config.excel_base_exclusao_url
            or DEFAULT_EXCEL_BASE_EXCLUSAO_URL
        )
    except Exception:
        url = DEFAULT_EXCEL_BASE_EXCLUSAO_URL

    cache_key = 'contestacao_base_exclusao_content'
    cached = cache.get(cache_key)
    if cached:
        return pd.read_excel(io.BytesIO(cached), sheet_name='Planilha1', engine='openpyxl'), None

    download_url = _get_excel_download_url(url)
    urls_to_try = [download_url]
    if '?' in url:
        urls_to_try.append(url + '&download=1')
    else:
        urls_to_try.append(url + '?download=1')

    import re
    match = re.search(r'(IQ[A-Za-z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        urls_to_try.append(f"https://onedrive.live.com/download.aspx?resid={file_id}")

    response = None
    last_error = None
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    for try_url in urls_to_try:
        try:
            response = requests.get(try_url, timeout=30, headers=headers, allow_redirects=True)
            if response.status_code == 200:
                ct = response.headers.get('Content-Type', '')
                if 'excel' in ct or 'spreadsheet' in ct or 'octet-stream' in ct or len(response.content) > 1000:
                    break
                else:
                    last_error = f'Conteúdo não é Excel: {ct}'
                    response = None
            else:
                last_error = f'HTTP {response.status_code}'
                response = None
        except Exception as e:
            last_error = str(e)
            continue

    if response is None or response.status_code != 200:
        return None, f'Erro ao baixar planilha: {last_error}'

    cache.set(cache_key, response.content, 300)

    try:
        df = pd.read_excel(io.BytesIO(response.content), sheet_name='Planilha1', engine='openpyxl')
        return df, None
    except Exception as e:
        return None, f'Erro ao ler planilha: {str(e)}'


def _find_column(df, name):
    """Encontra uma coluna pelo nome (case-insensitive)."""
    for col in df.columns:
        if str(col).strip().upper() == name.upper():
            return col
    return None


@login_required
def sync_exclusions(request):
    """Sincroniza registros da planilha BASE_EXCLUSAO para o banco de dados."""
    # Apenas ADMIN+ podem sincronizar
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para sincronizar.')
        return redirect('contestacao:exclusion_list')

    if request.method != 'POST':
        return redirect('contestacao:exclusion_list')

    df, error = _download_exclusion_spreadsheet()
    if error or df is None:
        messages.error(request, f'Erro ao baixar planilha: {error}')
        return redirect('contestacao:exclusion_list')

    filial_col = _find_column(df, 'FILIAL')
    vendedor_col = _find_column(df, 'VENDEDOR')
    receita_col = _find_column(df, 'RECEITA')
    pilar_col = _find_column(df, 'PILAR')
    gerente_col = _find_column(df, 'GERENTE')
    coord_col = _find_column(df, 'COORDENACAO') or _find_column(df, 'COORDENAÇÃO') or _find_column(df, 'COORDENADOR')
    nvenda_col = _find_column(df, 'Nº DA VENDA') or _find_column(df, 'N DA VENDA') or _find_column(df, 'NUMERO_VENDA')
    data_col = _find_column(df, 'DATA')
    cliente_col = _find_column(df, 'NOME CLIENTE') or _find_column(df, 'CLIENTE')
    cpf_col = _find_column(df, 'CPF/CNPJ') or _find_column(df, 'CPF')
    plano_col = _find_column(df, 'PLANO/PRODUTO') or _find_column(df, 'PLANO') or _find_column(df, 'PRODUTO')
    imei_col = _find_column(df, 'IMEI')
    acesso_col = _find_column(df, 'NUMERO ACESSO') or _find_column(df, 'NUMERO_ACESSO')
    obs_col = _find_column(df, 'OBSERVAÇÃO') or _find_column(df, 'OBSERVACAO') or _find_column(df, 'OBS')

    if not filial_col or not vendedor_col or not receita_col or not pilar_col:
        messages.error(request, 'Colunas obrigatórias não encontradas na planilha (FILIAL, VENDEDOR, RECEITA, PILAR).')
        return redirect('contestacao:exclusion_list')

    # Apagar apenas registros SEM contestações ativas — nunca apagar os que têm contestação
    contested_ids = set(
        Contestation.objects.values_list('exclusion_id', flat=True)
    )
    ExclusionRecord.objects.exclude(pk__in=contested_ids).delete()

    records = []
    for _, row in df.iterrows():
        vendedor_val = str(row.get(vendedor_col, '')).strip()
        if not vendedor_val or vendedor_val == 'nan':
            continue
        receita_val = row.get(receita_col, 0)
        try:
            receita_val = float(receita_val) if pd.notna(receita_val) else 0
        except (ValueError, TypeError):
            receita_val = 0

        records.append(ExclusionRecord(
            filial=str(row.get(filial_col, '')).strip(),
            vendedor=vendedor_val,
            receita=receita_val,
            pilar=str(row.get(pilar_col, '')).strip(),
            gerente=str(row.get(gerente_col, '')).strip() if gerente_col else '',
            coordenacao=str(row.get(coord_col, '')).strip() if coord_col else '',
            numero_venda=str(row.get(nvenda_col, '')).strip() if nvenda_col else '',
            data_venda=_format_sale_date_value(row.get(data_col, '')) if data_col else '',
            nome_cliente=str(row.get(cliente_col, '')).strip() if cliente_col else '',
            cpf_cnpj=str(row.get(cpf_col, '')).strip() if cpf_col else '',
            plano_produto=str(row.get(plano_col, '')).strip() if plano_col else '',
            imei=str(row.get(imei_col, '')).strip() if imei_col else '',
            numero_acesso=str(row.get(acesso_col, '')).strip() if acesso_col else '',
            observacao=str(row.get(obs_col, '')).strip() if obs_col else '',
        ))

    ExclusionRecord.objects.bulk_create(records, batch_size=500)
    ContestationHistory.objects.create(
        action='synced',
        user=request.user,
        notes=f'{len(records)} registros importados',
    )
    messages.success(request, f'{len(records)} registros de exclusão importados com sucesso!')
    return redirect('contestacao:exclusion_list')


@login_required
def exclusion_list(request):
    """Lista os registros de exclusão — filtrados por filial/setor do usuário."""
    if not _can_access_contestation_module(request.user):
        messages.error(request, 'Sem permissão para acessar contestações.')
        return redirect('home')

    qs = ExclusionRecord.objects.all()

    # Superadmin vê tudo; outros filtram por setor
    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    can_view_all_scope = _can_view_all_contestation_scope(request.user)
    qs = _apply_exclusion_scope_for_user(qs, request.user)

    # Filtros da query string
    search = request.GET.get('q', '').strip()
    pilar = request.GET.get('pilar', '').strip()
    filial_filter = request.GET.get('filial', '').strip()
    if search:
        qs = qs.filter(Q(vendedor__icontains=search) | Q(nome_cliente__icontains=search) | Q(cpf_cnpj__icontains=search))
    if pilar:
        qs = qs.filter(pilar__iexact=pilar)
    if filial_filter:
        qs = qs.filter(filial__iexact=filial_filter)

    # Pilares e filiais filtrados pelos setores acessíveis do usuário (para a UI)
    pilares = qs.values_list('pilar', flat=True).distinct().order_by('pilar')
    filiais = qs.values_list('filial', flat=True).distinct().order_by('filial')

    # IDs que já têm contestação ativa com seus status (rejeitadas ficam livres para novo envio)
    contestations_map = {}
    for c in Contestation.objects.filter(
        _open_contestation_filter()
    ).values('exclusion_id', 'status', 'pk'):
        contestations_map[c['exclusion_id']] = {'status': c['status'], 'pk': c['pk']}

    locked_sectors = set(
        Contestation.objects.filter(_open_contestation_filter())
        .values_list('exclusion__filial', flat=True)
    )
    locked_sectors = {str(sector).strip().upper() for sector in locked_sectors if sector}
    
    # Keep backwards compatibility
    contested_ids = set(contestations_map.keys())

    total_records = qs.count()
    total_receita = qs.aggregate(total=Sum('receita'))['total'] or 0
    sync_state = _get_sync_window_state()

    paginator = Paginator(qs, 50)
    page_number = request.GET.get('page')
    records_page = paginator.get_page(page_number)

    context = {
        'records': records_page,
        'page_obj': records_page,
        'pilares': pilares,
        'filiais': filiais,
        'search': search,
        'pilar_filter': pilar,
        'filial_filter': filial_filter,
        'total_records': total_records,
        'total_receita': total_receita,
        'contested_ids': contested_ids,
        'contestations_map': contestations_map,
        'locked_sectors': locked_sectors,
        'can_manage': _can_manage_contestations(request.user),
        'can_sync': _can_manage_contestations(request.user),  # Apenas ADMIN+ podem sincronizar
        'can_dashboard': _can_manage_contestations(request.user),  # Apenas ADMIN+ podem acessar dashboard
        'is_superadmin': can_view_all_scope,
        'contestation_blocked': sync_state['is_blocked'],
        'sync_has_data': sync_state['has_sync'],
        'sync_is_expired': sync_state['is_expired'],
        'sync_last_at': sync_state['last_sync_at'],
        'sync_deadline_at': sync_state['sync_deadline_at'],
        'sync_remaining_seconds': sync_state['remaining_seconds'],
        'sync_remaining_label': sync_state['remaining_label'],
    }
    return render(request, 'contestacao/exclusion_list.html', context)


@login_required
def contestation_cart_items_summary(request):
    """Retorna dados resumidos para recuperar itens do carrinho por IDs."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido.'}, status=405)

    if not _can_access_contestation_module(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissão.'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        payload = {}

    raw_ids = payload.get('ids') or []
    valid_ids = []
    for raw in raw_ids:
        try:
            valid_ids.append(int(raw))
        except (TypeError, ValueError):
            continue

    if not valid_ids:
        return JsonResponse({'success': True, 'items': {}})

    # Recuperação por ID para reparar carrinhos antigos/inconsistentes,
    # independente de filtros atuais da listagem.
    scoped_qs = ExclusionRecord.objects.filter(pk__in=valid_ids)
    items = {}
    for r in scoped_qs:
        items[str(r.pk)] = {
            'vendedor': r.vendedor or '-',
            'filial': r.filial or '-',
            'valor': f'R$ {r.receita:.2f}',
            'produto': r.plano_produto or '-',
        }

    found_ids = {int(k) for k in items.keys()}
    missing_ids = [rid for rid in valid_ids if rid not in found_ids]

    return JsonResponse({'success': True, 'items': items, 'missing_ids': missing_ids})


@login_required
def cart_draft_list(request):
    """Lista os itens de carrinho salvos no servidor para o usuario atual."""
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Metodo nao permitido.'}, status=405)

    if not _can_access_contestation_module(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissao.'}, status=403)

    drafts = ContestationCartDraft.objects.select_related('exclusion').filter(user=request.user).order_by('-updated_at')

    scoped_exclusions = _apply_exclusion_scope_for_user(
        ExclusionRecord.objects.filter(pk__in=drafts.values_list('exclusion_id', flat=True)),
        request.user,
    )
    allowed_ids = set(scoped_exclusions.values_list('pk', flat=True))

    items = []
    for draft in drafts:
        if draft.exclusion_id in allowed_ids:
            items.append(_serialize_cart_draft_item(draft))

    return JsonResponse({'success': True, 'items': items})


@login_required
def cart_draft_upsert(request):
    """Cria ou atualiza um item do carrinho no servidor (motivo + anexo)."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Metodo nao permitido.'}, status=405)

    if not _can_access_contestation_module(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissao.'}, status=403)

    exclusion_id_raw = request.POST.get('exclusion_id')
    reason = (request.POST.get('reason') or '').strip()
    attachment = request.FILES.get('attachment')

    try:
        exclusion_id = int(exclusion_id_raw)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Registro invalido.'}, status=400)

    scoped_exclusions = _apply_exclusion_scope_for_user(
        ExclusionRecord.objects.filter(pk=exclusion_id),
        request.user,
    )
    exclusion = scoped_exclusions.first()
    if not exclusion:
        return JsonResponse({'success': False, 'error': 'Registro nao encontrado no seu escopo.'}, status=404)

    draft, _ = ContestationCartDraft.objects.get_or_create(
        user=request.user,
        exclusion=exclusion,
        defaults={'reason': reason},
    )

    # Atualiza somente quando o cliente enviar novos dados.
    if reason:
        draft.reason = reason
    if attachment:
        draft.attachment = attachment
    draft.save()

    return JsonResponse({'success': True, 'item': _serialize_cart_draft_item(draft)})


@login_required
def cart_draft_delete(request):
    """Remove um item especifico do carrinho salvo no servidor."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Metodo nao permitido.'}, status=405)

    if not _can_access_contestation_module(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissao.'}, status=403)

    exclusion_id_raw = request.POST.get('exclusion_id')
    try:
        exclusion_id = int(exclusion_id_raw)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Registro invalido.'}, status=400)

    deleted, _ = ContestationCartDraft.objects.filter(user=request.user, exclusion_id=exclusion_id).delete()
    return JsonResponse({'success': True, 'deleted': deleted > 0})


@login_required
def cart_draft_clear(request):
    """Limpa todos os itens do carrinho salvo no servidor para o usuario atual."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Metodo nao permitido.'}, status=405)

    if not _can_access_contestation_module(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissao.'}, status=403)

    deleted, _ = ContestationCartDraft.objects.filter(user=request.user).delete()
    return JsonResponse({'success': True, 'deleted': deleted})


@login_required
def cart_draft_sync(request):
    """Sincroniza rascunhos locais com o servidor, reconciliando IDs com vendas existentes."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Metodo nao permitido.'}, status=405)

    if not _can_access_contestation_module(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissao.'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        payload = {}

    raw_items = payload.get('items') or []
    if not isinstance(raw_items, list):
        return JsonResponse({'success': False, 'error': 'Payload invalido.'}, status=400)

    synced = []
    unresolved = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        local_id = item.get('exclusion_id')
        reason = (item.get('reason') or '').strip()
        summary = item.get('summary') or {}
        exclusion = _resolve_exclusion_for_draft_item(request.user, exclusion_id=local_id, summary=summary)

        if not exclusion:
            unresolved.append({'local_id': local_id})
            continue

        draft, _ = ContestationCartDraft.objects.get_or_create(
            user=request.user,
            exclusion=exclusion,
            defaults={'reason': reason},
        )

        if reason:
            draft.reason = reason
            draft.save(update_fields=['reason', 'updated_at'])

        synced.append({
            'local_id': local_id,
            'resolved_exclusion_id': exclusion.pk,
            'has_attachment': bool(draft.attachment),
        })

    return JsonResponse({
        'success': True,
        'synced': synced,
        'unresolved': unresolved,
    })


@login_required
def cart_draft_compact(request):
    """Mantem no maximo 44 itens no carrinho, priorizando IDs vindos do cliente."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Metodo nao permitido.'}, status=405)

    if not _can_access_contestation_module(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissao.'}, status=403)

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        payload = {}

    requested_ids = payload.get('ids') or []
    max_items_raw = payload.get('max_items', 44)
    try:
        max_items = int(max_items_raw)
    except (TypeError, ValueError):
        max_items = 44
    max_items = max(1, min(max_items, 200))

    preferred_ids = []
    for raw in requested_ids:
        try:
            rid = int(raw)
        except (TypeError, ValueError):
            continue
        if rid not in preferred_ids:
            preferred_ids.append(rid)

    user_drafts_qs = ContestationCartDraft.objects.filter(user=request.user).order_by('-updated_at', '-id')
    draft_map = {d.exclusion_id: d for d in user_drafts_qs}

    keep_ids = []
    for rid in preferred_ids:
        if rid in draft_map and rid not in keep_ids:
            keep_ids.append(rid)
        if len(keep_ids) >= max_items:
            break

    if len(keep_ids) < max_items:
        for draft in user_drafts_qs:
            rid = draft.exclusion_id
            if rid in keep_ids:
                continue
            keep_ids.append(rid)
            if len(keep_ids) >= max_items:
                break

    existing_ids = list(draft_map.keys())
    keep_set = set(keep_ids)
    delete_ids = [rid for rid in existing_ids if rid not in keep_set]

    deleted_count = 0
    if delete_ids:
        deleted_count, _ = ContestationCartDraft.objects.filter(user=request.user, exclusion_id__in=delete_ids).delete()

    return JsonResponse({
        'success': True,
        'kept': len(keep_ids),
        'deleted': deleted_count,
        'keep_ids': keep_ids,
    })


@login_required
def create_contestation(request, exclusion_id):
    """Cria uma nova contestação para um registro de exclusão."""
    if not _can_create_contestations(request.user):
        messages.error(request, 'Sem permissão para contestar.')
        return redirect('contestacao:exclusion_list')

    sync_state = _get_sync_window_state()
    if sync_state['is_blocked']:
        if sync_state['has_sync']:
            messages.error(request, 'Período de 3 dias após a última sincronização expirou. Sincronize a planilha para liberar novas contestações.')
        else:
            messages.error(request, 'É necessário sincronizar a planilha antes de criar novas contestações.')
        return redirect('contestacao:exclusion_list')

    exclusion = get_object_or_404(ExclusionRecord, pk=exclusion_id)

    # Verificar se já existe contestação em andamento
    existing = Contestation.objects.filter(exclusion=exclusion).filter(_open_contestation_filter()).first()
    if existing:
        messages.warning(request, 'Já existe uma contestação em andamento para este registro.')
        return redirect('contestacao:exclusion_list')

    if _has_open_contestation_for_sector(exclusion.filial):
        messages.warning(request, f'Já existe uma contestação em andamento para o setor {exclusion.filial}.')
        return redirect('contestacao:exclusion_list')

    if request.method == 'POST':
        reason = request.POST.get('reason', '').strip()
        attachment = request.FILES.get('attachment')
        if not reason:
            messages.error(request, 'Informe o motivo da contestação.')
        else:
            c = Contestation.objects.create(
                exclusion=exclusion,
                requester=request.user,
                reason=reason,
                attachment=attachment,
            )
            ContestationHistory.objects.create(
                contestation=c,
                action='created',
                user=request.user,
                notes=reason,
                extra_data={'exclusion_id': exclusion.pk, 'vendedor': exclusion.vendedor},
            )
            messages.success(request, f'Contestação #{c.pk} criada com sucesso!')
            return redirect('contestacao:my_contestations')

    return render(request, 'contestacao/create_contestation.html', {'exclusion': exclusion})


@login_required
def bulk_create_contestation(request):
    """Cria contestações em lote — cada item com motivo e evidência individual (via FormData)."""
    if not _can_create_contestations(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissão para contestar.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido.'}, status=405)

    sync_state = _get_sync_window_state()
    if sync_state['is_blocked']:
        if sync_state['has_sync']:
            error_msg = 'Período de 3 dias após a última sincronização expirou. Sincronize a planilha para liberar novas contestações.'
        else:
            error_msg = 'É necessário sincronizar a planilha antes de criar novas contestações.'
        return JsonResponse({'success': False, 'error': error_msg}, status=400)

    try:
        count = int(request.POST.get('count', 0))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Dados inválidos.'}, status=400)

    if count <= 0:
        return JsonResponse({'success': False, 'error': 'Nenhum item enviado.'}, status=400)

    # Parse items from FormData
    items = []
    requested_ids = set()
    for i in range(count):
        eid = request.POST.get(f'exclusion_id_{i}')
        use_server_file = (request.POST.get(f'use_server_file_{i}') or '').strip() in ['1', 'true', 'True', 'yes', 'on']
        reason = request.POST.get(f'reason_{i}', '').strip()
        attachment = request.FILES.get(f'file_{i}')
        if not eid:
            continue
        try:
            eid_int = int(eid)
        except (TypeError, ValueError):
            continue

        requested_ids.add(eid_int)
        if not reason:
            continue

        items.append({
            'exclusion_id': eid_int,
            'reason': reason,
            'attachment': attachment,
            'use_server_file': use_server_file,
        })

    if not items:
        return JsonResponse({'success': False, 'error': 'Nenhum item válido. Motivo e evidência são obrigatórios.'}, status=400)

    exclusion_ids = [item['exclusion_id'] for item in items]
    exclusions_by_id = {e.pk: e for e in ExclusionRecord.objects.filter(pk__in=exclusion_ids)}
    draft_map = {
        d.exclusion_id: d for d in ContestationCartDraft.objects.filter(user=request.user, exclusion_id__in=exclusion_ids)
    }

    # Filter out already contested
    already_contested = set(
        Contestation.objects.filter(exclusion_id__in=exclusion_ids)
        .filter(_open_contestation_filter())
        .values_list('exclusion_id', flat=True)
    )

    open_sectors = set(
        Contestation.objects.filter(_open_contestation_filter())
        .values_list('exclusion__filial', flat=True)
    )
    open_sectors = {str(sector).strip().upper() for sector in open_sectors if sector}

    created_count = 0
    created_ids = []
    for item in items:
        eid = item['exclusion_id']
        reason = item['reason']
        attachment = item['attachment']
        if item.get('use_server_file') and not attachment:
            draft_item = draft_map.get(eid)
            if draft_item and draft_item.attachment:
                attachment = draft_item.attachment
        if eid not in exclusions_by_id or eid in already_contested:
            continue
        if not attachment:
            continue
        exclusion = exclusions_by_id[eid]
        if str(exclusion.filial).strip().upper() in open_sectors:
            continue
        c = Contestation.objects.create(
            exclusion=exclusion,
            requester=request.user,
            reason=reason,
            attachment=attachment,
        )
        ContestationHistory.objects.create(
            contestation=c,
            action='created',
            user=request.user,
            notes=reason,
            extra_data={'exclusion_id': exclusion.pk, 'vendedor': exclusion.vendedor, 'bulk': True},
        )
        created_count += 1
        created_ids.append(eid)
        already_contested.add(eid)
        if exclusion.filial:
            open_sectors.add(str(exclusion.filial).strip().upper())

    if created_count == 0:
        return JsonResponse({'success': False, 'error': 'Nenhuma contestacao pode ser criada. Verifique se os itens possuem motivo e evidencia salvos.'})

    ContestationCartDraft.objects.filter(user=request.user, exclusion_id__in=created_ids).delete()

    return JsonResponse({'success': True, 'created': created_count})


@login_required
def my_contestations(request):
    """Minhas contestações (criadas por mim) ou todas visíveis por setor."""
    if not _can_create_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('home')

    qs = Contestation.objects.select_related('exclusion', 'requester', 'reviewed_by')
    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)

    if rank >= HIERARCHY_RANK['SUPERADMIN'] or _has_global_contestation_access(request.user):
        pass  # vê tudo
    else:
        # Admin+ ou PADRAO veem contestações de seus setores
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        
        if user_sectors:
            # Filtrar contestações cuja filial bate com algum setor do usuário
            matching_ids = []
            for record in qs:
                if _match_sector_to_filial(user_sectors, record.exclusion.filial):
                    matching_ids.append(record.id)
            qs = qs.filter(id__in=matching_ids) if matching_ids else qs.filter(requester=request.user)
        else:
            qs = qs.filter(requester=request.user)

    status_filter = request.GET.get('status', '')
    if status_filter == 'rejected_pending':
        qs = qs.filter(status='rejected', confirmed_by__isnull=True)
    elif status_filter:
        qs = qs.filter(status=status_filter)

    context = {
        'contestations': qs[:100],
        'status_filter': status_filter,
        'can_manage': _can_manage_contestations(request.user),
    }
    return render(request, 'contestacao/my_contestations.html', context)


@login_required
def manage_contestations(request):
    """Lista de contestações para análise (ADMIN/SUPERADMIN)."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para gerenciar contestações.')
        return redirect('contestacao:my_contestations')

    base_qs = Contestation.objects.select_related('exclusion', 'requester', 'reviewed_by', 'confirmed_by')

    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    can_view_all_scope = _can_view_all_contestation_scope(request.user)
    if not can_view_all_scope:
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        if user_sectors:
            # Filtrar contestações cuja filial bate com algum setor do usuário
            matching_ids = []
            for record in base_qs:
                if _match_sector_to_filial(user_sectors, record.exclusion.filial):
                    matching_ids.append(record.id)
            base_qs = base_qs.filter(id__in=matching_ids) if matching_ids else base_qs.none()
        else:
            base_qs = base_qs.none()

    # Filtro de loja/filial
    filial_filter = request.GET.get('filial', '').strip()
    if filial_filter:
        base_qs = base_qs.filter(exclusion__filial__iexact=filial_filter)

    # Filtro de status
    status_filter = request.GET.get('status', 'pending')
    qs = base_qs
    if status_filter == 'awaiting_manager':
        qs = qs.filter(status='rejected', confirmed_by__isnull=True)
    elif status_filter:
        qs = qs.filter(status=status_filter)

    sector_card_statuses = {'', 'pending', 'awaiting_manager', 'confirmed', 'rejected', 'denied'}

    # Para os status com cards de setor, listar apenas quando setor for selecionado.
    selected_sector = request.GET.get('setor', '').strip()
    sector_cards_enabled = status_filter in sector_card_statuses
    sector_cards_base_qs = qs
    pending_sector_cards_data = []

    if sector_cards_enabled:
        pending_sector_cards = (
            sector_cards_base_qs
            .values('exclusion__filial')
            .annotate(total=Count('id'), earliest=Min('created_at'))
            .order_by('earliest', 'exclusion__filial')
        )

        now = timezone.now()
        for item in pending_sector_cards:
            earliest = item.get('earliest')
            deadline = None
            remaining_seconds = 0
            remaining_label = ''
            is_expired = False

            if status_filter == 'pending' and earliest:
                deadline = earliest + timezone.timedelta(days=SECTOR_PENDING_DEADLINE_DAYS)
                remaining_seconds = int((deadline - now).total_seconds())
                remaining_label = _format_remaining_duration(remaining_seconds) if remaining_seconds > 0 else 'Expirado'
                is_expired = remaining_seconds <= 0

            pending_sector_cards_data.append({
                'exclusion__filial': item.get('exclusion__filial'),
                'total': item.get('total', 0),
                'earliest': earliest,
                'deadline': deadline,
                'remaining_seconds': max(remaining_seconds, 0),
                'remaining_label': remaining_label,
                'is_expired': is_expired,
            })

    if sector_cards_enabled:
        if selected_sector:
            qs = qs.filter(exclusion__filial__iexact=selected_sector)
        else:
            qs = qs.none()

    sector_cards_title_map = {
        '': 'Setores (todas as contestações)',
        'pending': 'Setores com pendências',
        'awaiting_manager': 'Setores aguardando gerente',
        'confirmed': 'Setores para pagamento',
        'rejected': 'Setores com rejeitadas',
        'denied': 'Setores negados',
    }
    
    # Get list of filiais for dropdown
    filiais = base_qs.values_list(
        'exclusion__filial', flat=True
    ).exclude(exclusion__filial='').distinct().order_by('exclusion__filial')

    pending_qs = base_qs.filter(status='pending')
    awaiting_manager_qs = base_qs.filter(status='rejected', confirmed_by__isnull=True)
    confirmed_qs = base_qs.filter(status='confirmed', payment_status='pending_payment')
    rejected_qs = base_qs.filter(status='rejected')
    denied_qs = base_qs.filter(status='denied')

    def _status_metrics(queryset):
        lines = queryset.count()
        sectors = queryset.exclude(exclusion__filial='').values('exclusion__filial').distinct().count()
        return {'lines': lines, 'sectors': sectors}

    pending_metrics = _status_metrics(pending_qs)
    awaiting_manager_metrics = _status_metrics(awaiting_manager_qs)
    confirmed_metrics = _status_metrics(confirmed_qs)
    rejected_metrics = _status_metrics(rejected_qs)
    denied_metrics = _status_metrics(denied_qs)
    all_metrics = _status_metrics(base_qs)

    pending_count = pending_metrics['lines']
    confirmed_count = confirmed_metrics['lines']
    awaiting_manager_count = awaiting_manager_metrics['lines']

    exclusions_qs = _apply_exclusion_visibility_filter(ExclusionRecord.objects.all(), request.user)
    if filial_filter:
        exclusions_qs = exclusions_qs.filter(filial__iexact=filial_filter)

    all_filiais_set = {
        str(f).strip() for f in exclusions_qs.values_list('filial', flat=True)
        if str(f).strip()
    }
    filiais_com_contestacao_set = {
        str(f).strip() for f in base_qs.values_list('exclusion__filial', flat=True)
        if str(f).strip()
    }
    info_only_no_submission_cards = sorted(all_filiais_set - filiais_com_contestacao_set)

    can_assign_global_managers = _can_assign_global_contestation_managers(request.user)
    global_manager_users = []
    available_manager_users = []
    if can_assign_global_managers:
        config = SystemConfig.get_config()
        global_manager_users = list(
            config.contestacao_global_managers.filter(is_active=True).order_by('first_name', 'last_name')
        )
        global_manager_ids = [u.id for u in global_manager_users]
        available_manager_users = list(
            User.objects.filter(is_active=True)
            .exclude(id__in=global_manager_ids)
            .order_by('first_name', 'last_name', 'email')
        )

    context = {
        'contestations': qs[:100],
        'status_filter': status_filter,
        'filial_filter': filial_filter,
        'filiais': filiais,
        'pending_count': pending_count,
        'confirmed_count': confirmed_count,
        'awaiting_manager_count': awaiting_manager_count,
        'pending_metrics': pending_metrics,
        'awaiting_manager_metrics': awaiting_manager_metrics,
        'confirmed_metrics': confirmed_metrics,
        'rejected_metrics': rejected_metrics,
        'denied_metrics': denied_metrics,
        'all_metrics': all_metrics,
        'status_choices': Contestation.STATUS_CHOICES,
        'payment_choices': Contestation.PAYMENT_CHOICES,
        'approval_mode_choices': Contestation.APPROVAL_MODE_CHOICES,
        'pending_sector_cards': pending_sector_cards_data,
        'sector_cards_enabled': sector_cards_enabled,
        'sector_cards_title': sector_cards_title_map.get(status_filter, 'Setores'),
        'info_only_no_submission_cards': info_only_no_submission_cards,
        'selected_sector': selected_sector,
        'can_assign_global_managers': can_assign_global_managers,
        'global_manager_users': global_manager_users,
        'available_manager_users': available_manager_users,
    }
    return render(request, 'contestacao/manage_contestations.html', context)


@login_required
def manage_global_contestation_managers(request):
    """Gerencia usuários liberados para gerenciar contestação globalmente."""
    if request.method != 'POST':
        return redirect('contestacao:manage_contestations')

    if not _can_assign_global_contestation_managers(request.user):
        messages.error(request, 'Sem permissão para liberar gestores globais da contestação.')
        return redirect('contestacao:manage_contestations')

    action = request.POST.get('action_type', '').strip()
    user_id = request.POST.get('user_id', '').strip()
    if not user_id:
        messages.warning(request, 'Selecione um usuário.')
        return redirect('contestacao:manage_contestations')

    try:
        target_user = User.objects.get(pk=int(user_id), is_active=True)
    except (TypeError, ValueError, User.DoesNotExist):
        messages.error(request, 'Usuário inválido.')
        return redirect('contestacao:manage_contestations')

    config = SystemConfig.get_config()

    if action == 'remove':
        config.contestacao_global_managers.remove(target_user)
        messages.success(request, f'{target_user.full_name} removido da gestão global de contestação.')
    else:
        config.contestacao_global_managers.add(target_user)
        messages.success(request, f'{target_user.full_name} liberado para gerenciar tudo em contestação.')

    return redirect('contestacao:manage_contestations')


@login_required
def update_contested_sale_value(request, pk):
    """Permite ao gestor atualizar o valor (receita) da venda contestada."""
    if request.method != 'POST':
        return redirect('contestacao:manage_contestations')

    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para alterar valor da venda.')
        return redirect('contestacao:manage_contestations')

    contestation = get_object_or_404(Contestation.objects.select_related('exclusion'), pk=pk)

    # Garante que o usuário só altere valores dentro do escopo permitido.
    allowed_qs = _apply_sector_visibility_filter(Contestation.objects.filter(pk=contestation.pk), request.user)
    if not allowed_qs.exists():
        messages.error(request, 'Você não pode alterar o valor desta contestação.')
        return redirect('contestacao:manage_contestations')

    raw_value = (request.POST.get('new_receita') or '').strip()
    if not raw_value:
        messages.warning(request, 'Informe o novo valor da venda.')
        return redirect('contestacao:manage_contestations')

    normalized = raw_value.replace('R$', '').replace(' ', '')
    if ',' in normalized:
        normalized = normalized.replace('.', '').replace(',', '.')

    try:
        new_value = Decimal(normalized)
    except (InvalidOperation, ValueError):
        messages.error(request, 'Valor inválido. Use um número válido, por exemplo: 1234,56')
        return redirect('contestacao:manage_contestations')

    if new_value < 0:
        messages.error(request, 'O valor não pode ser negativo.')
        return redirect('contestacao:manage_contestations')

    old_value = contestation.exclusion.receita
    if old_value == new_value:
        messages.info(request, f'O valor da contestação #{contestation.pk} já está em R$ {new_value:.2f}.')
        return redirect('contestacao:manage_contestations')

    if not contestation.sale_value_was_edited:
        contestation.sale_value_original = old_value
    contestation.sale_value_was_edited = True
    contestation.sale_value_edited_by = request.user
    contestation.sale_value_edited_at = timezone.now()
    contestation.save(update_fields=[
        'sale_value_original',
        'sale_value_was_edited',
        'sale_value_edited_by',
        'sale_value_edited_at',
        'updated_at',
    ])

    contestation.exclusion.receita = new_value
    contestation.exclusion.save(update_fields=['receita'])

    messages.success(
        request,
        f'Valor da venda da contestação #{contestation.pk} atualizado de R$ {old_value:.2f} para R$ {new_value:.2f}.'
    )
    return redirect('contestacao:manage_contestations')


@login_required
def update_contestation_manage(request, pk):
    """Permite ao gestor editar administrativamente o objeto de contestação."""
    if request.method != 'POST':
        return redirect('contestacao:manage_contestations')

    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para editar a contestação.')
        return redirect('contestacao:manage_contestations')

    contestation = get_object_or_404(Contestation, pk=pk)

    allowed_qs = _apply_sector_visibility_filter(Contestation.objects.filter(pk=contestation.pk), request.user)
    if not allowed_qs.exists():
        messages.error(request, 'Você não pode editar esta contestação.')
        return redirect('contestacao:manage_contestations')

    status = (request.POST.get('status') or '').strip()
    payment_status = (request.POST.get('payment_status') or '').strip()
    approval_mode = (request.POST.get('approval_mode') or '').strip()
    reason = (request.POST.get('reason') or '').strip()
    review_notes = (request.POST.get('review_notes') or '').strip()
    confirmation_notes = (request.POST.get('confirmation_notes') or '').strip()
    attachment_wrong = (request.POST.get('attachment_wrong') or '').lower() in ['1', 'true', 'on', 'yes']

    valid_statuses = {choice[0] for choice in Contestation.STATUS_CHOICES}
    valid_payments = {choice[0] for choice in Contestation.PAYMENT_CHOICES}
    valid_approval_modes = {choice[0] for choice in Contestation.APPROVAL_MODE_CHOICES}

    if status not in valid_statuses:
        messages.error(request, 'Status inválido.')
        return redirect('contestacao:manage_contestations')

    if payment_status not in valid_payments:
        messages.error(request, 'Status de pagamento inválido.')
        return redirect('contestacao:manage_contestations')

    if approval_mode not in valid_approval_modes:
        messages.error(request, 'Modo de aprovação inválido.')
        return redirect('contestacao:manage_contestations')

    def _parse_dtlocal(value):
        text = (value or '').strip()
        if not text:
            return None
        try:
            parsed = datetime.datetime.strptime(text, '%Y-%m-%dT%H:%M')
        except ValueError:
            return None
        return timezone.make_aware(parsed, timezone.get_current_timezone())

    reviewed_at = _parse_dtlocal(request.POST.get('reviewed_at'))
    confirmed_at = _parse_dtlocal(request.POST.get('confirmed_at'))
    paid_at = _parse_dtlocal(request.POST.get('paid_at'))

    attachment = request.FILES.get('attachment')
    review_attachment = request.FILES.get('review_attachment')

    changed_fields = []

    if contestation.status != status:
        contestation.status = status
        changed_fields.append('status')

    if contestation.payment_status != payment_status:
        contestation.payment_status = payment_status
        changed_fields.append('payment_status')

    if contestation.approval_mode != approval_mode:
        contestation.approval_mode = approval_mode
        changed_fields.append('approval_mode')

    if contestation.reason != reason:
        contestation.reason = reason
        changed_fields.append('reason')

    if contestation.review_notes != review_notes:
        contestation.review_notes = review_notes
        changed_fields.append('review_notes')

    if contestation.confirmation_notes != confirmation_notes:
        contestation.confirmation_notes = confirmation_notes
        changed_fields.append('confirmation_notes')

    if contestation.attachment_wrong != attachment_wrong:
        contestation.attachment_wrong = attachment_wrong
        changed_fields.append('attachment_wrong')

    if contestation.reviewed_at != reviewed_at:
        contestation.reviewed_at = reviewed_at
        changed_fields.append('reviewed_at')

    if contestation.confirmed_at != confirmed_at:
        contestation.confirmed_at = confirmed_at
        changed_fields.append('confirmed_at')

    if contestation.paid_at != paid_at:
        contestation.paid_at = paid_at
        changed_fields.append('paid_at')

    if attachment:
        contestation.attachment = attachment
        changed_fields.append('attachment')

    if review_attachment:
        contestation.review_attachment = review_attachment
        changed_fields.append('review_attachment')

    if contestation.reviewed_at and contestation.reviewed_by is None:
        contestation.reviewed_by = request.user
        changed_fields.append('reviewed_by')

    if contestation.confirmed_at and contestation.confirmed_by is None:
        contestation.confirmed_by = request.user
        changed_fields.append('confirmed_by')

    if changed_fields:
        contestation.save(update_fields=changed_fields + ['updated_at'])
        messages.success(request, f'Contestação #{contestation.pk} atualizada com sucesso.')
    else:
        messages.info(request, f'Nenhuma alteração detectada na contestação #{contestation.pk}.')

    return redirect('contestacao:manage_contestations')


@login_required
def release_sector_for_retry(request):
    """Libera um setor para refazer contestação, encerrando as contestações em andamento."""
    if request.method != 'POST':
        return redirect('contestacao:manage_contestations')

    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para gerenciar contestações.')
        return redirect('contestacao:my_contestations')

    sector = request.POST.get('setor', '').strip()
    if not sector:
        messages.warning(request, 'Selecione um setor para liberar o refazer da contestação.')
        return redirect('contestacao:manage_contestations?status=pending')

    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    if rank < HIERARCHY_RANK['SUPERADMIN']:
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        allowed_sectors = {s.strip().upper() for s in user_sectors if s}
        if sector.upper() not in allowed_sectors:
            messages.error(request, f'Você não pode liberar o setor {sector}.')
            return redirect('contestacao:manage_contestations?status=pending')

    open_qs = Contestation.objects.filter(
        exclusion__filial__iexact=sector
    ).filter(_open_contestation_filter())

    released_count = 0
    now = timezone.now()
    for c in open_qs.select_related('exclusion'):
        c.status = 'denied'
        c.payment_status = 'not_applicable'
        c.confirmed_by = request.user
        c.confirmed_at = now
        c.confirmation_notes = 'Liberada para refazer contestação do setor.'
        c.save(update_fields=['status', 'payment_status', 'confirmed_by', 'confirmed_at', 'confirmation_notes', 'updated_at'])

        ContestationHistory.objects.create(
            contestation=c,
            action='denied',
            user=request.user,
            notes='Liberação manual para refazer contestação do setor.',
            extra_data={'sector_released': sector},
        )
        released_count += 1

    if released_count == 0:
        messages.info(request, f'Nenhuma contestação em andamento encontrada para o setor {sector}.')
    else:
        messages.success(request, f'Setor {sector} liberado para refazer contestação ({released_count} registro(s) encerrado(s)).')

    return redirect('contestacao:manage_contestations')


@login_required
def contested_with_vivo(request):
    """Lista vendas com ação 'Aprovar e Contestar' para envio à Vivo."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para gerenciar contestações.')
        return redirect('contestacao:my_contestations')

    qs = Contestation.objects.filter(approval_mode='approved_and_contested').select_related(
        'exclusion', 'requester', 'reviewed_by', 'confirmed_by'
    )
    qs = _apply_sector_visibility_filter(qs, request.user)

    filial_filter = request.GET.get('filial', '').strip()
    if filial_filter:
        qs = qs.filter(exclusion__filial__iexact=filial_filter)

    filiais = qs.values_list('exclusion__filial', flat=True).exclude(exclusion__filial='').distinct().order_by('exclusion__filial')

    total_sales = qs.count()
    total_value = qs.aggregate(total=Sum('exclusion__receita'))['total'] or 0
    chart_rows = (
        qs.values('exclusion__filial')
        .annotate(total_sales=Count('id'), total_value=Sum('exclusion__receita'))
        .order_by('-total_sales', 'exclusion__filial')[:15]
    )
    chart_labels = [row['exclusion__filial'] or '-' for row in chart_rows]
    chart_totals = [row['total_sales'] for row in chart_rows]

    context = {
        'contestations': qs.order_by('-created_at')[:300],
        'filiais': filiais,
        'filial_filter': filial_filter,
        'total': total_sales,
        'total_value': total_value,
        'chart_labels': chart_labels,
        'chart_totals': chart_totals,
    }
    return render(request, 'contestacao/contested_with_vivo.html', context)


@login_required
def contestation_detail(request, pk):
    """Detalhe de uma contestação."""
    contestation = get_object_or_404(
        Contestation.objects.select_related('exclusion', 'requester', 'reviewed_by'), pk=pk
    )

    # Permissão: criador, admin do setor, superadmin
    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    if contestation.requester != request.user and rank < HIERARCHY_RANK['ADMIN']:
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:my_contestations')

    context = {
        'c': contestation,
        'can_manage': _can_manage_contestations(request.user),
    }
    return render(request, 'contestacao/contestation_detail.html', context)


@login_required
def approve_contestation(request, pk):
    """Gestor aprova a contestação e envia direto para pagamento."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')
    c = get_object_or_404(Contestation, pk=pk, status='pending')
    notes = request.POST.get('review_notes', '')
    attachment = request.FILES.get('review_attachment')
    c.approve(request.user, notes, approval_mode='approved', attachment=attachment)
    ContestationHistory.objects.create(
        contestation=c, action='approved', user=request.user, notes=notes,
    )
    messages.success(request, f'Contestação #{c.pk} aprovada e enviada para pagamento.')
    return redirect('contestacao:manage_contestations')


@login_required
def approve_and_contest_contestation(request, pk):
    """Gestor aprova como 'Aprovar e Contestar' e envia direto para pagamento."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')
    c = get_object_or_404(Contestation, pk=pk, status='pending')
    notes = request.POST.get('review_notes', '')
    attachment = request.FILES.get('review_attachment')
    c.approve(request.user, notes, approval_mode='approved_and_contested', attachment=attachment)
    ContestationHistory.objects.create(
        contestation=c,
        action='approved_and_contested',
        user=request.user,
        notes=notes,
    )
    messages.success(request, f'Contestação #{c.pk} aprovada como "Aprovar e Contestar" e enviada para pagamento.')
    return redirect('contestacao:manage_contestations')


@login_required
def reject_contestation(request, pk):
    """Gestor rejeita a contestação."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')
    c = get_object_or_404(Contestation, pk=pk, status='pending')
    notes = request.POST.get('review_notes', '')
    attachment = request.FILES.get('review_attachment')
    c.reject(request.user, notes, attachment=attachment)

    # Rejeitada volta à base com parecer de rejeição no campo observação.
    parecer = notes.strip() if notes else 'Sem parecer informado.'
    c.exclusion.observacao = f'Parecer da rejeição: {parecer}'
    c.exclusion.save(update_fields=['observacao'])

    ContestationHistory.objects.create(
        contestation=c, action='rejected', user=request.user, notes=notes,
    )
    messages.success(request, f'Contestação #{c.pk} rejeitada! Aguardando de acordo do gerente.')
    return redirect('contestacao:manage_contestations')


@login_required
def toggle_attachment_wrong(request, pk):
    """Marca/desmarca 'Anexo veio errado' na contestação."""
    if not _can_manage_contestations(request.user):
        return JsonResponse({'success': False, 'error': 'Sem permissão.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido.'}, status=405)

    c = get_object_or_404(Contestation, pk=pk)
    value = request.POST.get('attachment_wrong', 'false').lower() in ['1', 'true', 'on', 'yes']
    c.attachment_wrong = value
    c.save(update_fields=['attachment_wrong', 'updated_at'])

    return JsonResponse({'success': True, 'attachment_wrong': c.attachment_wrong})


@login_required
def confirm_contestation(request, pk):
    """Gerente dá de acordo para fluxos legados (accepted) e rejeições (rejected)."""
    c = get_object_or_404(Contestation, pk=pk, status__in=['accepted', 'rejected'])
    if c.requester != request.user:
        messages.error(request, 'Apenas o solicitante pode dar de acordo nesta contestação.')
        return redirect('contestacao:my_contestations')
    was_accepted = c.status == 'accepted'
    notes = request.POST.get('confirmation_notes', '')
    c.confirm(request.user, notes)
    ContestationHistory.objects.create(
        contestation=c, action='confirmed', user=request.user, notes=notes,
    )
    if was_accepted:
        messages.success(request, f'Contestação #{c.pk} confirmada e enviada para pagamento.')
    else:
        messages.success(request, f'De acordo registrado na contestação #{c.pk}.')
    return redirect('contestacao:my_contestations')


@login_required
def deny_contestation(request, pk):
    """Gerente discorda da decisão do gestor."""
    c = get_object_or_404(Contestation, pk=pk, status__in=['accepted', 'rejected'])
    if c.requester != request.user:
        messages.error(request, 'Apenas o solicitante pode contestar esta decisão.')
        return redirect('contestacao:my_contestations')
    notes = request.POST.get('confirmation_notes', '')
    c.deny_confirmation(request.user, notes)
    ContestationHistory.objects.create(
        contestation=c, action='denied', user=request.user, notes=notes,
    )
    messages.success(request, f'Contestação #{c.pk} negada.')
    return redirect('contestacao:my_contestations')


@login_required
def mark_paid(request, pk):
    """Gestor marca como pago (após confirmação do gerente)."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')
    c = get_object_or_404(Contestation, pk=pk, status='confirmed')
    c.mark_paid(request.user)
    ContestationHistory.objects.create(
        contestation=c, action='paid', user=request.user,
    )
    messages.success(request, f'Contestação #{c.pk} marcada como paga!')
    return redirect('contestacao:manage_contestations')


@login_required
def bulk_mark_paid(request):
    """Marca várias contestações confirmadas como pagas."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')

    if request.method != 'POST':
        return redirect('contestacao:manage_contestations')

    ids = request.POST.getlist('contestation_ids')
    valid_ids = []
    for raw_id in ids:
        try:
            valid_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue

    if not valid_ids:
        messages.warning(request, 'Selecione ao menos uma contestação para marcar como paga.')
        return redirect('contestacao:manage_contestations?status=confirmed')

    contestations = Contestation.objects.filter(
        pk__in=valid_ids,
        status='confirmed',
        payment_status='pending_payment',
    ).select_related('exclusion')

    paid_count = 0
    for c in contestations:
        c.mark_paid(request.user)
        ContestationHistory.objects.create(
            contestation=c,
            action='paid',
            user=request.user,
            notes='Pagamento em lote',
            extra_data={'bulk': True},
        )
        paid_count += 1

    if paid_count == 0:
        messages.warning(request, 'Nenhuma contestação válida foi marcada como paga.')
    else:
        messages.success(request, f'{paid_count} contestação(ões) marcada(s) como paga(s)!')

    return redirect('contestacao:manage_contestations?status=confirmed')


@login_required
def contestation_history(request):
    """Histórico de todas as ações em contestações (apenas SUPERADMIN)."""
    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    if rank < HIERARCHY_RANK['SUPERADMIN']:
        messages.error(request, 'Sem permissão para ver o histórico.')
        return redirect('contestacao:exclusion_list')

    qs = ContestationHistory.objects.select_related('contestation', 'contestation__exclusion', 'user')

    action_filter = request.GET.get('action', '').strip()
    if action_filter:
        qs = qs.filter(action=action_filter)

    search = request.GET.get('q', '').strip()
    if search:
        qs = qs.filter(
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) |
            Q(contestation__exclusion__vendedor__icontains=search) |
            Q(notes__icontains=search)
        )

    context = {
        'history': qs[:200],
        'action_filter': action_filter,
        'search': search,
        'action_choices': ContestationHistory.ACTION_CHOICES,
    }
    return render(request, 'contestacao/contestation_history.html', context)


@login_required
def dashboard(request):
    """Dashboard de contestações com métricas e totais."""
    # Apenas ADMIN+ podem acessar o dashboard
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para acessar o dashboard.')
        return redirect('home')

    qs = Contestation.objects.select_related('exclusion')
    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    can_view_all_scope = _can_view_all_contestation_scope(request.user)

    # Filtro por setor (mesma lógica do exclusion_list)
    if not can_view_all_scope:
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        if user_sectors:
            # Filtrar contestações cuja filial bate com algum setor do usuário
            matching_ids = []
            for record in qs:
                if _match_sector_to_filial(user_sectors, record.exclusion.filial):
                    matching_ids.append(record.id)
            qs = qs.filter(id__in=matching_ids) if matching_ids else qs.filter(requester=request.user)
        else:
            qs = qs.filter(requester=request.user)

    # Total na base (ExclusionRecord) com mesmo filtro de setor
    base_qs = ExclusionRecord.objects.all()
    if not can_view_all_scope:
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        if user_sectors:
            matching_ids = []
            for record in base_qs:
                if _match_sector_to_filial(user_sectors, record.filial):
                    matching_ids.append(record.id)
            base_qs = base_qs.filter(id__in=matching_ids) if matching_ids else base_qs.none()
        else:
            base_qs = base_qs.none()
    total_na_base = base_qs.count()
    receita_na_base = base_qs.aggregate(total=Sum('receita'))['total'] or 0

    total_enviado = qs.count()
    total_aceito = qs.filter(status__in=['accepted', 'confirmed']).count()
    total_recusado = qs.filter(status__in=['rejected', 'denied']).count()
    total_pendente = qs.filter(status='pending').count()

    receita_contestada = qs.aggregate(total=Sum('exclusion__receita'))['total'] or 0
    receita_aceita = qs.filter(status__in=['accepted', 'confirmed']).aggregate(total=Sum('exclusion__receita'))['total'] or 0

    # Métricas por pilar
    por_pilar = (
        qs.values('exclusion__pilar')
        .annotate(
            total=Count('id'),
            aceitos=Count('id', filter=Q(status__in=['accepted', 'confirmed'])),
            recusados=Count('id', filter=Q(status__in=['rejected', 'denied'])),
        )
        .order_by('-total')
    )

    base_por_pilar = {
        row['pilar'] or '': row['total']
        for row in base_qs.values('pilar').annotate(total=Count('id'))
    }
    por_pilar = list(por_pilar)
    for item in por_pilar:
        pilar_key = item.get('exclusion__pilar') or ''
        total_base = base_por_pilar.get(pilar_key, 0)
        item['poderiam_ser_contestadas'] = max(total_base - item['total'], 0)

    # Métricas por filial
    por_filial = (
        qs.values('exclusion__filial')
        .annotate(
            total=Count('id'),
            aceitos=Count('id', filter=Q(status__in=['accepted', 'confirmed'])),
            recusados=Count('id', filter=Q(status__in=['rejected', 'denied'])),
        )
        .order_by('-total')
    )

    base_por_filial = {
        row['filial'] or '': row['total']
        for row in base_qs.values('filial').annotate(total=Count('id'))
    }
    por_filial = list(por_filial)
    for item in por_filial:
        filial_key = item.get('exclusion__filial') or ''
        total_base = base_por_filial.get(filial_key, 0)
        item['poderiam_ser_contestadas'] = max(total_base - item['total'], 0)

    # Métricas por status
    por_status = qs.values('status').annotate(total=Count('id')).order_by('-total')

    # Taxa de aprovação
    finalizados = total_aceito + total_recusado
    taxa_aprovacao = round((total_aceito / finalizados * 100), 1) if finalizados > 0 else 0

    # Tempo médio da solicitação do gerente: criado -> analisado pelo gestor
    manager_request_seconds = 0
    manager_request_count = 0
    for c in qs.filter(reviewed_at__isnull=False):
        delta = (c.reviewed_at - c.created_at).total_seconds()
        if delta > 0:
            manager_request_seconds += delta
            manager_request_count += 1

    # Tempo médio aguardando gerente: analisado -> de acordo do gerente
    awaiting_manager_seconds = 0
    awaiting_manager_count = 0
    for c in qs.filter(reviewed_at__isnull=False, confirmed_at__isnull=False):
        delta = (c.confirmed_at - c.reviewed_at).total_seconds()
        if delta > 0:
            awaiting_manager_seconds += delta
            awaiting_manager_count += 1

    # Tempo médio do pagamento: confirmado -> pago
    payment_seconds = 0
    payment_count = 0
    for c in qs.filter(payment_status='paid', paid_at__isnull=False, confirmed_at__isnull=False):
        delta = (c.paid_at - c.confirmed_at).total_seconds()
        if delta > 0:
            payment_seconds += delta
            payment_count += 1

    avg_manager_request_time = _format_avg_duration(manager_request_seconds, manager_request_count)
    avg_awaiting_manager_time = _format_avg_duration(awaiting_manager_seconds, awaiting_manager_count)
    avg_payment_time = _format_avg_duration(payment_seconds, payment_count)

    context = {
        'total_na_base': total_na_base,
        'receita_na_base': receita_na_base,
        'total_enviado': total_enviado,
        'total_aceito': total_aceito,
        'total_recusado': total_recusado,
        'total_pendente': total_pendente,
        'receita_contestada': receita_contestada,
        'receita_aceita': receita_aceita,
        'por_pilar': por_pilar,
        'por_filial': por_filial,
        'por_status': por_status,
        'taxa_aprovacao': taxa_aprovacao,
        'avg_manager_request_time': avg_manager_request_time,
        'avg_awaiting_manager_time': avg_awaiting_manager_time,
        'avg_payment_time': avg_payment_time,
    }
    return render(request, 'contestacao/dashboard.html', context)


@login_required
def export_contested_sales(request):
    """Exporta CSV detalhado com modelo da base de exclusão + colunas da contestação."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para exportar.')
        return redirect('contestacao:dashboard')

    qs = Contestation.objects.select_related('exclusion', 'requester', 'reviewed_by').order_by('-created_at')
    qs = _apply_sector_visibility_filter(qs, request.user)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="vendas_contestadas.csv"'

    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'FILIAL',
        'Vendedor',
        'RECEITA',
        'Pilar',
        'Gerente',
        'Coordenacao',
        'Nº da Venda',
        'Data da Venda',
        'Nome Cliente',
        'CPF/CNPJ',
        'Plano/Produto',
        'IMEI',
        'Numero Acesso',
        'Observacao',
        'ID Contestacao',
        'Motivo Contestacao',
        'Parecer Gestor',
        'Parecer Gerente',
        'Ha Evidencia (Solicitante)',
        'Ha Evidencia (Gestor)',
        'Anexo Veio Errado',
        'Status Contestacao',
        'Status Pagamento',
        'Botao Clicado',
        'Solicitante',
        'Gestor',
        'Confirmado Por',
        'Data Criacao',
        'Data Analise',
        'Data Confirmacao',
        'Data Pagamento',
        'Venda Editada',
        'Valor Original Venda',
        'Editada Por',
        'Data Edicao Venda',
    ])

    for c in qs:
        writer.writerow([
            c.exclusion.filial,
            c.exclusion.vendedor,
            f"{c.exclusion.receita:.2f}",
            c.exclusion.pilar,
            c.exclusion.gerente,
            c.exclusion.coordenacao,
            c.exclusion.numero_venda,
            c.exclusion.data_venda,
            c.exclusion.nome_cliente,
            c.exclusion.cpf_cnpj,
            c.exclusion.plano_produto,
            c.exclusion.imei,
            c.exclusion.numero_acesso,
            c.exclusion.observacao,
            c.pk,
            c.reason,
            c.review_notes,
            c.confirmation_notes,
            'Sim' if c.attachment else 'Nao',
            'Sim' if c.review_attachment else 'Nao',
            'Sim' if c.attachment_wrong else 'Nao',
            _status_label(c.status),
            _payment_status_label(c.payment_status),
            _approval_mode_label(c.approval_mode),
            c.requester.full_name,
            c.reviewed_by.full_name if c.reviewed_by else '',
            c.confirmed_by.full_name if c.confirmed_by else '',
            c.created_at.strftime('%d/%m/%Y %H:%M') if c.created_at else '',
            c.reviewed_at.strftime('%d/%m/%Y %H:%M') if c.reviewed_at else '',
            c.confirmed_at.strftime('%d/%m/%Y %H:%M') if c.confirmed_at else '',
            c.paid_at.strftime('%d/%m/%Y %H:%M') if c.paid_at else '',
            'Sim' if c.sale_value_was_edited else 'Nao',
            f"{c.sale_value_original:.2f}" if c.sale_value_original is not None else '',
            c.sale_value_edited_by.full_name if c.sale_value_edited_by else '',
            c.sale_value_edited_at.strftime('%d/%m/%Y %H:%M') if c.sale_value_edited_at else '',
        ])

    return response


@login_required
def export_contestation_report(request):
    """Exporta CSV de relatório agregado das contestações com botão clicado."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão para exportar.')
        return redirect('contestacao:dashboard')

    qs = Contestation.objects.select_related('exclusion').order_by('-created_at')
    qs = _apply_sector_visibility_filter(qs, request.user)

    grouped = (
        qs.values('exclusion__filial', 'exclusion__pilar', 'status', 'approval_mode')
        .annotate(total_vendas=Count('id'), receita_total=Sum('exclusion__receita'))
        .order_by('exclusion__filial', 'exclusion__pilar', 'status', 'approval_mode')
    )

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="relatorio_contestacoes.csv"'

    response.write('\ufeff')
    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Filial',
        'Pilar',
        'Status',
        'Botao Clicado',
        'Total Vendas',
        'Receita Total',
    ])

    for row in grouped:
        writer.writerow([
            row['exclusion__filial'] or '-',
            row['exclusion__pilar'] or '-',
            _status_label(row['status']),
            _approval_mode_label(row['approval_mode']),
            row['total_vendas'] or 0,
            f"{(row['receita_total'] or 0):.2f}",
        ])

    return response
