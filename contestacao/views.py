import io
import pandas as pd
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.cache import cache
from django.http import JsonResponse
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import ExclusionRecord, Contestation, ContestationHistory
from users.models import SystemConfig


HIERARCHY_RANK = {
    'PADRAO': 0,
    'ADMINISTRATIVO': 1,
    'SUPERVISOR': 2,
    'ADMIN': 3,
    'SUPERADMIN': 4,
}

DEFAULT_EXCEL_BASE_EXCLUSAO_URL = "https://1drv.ms/x/c/871ee1819c7e2faa/IQBryBteOg4sS4cBwU1tIgKoATfi6qmYB8eRrIaTpyP8Qhc?e=pye3Sj"


def _can_manage_contestations(user):
    rank = HIERARCHY_RANK.get(user.hierarchy, 0)
    return rank >= HIERARCHY_RANK['ADMIN']


def _can_create_contestations(user):
    rank = HIERARCHY_RANK.get(user.hierarchy, 0)
    return rank >= HIERARCHY_RANK['SUPERVISOR']


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
    if not _can_create_contestations(request.user):
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
    if rank < HIERARCHY_RANK['SUPERADMIN']:
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        user_sectors_upper = [s.strip().upper() for s in user_sectors if s]
        if user_sectors_upper:
            q = Q()
            for s in user_sectors_upper:
                q |= Q(filial__iexact=s)
            qs = qs.filter(q)
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

    pilares = ExclusionRecord.objects.values_list('pilar', flat=True).distinct().order_by('pilar')
    filiais = ExclusionRecord.objects.values_list('filial', flat=True).distinct().order_by('filial')

    # IDs que já têm contestação pendente/em andamento with their statuses
    contestations_map = {}
    for c in Contestation.objects.filter(
        status__in=['pending', 'accepted', 'confirmed', 'rejected', 'denied']
    ).values('exclusion_id', 'status', 'pk'):
        contestations_map[c['exclusion_id']] = {'status': c['status'], 'pk': c['pk']}
    
    # Keep backwards compatibility
    contested_ids = set(contestations_map.keys())

    total_records = qs.count()
    total_receita = qs.aggregate(total=Sum('receita'))['total'] or 0

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
        'can_manage': _can_manage_contestations(request.user),
        'can_sync': _can_create_contestations(request.user),
        'is_superadmin': rank >= HIERARCHY_RANK['SUPERADMIN'],
    }
    return render(request, 'contestacao/exclusion_list.html', context)


@login_required
def create_contestation(request, exclusion_id):
    """Cria uma nova contestação para um registro de exclusão."""
    if not _can_create_contestations(request.user):
        messages.error(request, 'Sem permissão para contestar.')
        return redirect('contestacao:exclusion_list')

    exclusion = get_object_or_404(ExclusionRecord, pk=exclusion_id)

    # Verificar se já existe contestação em andamento
    existing = Contestation.objects.filter(
        exclusion=exclusion, status__in=['pending', 'accepted', 'confirmed']
    ).first()
    if existing:
        messages.warning(request, 'Já existe uma contestação pendente para este registro.')
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

    created_count = 0
    for item in items:
        eid = item['exclusion_id']
        reason = item['reason']
        attachment = item['attachment']
        if eid not in exclusions_by_id or eid in already_contested:
            continue
        exclusion = exclusions_by_id[eid]
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
        return JsonResponse({'success': False, 'error': 'Nenhuma contestação pôde ser criada (já existem contestações em andamento).'})

    return JsonResponse({'success': True, 'created': created_count})


