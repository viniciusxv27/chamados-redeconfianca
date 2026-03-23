import io
import csv
import pandas as pd
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.cache import cache
from django.http import JsonResponse, HttpResponse
from django.db import models
from django.db.models import Count, Min, Q, Sum
from django.utils import timezone

from .models import ExclusionRecord, Contestation, ContestationHistory
from users.models import SystemConfig, User


HIERARCHY_RANK = {
    'PADRAO': 0,
    'ADMINISTRATIVO': 1,
    'SUPERVISOR': 2,
    'ADMIN': 3,
    'SUPERADMIN': 4,
}

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


def _can_create_contestations(user):
    return user.can_create_contestations()


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
        url = config.excel_base_exclusao_url or DEFAULT_EXCEL_BASE_EXCLUSAO_URL
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
    coord_col = _find_column(df, 'COORDENACAO') or _find_column(df, 'COORDENAÇÃO') or _find_column(df, 'COORDENADOR')
    nvenda_col = _find_column(df, 'Nº DA VENDA') or _find_column(df, 'N DA VENDA') or _find_column(df, 'NUMERO_VENDA')
    data_col = _find_column(df, 'DATA')
    cliente_col = _find_column(df, 'NOME CLIENTE') or _find_column(df, 'CLIENTE')
    cpf_col = _find_column(df, 'CPF/CNPJ') or _find_column(df, 'CPF')
    plano_col = _find_column(df, 'PLANO/PRODUTO') or _find_column(df, 'PLANO') or _find_column(df, 'PRODUTO')
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
            coordenacao=str(row.get(coord_col, '')).strip() if coord_col else '',
            numero_venda=str(row.get(nvenda_col, '')).strip() if nvenda_col else '',
            data_venda=str(row.get(data_col, '')).strip() if data_col else '',
            nome_cliente=str(row.get(cliente_col, '')).strip() if cliente_col else '',
            cpf_cnpj=str(row.get(cpf_col, '')).strip() if cpf_col else '',
            plano_produto=str(row.get(plano_col, '')).strip() if plano_col else '',
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
    if not _can_create_contestations(request.user):
        messages.error(request, 'Sem permissão para acessar contestações.')
        return redirect('home')

    qs = ExclusionRecord.objects.all()

    # Superadmin vê tudo; outros filtram por setor
    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    can_view_all_scope = _can_view_all_contestation_scope(request.user)
    if not can_view_all_scope:
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        
        if user_sectors:
            # Filtrar registros cuja filial bate com algum setor do usuário
            matching_ids = []
            for record in qs:
                if _match_sector_to_filial(user_sectors, record.filial):
                    matching_ids.append(record.id)
            qs = qs.filter(id__in=matching_ids) if matching_ids else qs.none()
        else:
            qs = qs.none()

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

    context = {
        'records': qs[:200],
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
    existing = Contestation.objects.filter(
        exclusion=exclusion, status__in=['pending', 'accepted', 'confirmed']
    ).first()
    if existing:
        messages.warning(request, 'Já existe uma contestação pendente para este registro.')
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
    for i in range(count):
        eid = request.POST.get(f'exclusion_id_{i}')
        reason = request.POST.get(f'reason_{i}', '').strip()
        attachment = request.FILES.get(f'file_{i}')
        if eid and reason and attachment:
            items.append({'exclusion_id': int(eid), 'reason': reason, 'attachment': attachment})

    if not items:
        return JsonResponse({'success': False, 'error': 'Nenhum item válido. Motivo e evidência são obrigatórios.'}, status=400)

    exclusion_ids = [item['exclusion_id'] for item in items]
    exclusions_by_id = {e.pk: e for e in ExclusionRecord.objects.filter(pk__in=exclusion_ids)}

    # Filter out already contested
    already_contested = set(
        Contestation.objects.filter(
            exclusion_id__in=exclusion_ids,
            status__in=['pending', 'accepted', 'confirmed'],
        ).values_list('exclusion_id', flat=True)
    )

    open_sectors = set(
        Contestation.objects.filter(_open_contestation_filter())
        .values_list('exclusion__filial', flat=True)
    )
    open_sectors = {str(sector).strip().upper() for sector in open_sectors if sector}

    created_count = 0
    for item in items:
        eid = item['exclusion_id']
        reason = item['reason']
        attachment = item['attachment']
        if eid not in exclusions_by_id or eid in already_contested:
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

    if created_count == 0:
        return JsonResponse({'success': False, 'error': 'Nenhuma contestação pôde ser criada (já existem contestações em andamento para os setores selecionados).'})

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
        qs = qs.filter(status__in=['accepted', 'rejected'], confirmed_by__isnull=True)
    elif status_filter:
        qs = qs.filter(status=status_filter)

    # Em "Pendentes", mostrar cards de setor e listar apenas quando setor for selecionado.
    selected_sector = request.GET.get('setor', '').strip()
    pending_sector_cards = (
        base_qs.filter(status='pending')
        .values('exclusion__filial')
        .annotate(total=Count('id'), earliest=Min('created_at'))
        .order_by('earliest', 'exclusion__filial')
    )

    now = timezone.now()
    pending_sector_cards_data = []
    for item in pending_sector_cards:
        earliest = item.get('earliest')
        deadline = (earliest + timezone.timedelta(days=SECTOR_PENDING_DEADLINE_DAYS)) if earliest else None
        remaining_seconds = int((deadline - now).total_seconds()) if deadline else 0
        pending_sector_cards_data.append({
            'exclusion__filial': item.get('exclusion__filial'),
            'total': item.get('total', 0),
            'earliest': earliest,
            'deadline': deadline,
            'remaining_seconds': max(remaining_seconds, 0),
            'remaining_label': _format_remaining_duration(remaining_seconds) if remaining_seconds > 0 else 'Expirado',
            'is_expired': remaining_seconds <= 0,
        })
    if status_filter == 'pending':
        if selected_sector:
            qs = qs.filter(exclusion__filial__iexact=selected_sector)
        else:
            qs = qs.none()
    
    # Get list of filiais for dropdown
    filiais = base_qs.values_list(
        'exclusion__filial', flat=True
    ).exclude(exclusion__filial='').distinct().order_by('exclusion__filial')

    pending_count = base_qs.filter(status='pending').count()
    confirmed_count = base_qs.filter(status='confirmed', payment_status='pending_payment').count()
    awaiting_manager_count = base_qs.filter(
        status__in=['accepted', 'rejected'], confirmed_by__isnull=True
    ).count()

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

    can_assign_global_managers = rank >= HIERARCHY_RANK['SUPERADMIN']
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
        'pending_sector_cards': pending_sector_cards_data,
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

    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    if rank < HIERARCHY_RANK['SUPERADMIN']:
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
    """Gestor aprova a contestação — aguarda confirmação do gerente."""
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
    messages.success(request, f'Contestação #{c.pk} aprovada! Aguardando confirmação do gerente.')
    return redirect('contestacao:manage_contestations')


@login_required
def approve_and_contest_contestation(request, pk):
    """Gestor aprova e marca como 'Aprovar e Contestar' para relatório."""
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
    messages.success(request, f'Contestação #{c.pk} aprovada como "Aprovar e Contestar".')
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
    """Gerente dá de acordo após decisão do gestor (aprovação ou rejeição)."""
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
        messages.success(request, f'Contestação #{c.pk} confirmada! Aguardando pagamento.')
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
    """Exporta CSV detalhado das vendas contestadas com coluna de botão clicado."""
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
        'ID Contestacao',
        'Vendedor',
        'Filial',
        'Pilar',
        'Numero da Venda',
        'Receita',
        'Status',
        'Status Pagamento',
        'Botao Clicado',
        'Solicitante',
        'Gestor',
        'Data Criacao',
        'Data Analise',
        'Data Confirmacao',
    ])

    for c in qs:
        writer.writerow([
            c.pk,
            c.exclusion.vendedor,
            c.exclusion.filial,
            c.exclusion.pilar,
            c.exclusion.numero_venda,
            f"{c.exclusion.receita:.2f}",
            _status_label(c.status),
            _payment_status_label(c.payment_status),
            _approval_mode_label(c.approval_mode),
            c.requester.full_name,
            c.reviewed_by.full_name if c.reviewed_by else '',
            c.created_at.strftime('%d/%m/%Y %H:%M') if c.created_at else '',
            c.reviewed_at.strftime('%d/%m/%Y %H:%M') if c.reviewed_at else '',
            c.confirmed_at.strftime('%d/%m/%Y %H:%M') if c.confirmed_at else '',
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
