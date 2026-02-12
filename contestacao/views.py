import io
import pandas as pd
import requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.cache import cache
from django.http import JsonResponse
from django.db.models import Q, Sum
from django.utils import timezone

from .models import ExclusionRecord, Contestation
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

    if not filial_col or not vendedor_col or not receita_col or not pilar_col:
        messages.error(request, 'Colunas obrigatórias não encontradas na planilha (FILIAL, VENDEDOR, RECEITA, PILAR).')
        return redirect('contestacao:exclusion_list')

    # Limpar registros antigos e reimportar
    ExclusionRecord.objects.all().delete()

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
        ))

    ExclusionRecord.objects.bulk_create(records, batch_size=500)
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
    if search:
        qs = qs.filter(Q(vendedor__icontains=search) | Q(nome_cliente__icontains=search) | Q(cpf_cnpj__icontains=search))
    if pilar:
        qs = qs.filter(pilar__iexact=pilar)

    pilares = ExclusionRecord.objects.values_list('pilar', flat=True).distinct().order_by('pilar')

    # IDs que já têm contestação pendente
    contested_ids = set(
        Contestation.objects.filter(status='pending').values_list('exclusion_id', flat=True)
    )

    total_records = qs.count()
    total_receita = qs.aggregate(total=Sum('receita'))['total'] or 0

    context = {
        'records': qs[:200],
        'pilares': pilares,
        'search': search,
        'pilar_filter': pilar,
        'total_records': total_records,
        'total_receita': total_receita,
        'contested_ids': contested_ids,
        'can_manage': _can_manage_contestations(request.user),
        'can_sync': _can_create_contestations(request.user),
    }
    return render(request, 'contestacao/exclusion_list.html', context)


@login_required
def create_contestation(request, exclusion_id):
    """Cria uma nova contestação para um registro de exclusão."""
    if not _can_create_contestations(request.user):
        messages.error(request, 'Sem permissão para contestar.')
        return redirect('contestacao:exclusion_list')

    exclusion = get_object_or_404(ExclusionRecord, pk=exclusion_id)

    # Verificar se já existe contestação pendente
    existing = Contestation.objects.filter(exclusion=exclusion, status='pending').first()
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
            messages.success(request, f'Contestação #{c.pk} criada com sucesso!')
            return redirect('contestacao:my_contestations')

    return render(request, 'contestacao/create_contestation.html', {'exclusion': exclusion})


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
    if status_filter:
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

    status_filter = request.GET.get('status', 'pending')
    if status_filter:
        qs = qs.filter(status=status_filter)

    pending_count = Contestation.objects.filter(status='pending').count()

    context = {
        'contestations': qs[:100],
        'status_filter': status_filter,
        'pending_count': pending_count,
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
def accept_contestation(request, pk):
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')
    c = get_object_or_404(Contestation, pk=pk, status='pending')
    notes = request.POST.get('review_notes', '')
    c.accept(request.user, notes)
    messages.success(request, f'Contestação #{c.pk} aceita!')
    return redirect('contestacao:manage_contestations')


@login_required
def reject_contestation(request, pk):
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')
    c = get_object_or_404(Contestation, pk=pk, status='pending')
    notes = request.POST.get('review_notes', '')
    c.reject(request.user, notes)
    messages.success(request, f'Contestação #{c.pk} rejeitada.')
    return redirect('contestacao:manage_contestations')


@login_required
def mark_paid(request, pk):
    if not _can_manage_contestations(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contestacao:manage_contestations')
    c = get_object_or_404(Contestation, pk=pk, status='accepted')
    c.mark_paid(request.user)
    messages.success(request, f'Contestação #{c.pk} marcada como paga!')
    return redirect('contestacao:manage_contestations')