@login_required
def my_contestations(request):
    """Minhas contestações (criadas por mim) ou todas visíveis por setor."""
    if not _can_create_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('home')

    qs = Contestation.objects.select_related('exclusion', 'requester', 'reviewed_by')
    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)

    if rank >= HIERARCHY_RANK['SUPERADMIN']:
        pass  # vê tudo
    elif rank >= HIERARCHY_RANK['ADMIN']:
        # Admin vê as de seus setores
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        upper_sectors = [s.strip().upper() for s in user_sectors if s]
        if upper_sectors:
            q = Q()
            for s in upper_sectors:
                q |= Q(exclusion__filial__iexact=s)
            qs = qs.filter(q)
        else:
            qs = qs.filter(requester=request.user)
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

    qs = Contestation.objects.select_related('exclusion', 'requester', 'reviewed_by')

    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)
    if rank < HIERARCHY_RANK['SUPERADMIN']:
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        upper_sectors = [s.strip().upper() for s in user_sectors if s]
        if upper_sectors:
            q = Q()
            for s in upper_sectors:
                q |= Q(exclusion__filial__iexact=s)
            qs = qs.filter(q)

    # Filtro de status
    status_filter = request.GET.get('status', 'pending')
    if status_filter == 'awaiting_manager':
        qs = qs.filter(status__in=['accepted', 'rejected'], confirmed_by__isnull=True)
    elif status_filter:
        qs = qs.filter(status=status_filter)
    
    # Filtro de loja/filial
    filial_filter = request.GET.get('filial', '').strip()
    if filial_filter:
        qs = qs.filter(exclusion__filial__iexact=filial_filter)
    
    # Get list of filiais for dropdown
    filiais = Contestation.objects.select_related('exclusion').values_list(
        'exclusion__filial', flat=True
    ).distinct().order_by('exclusion__filial')

    pending_count = Contestation.objects.filter(status='pending').count()
    confirmed_count = Contestation.objects.filter(status='confirmed', payment_status='pending_payment').count()
    awaiting_manager_count = Contestation.objects.filter(
        status__in=['accepted', 'rejected'], confirmed_by__isnull=True
    ).count()

    context = {
        'contestations': qs[:100],
        'status_filter': status_filter,
        'filial_filter': filial_filter,
        'filiais': filiais,
        'pending_count': pending_count,
        'confirmed_count': confirmed_count,
        'awaiting_manager_count': awaiting_manager_count,
    }
    return render(request, 'contestacao/manage_contestations.html', context)


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
    c.approve(request.user, notes)
    ContestationHistory.objects.create(
        contestation=c, action='approved', user=request.user, notes=notes,
    )
    messages.success(request, f'Contestação #{c.pk} aprovada! Aguardando confirmação do gerente.')
    return redirect('contestacao:manage_contestations')


@login_required
def reject_contestation(request, pk):
    """Gestor rejeita a contestação."""
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')
    c = get_object_or_404(Contestation, pk=pk, status='pending')
    notes = request.POST.get('review_notes', '')
    c.reject(request.user, notes)
    ContestationHistory.objects.create(
        contestation=c, action='rejected', user=request.user, notes=notes,
    )
    messages.success(request, f'Contestação #{c.pk} rejeitada! Aguardando de acordo do gerente.')
    return redirect('contestacao:manage_contestations')


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
    if not _can_create_contestations(request.user):
        messages.error(request, 'Sem permissão para acessar o dashboard.')
        return redirect('home')

    qs = Contestation.objects.select_related('exclusion')
    rank = HIERARCHY_RANK.get(request.user.hierarchy, 0)

    # Filtro por setor (mesma lógica do exclusion_list)
    if rank < HIERARCHY_RANK['SUPERADMIN']:
        user_sectors = list(request.user.sectors.values_list('name', flat=True))
        if request.user.sector:
            user_sectors.append(request.user.sector.name)
        upper_sectors = [s.strip().upper() for s in user_sectors if s]
        if upper_sectors:
            q = Q()
            for s in upper_sectors:
                q |= Q(exclusion__filial__iexact=s)
            qs = qs.filter(q)
        else:
            qs = qs.filter(requester=request.user)

    # Total na base (ExclusionRecord) com mesmo filtro de setor
    base_qs = ExclusionRecord.objects.all()
    if rank < HIERARCHY_RANK['SUPERADMIN']:
        if upper_sectors:
            bq = Q()
            for s in upper_sectors:
                bq |= Q(filial__iexact=s)
            base_qs = base_qs.filter(bq)
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
    }
    return render(request, 'contestacao/dashboard.html', context)
