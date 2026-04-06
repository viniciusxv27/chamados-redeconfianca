from decimal import Decimal, InvalidOperation
import unicodedata
from collections import defaultdict

from django.db import transaction
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

import openpyxl

from .forms import GoalUploadForm, PowerBIReportForm
from .models import GoalEntry, GoalUpload, PowerBIReport


def _is_superadmin(user):
    return user.is_superuser or user.hierarchy == 'SUPERADMIN'


def _is_standard_user(user):
    return user.hierarchy == 'PADRAO' and not user.is_superuser


def _is_gerentes_group_user(user):
    if not _is_standard_user(user):
        return False
    return any(_normalize_text(group.name) == 'GERENTES' for group in user.groups.all())


def _get_user_store_candidates(user):
    candidates = set()
    if getattr(user, 'pdv', None):
        normalized_pdv = _normalize_text(user.pdv)
        if normalized_pdv:
            candidates.add(normalized_pdv)
    if getattr(user, 'sector', None) and getattr(user.sector, 'name', None):
        normalized_sector = _normalize_text(user.sector.name)
        if normalized_sector:
            candidates.add(normalized_sector)
    for sector in user.sectors.all():
        normalized_sector = _normalize_text(getattr(sector, 'name', ''))
        if normalized_sector:
            candidates.add(normalized_sector)
    return candidates


def _normalize_text(value):
    if value is None:
        return ''
    text = str(value).strip()
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return ' '.join(text.upper().split())


def _normalize_sheet_name(value):
    return _normalize_text(value).replace('_', '').replace(' ', '')


def _parse_decimal(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value)).quantize(Decimal('0.01'))
        except (InvalidOperation, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    text = text.replace('R$', '').replace(' ', '')
    if ',' in text and '.' in text:
        text = text.replace('.', '').replace(',', '.')
    else:
        text = text.replace(',', '.')

    try:
        return Decimal(text).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


PILAR_COLUMNS = [
    ('MOVEL', 'MOVEL'),
    ('FIXA', 'FIXA'),
    ('SVA', 'SVA'),
    ('SEGURO', 'SEGURO'),
    ('SMARTPHONE', 'SMARTPHONE'),
    ('ELETRONICOS', 'ELETRONICOS'),
    ('ESSENCIAIS', 'ESSENCIAIS'),
]


def _find_index_by_aliases(headers, aliases):
    normalized = [_normalize_text(h) for h in headers]
    for idx, header in enumerate(normalized):
        if any(alias in header for alias in aliases):
            return idx
    return None


def _extract_entries_cn_real(headers, rows):
    consultor_idx = _find_index_by_aliases(headers, ['CONSULTOR', 'CN', 'NOME'])
    pdv_idx = _find_index_by_aliases(headers, ['PDV', 'LOJA', 'FILIAL'])
    pilar_indexes = {}
    normalized_headers = [_normalize_text(h) for h in headers]

    for key, label in PILAR_COLUMNS:
        idx = None
        for header_idx, header in enumerate(normalized_headers):
            if key in header:
                idx = header_idx
                break
        pilar_indexes[label] = idx

    entries = []
    for row_number, row in enumerate(rows, start=2):
        if all(cell in (None, '') for cell in row):
            continue

        user_name = ''
        store_name = ''
        if consultor_idx is not None and consultor_idx < len(row):
            user_name = '' if row[consultor_idx] is None else str(row[consultor_idx]).strip()
        if pdv_idx is not None and pdv_idx < len(row):
            store_name = '' if row[pdv_idx] is None else str(row[pdv_idx]).strip()

        row_data = {}
        for col_idx, header in enumerate(headers):
            if not header:
                continue
            row_data[header] = row[col_idx] if col_idx < len(row) else None

        for pilar_name, idx in pilar_indexes.items():
            if idx is None or idx >= len(row):
                continue
            goal_value = _parse_decimal(row[idx])
            if goal_value is None:
                continue

            entries.append({
                'sheet_type': GoalEntry.SHEET_CN_REAL,
                'user_name': user_name,
                'store_name': store_name,
                'pilar': pilar_name,
                'goal_value': goal_value,
                'row_number': row_number,
                'row_data': row_data,
            })

    return entries


def _extract_entries_pdv_real(headers, rows):
    pdv_idx = _find_index_by_aliases(headers, ['PDV', 'LOJA', 'FILIAL'])
    pilar_indexes = {}
    normalized_headers = [_normalize_text(h) for h in headers]

    for key, label in PILAR_COLUMNS:
        idx = None
        for header_idx, header in enumerate(normalized_headers):
            if key in header:
                idx = header_idx
                break
        pilar_indexes[label] = idx

    entries = []
    for row_number, row in enumerate(rows, start=2):
        if all(cell in (None, '') for cell in row):
            continue

        store_name = ''
        if pdv_idx is not None and pdv_idx < len(row):
            store_name = '' if row[pdv_idx] is None else str(row[pdv_idx]).strip()

        row_data = {}
        for col_idx, header in enumerate(headers):
            if not header:
                continue
            row_data[header] = row[col_idx] if col_idx < len(row) else None

        for pilar_name, idx in pilar_indexes.items():
            if idx is None or idx >= len(row):
                continue
            goal_value = _parse_decimal(row[idx])
            if goal_value is None:
                continue

            entries.append({
                'sheet_type': GoalEntry.SHEET_PDV_REAL,
                'user_name': '',
                'store_name': store_name,
                'pilar': pilar_name,
                'goal_value': goal_value,
                'row_number': row_number,
                'row_data': row_data,
            })

    return entries


def _load_goal_entries_from_workbook(uploaded_file):
    workbook = openpyxl.load_workbook(uploaded_file, data_only=True)

    required = {
        'METASCNREAL': GoalEntry.SHEET_CN_REAL,
        'METAPDVREAL': GoalEntry.SHEET_PDV_REAL,
    }

    normalized_to_real = {
        _normalize_sheet_name(sheet_name): sheet_name for sheet_name in workbook.sheetnames
    }

    missing = [name for name in required.keys() if name not in normalized_to_real]
    if missing:
        raise ValueError('As sheets obrigatorias METAS  CN REAL e META PDV REAL nao foram encontradas.')

    all_entries = []

    cn_sheet = workbook[normalized_to_real['METASCNREAL']]
    cn_rows = list(cn_sheet.iter_rows(values_only=True))
    if cn_rows:
        cn_headers = [str(cell).strip() if cell is not None else '' for cell in cn_rows[0]]
        all_entries.extend(_extract_entries_cn_real(cn_headers, cn_rows[1:]))

    pdv_sheet = workbook[normalized_to_real['METAPDVREAL']]
    pdv_rows = list(pdv_sheet.iter_rows(values_only=True))
    if pdv_rows:
        pdv_headers = [str(cell).strip() if cell is not None else '' for cell in pdv_rows[0]]
        all_entries.extend(_extract_entries_pdv_real(pdv_headers, pdv_rows[1:]))

    return all_entries


def _visible_reports_for(user):
    reports = (
        PowerBIReport.objects.filter(is_active=True)
        .prefetch_related('allowed_groups', 'allowed_sectors', 'allowed_users')
        .order_by('sort_order', 'name')
    )
    if _is_superadmin(user):
        return reports
    return [report for report in reports if report.is_visible_to(user)]


@login_required
def power_bi_list_view(request):
    reports = _visible_reports_for(request.user)
    return render(
        request,
        'power_bi/list.html',
        {
            'reports': reports,
        }
    )


@login_required
def power_bi_viewer(request, report_id):
    report = get_object_or_404(
        PowerBIReport.objects.prefetch_related('allowed_groups', 'allowed_sectors', 'allowed_users'),
        id=report_id,
        is_active=True,
    )

    if not report.is_visible_to(request.user):
        messages.error(request, 'Voce nao tem permissao para visualizar este BI.')
        return redirect('power_bi:list')

    return render(
        request,
        'power_bi/viewer.html',
        {
            'report': report,
        }
    )


@login_required
def manage_power_bi_view(request):
    if not _is_superadmin(request.user):
        messages.error(request, 'Apenas SUPERADMIN pode gerenciar os links de Power BI.')
        return redirect('dashboard')

    reports = PowerBIReport.objects.all().prefetch_related('allowed_groups', 'allowed_sectors', 'allowed_users')
    form = PowerBIReportForm()

    return render(
        request,
        'power_bi/manage.html',
        {
            'reports': reports,
            'form': form,
            'editing_report': None,
        }
    )


@login_required
def create_power_bi_view(request):
    if not _is_superadmin(request.user):
        messages.error(request, 'Apenas SUPERADMIN pode gerenciar os links de Power BI.')
        return redirect('dashboard')

    if request.method != 'POST':
        return redirect('power_bi:manage')

    form = PowerBIReportForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, 'BI criado com sucesso.')
        return redirect('power_bi:manage')

    reports = PowerBIReport.objects.all().prefetch_related('allowed_groups', 'allowed_sectors', 'allowed_users')
    return render(
        request,
        'power_bi/manage.html',
        {
            'reports': reports,
            'form': form,
            'editing_report': None,
        }
    )


@login_required
def edit_power_bi_view(request, report_id):
    if not _is_superadmin(request.user):
        messages.error(request, 'Apenas SUPERADMIN pode gerenciar os links de Power BI.')
        return redirect('dashboard')

    report = get_object_or_404(PowerBIReport, id=report_id)

    if request.method == 'POST':
        form = PowerBIReportForm(request.POST, instance=report)
        if form.is_valid():
            form.save()
            messages.success(request, 'BI atualizado com sucesso.')
            return redirect('power_bi:manage')
    else:
        form = PowerBIReportForm(instance=report)

    reports = PowerBIReport.objects.all().prefetch_related('allowed_groups', 'allowed_sectors', 'allowed_users')
    return render(
        request,
        'power_bi/manage.html',
        {
            'reports': reports,
            'form': form,
            'editing_report': report,
        }
    )


@login_required
def delete_power_bi_view(request, report_id):
    if not _is_superadmin(request.user):
        messages.error(request, 'Apenas SUPERADMIN pode gerenciar os links de Power BI.')
        return redirect('dashboard')

    if request.method != 'POST':
        return redirect('power_bi:manage')

    report = get_object_or_404(PowerBIReport, id=report_id)
    report.delete()
    messages.success(request, 'BI removido com sucesso.')
    return redirect('power_bi:manage')


@login_required
def goals_list_view(request):
    is_standard_user = _is_standard_user(request.user)
    is_gerente_user = _is_gerentes_group_user(request.user)
    is_cn_user = is_standard_user and not is_gerente_user

    selected_year = request.GET.get('year')
    selected_month = request.GET.get('month')

    uploads = GoalUpload.objects.order_by('-year', '-month', '-updated_at')
    current_upload = None

    # CN padrao nao pode aplicar filtros globais; gerente e niveis acima podem escolher competencia.
    if not is_cn_user and selected_year and selected_month:
        current_upload = uploads.filter(year=selected_year, month=selected_month).first()
    if current_upload is None:
        current_upload = uploads.first()

    entries = GoalEntry.objects.none()
    selected_store = '' if (is_standard_user or is_gerente_user) else request.GET.get('store', '').strip()
    selected_pilar = '' if (is_standard_user or is_gerente_user) else request.GET.get('pilar', '').strip()
    selected_cn = request.GET.get('cn', '').strip() if is_gerente_user else ''

    if current_upload:
        entries = GoalEntry.objects.filter(upload=current_upload).order_by('sheet_type', 'store_name', 'pilar', 'user_name')

        if is_cn_user:
            entries = entries.filter(sheet_type=GoalEntry.SHEET_CN_REAL)
            user_tokens = {
                _normalize_text(request.user.full_name),
                _normalize_text(request.user.get_full_name()),
                _normalize_text(request.user.first_name),
                _normalize_text(request.user.last_name),
                _normalize_text(request.user.username),
            }
            user_tokens = {token for token in user_tokens if token}

            filtered_ids = []
            for entry in entries:
                entry_name = _normalize_text(entry.user_name)
                if not entry_name:
                    continue
                if any(token == entry_name for token in user_tokens):
                    filtered_ids.append(entry.id)
            entries = entries.filter(id__in=filtered_ids)
        elif is_gerente_user:
            manager_store_candidates = _get_user_store_candidates(request.user)
            manager_entry_ids = []
            for entry in entries:
                entry_store = _normalize_text(entry.store_name)
                if entry_store and entry_store in manager_store_candidates:
                    manager_entry_ids.append(entry.id)
            entries = entries.filter(id__in=manager_entry_ids)
        else:
            if selected_store:
                store_target = _normalize_text(selected_store)
                entry_ids = [
                    entry.id for entry in entries
                    if _normalize_text(entry.store_name) == store_target
                ]
                entries = entries.filter(id__in=entry_ids)

            if selected_pilar:
                pilar_target = _normalize_text(selected_pilar)
                entry_ids = [
                    entry.id for entry in entries
                    if _normalize_text(entry.pilar) == pilar_target
                ]
                entries = entries.filter(id__in=entry_ids)

    cn_entries = entries.filter(sheet_type=GoalEntry.SHEET_CN_REAL)
    pdv_entries = entries.filter(sheet_type=GoalEntry.SHEET_PDV_REAL) if (is_gerente_user or not is_standard_user) else GoalEntry.objects.none()

    available_cns = []
    if is_gerente_user:
        available_cns = sorted({entry.user_name for entry in cn_entries if entry.user_name})
        if available_cns and selected_cn not in available_cns:
            selected_cn = available_cns[0]
        if selected_cn:
            selected_cn_normalized = _normalize_text(selected_cn)
            cn_ids = [
                entry.id for entry in cn_entries
                if _normalize_text(entry.user_name) == selected_cn_normalized
            ]
            cn_entries = cn_entries.filter(id__in=cn_ids)

    all_stores = []
    all_pilares = []
    if current_upload and not (is_standard_user or is_gerente_user):
        all_entries = GoalEntry.objects.filter(upload=current_upload)
        all_stores = sorted({entry.store_name for entry in all_entries if entry.store_name})
        all_pilares = sorted({entry.pilar for entry in all_entries if entry.pilar})

    cn_total_value = sum((item.goal_value or Decimal('0.00')) for item in cn_entries)
    pdv_total_value = sum((item.goal_value or Decimal('0.00')) for item in pdv_entries)
    # PDV representa o total da loja; quando existir, evita duplicidade com soma de CN.
    total_value = pdv_total_value if pdv_total_value > Decimal('0.00') else cn_total_value

    pilar_cn_map = defaultdict(lambda: Decimal('0.00'))
    pilar_pdv_map = defaultdict(lambda: Decimal('0.00'))
    for item in cn_entries:
        if item.goal_value is not None:
            pilar_cn_map[item.pilar or 'SEM PILAR'] += item.goal_value
    for item in pdv_entries:
        if item.goal_value is not None:
            pilar_pdv_map[item.pilar or 'SEM PILAR'] += item.goal_value

    cn_pilar_rows = []
    cn_max_total = max(pilar_cn_map.values()) if pilar_cn_map else Decimal('0.00')
    cn_max_total = cn_max_total if cn_max_total > Decimal('0.00') else Decimal('1.00')
    for pilar in sorted(pilar_cn_map.keys()):
        total = pilar_cn_map[pilar]
        cn_pilar_rows.append({
            'pilar': pilar,
            'total': total,
            'bar_percent': float((total / cn_max_total) * Decimal('100')),
        })

    pdv_pilar_rows = []
    pdv_max_total = max(pilar_pdv_map.values()) if pilar_pdv_map else Decimal('0.00')
    pdv_max_total = pdv_max_total if pdv_max_total > Decimal('0.00') else Decimal('1.00')
    for pilar in sorted(pilar_pdv_map.keys()):
        total = pilar_pdv_map[pilar]
        pdv_pilar_rows.append({
            'pilar': pilar,
            'total': total,
            'bar_percent': float((total / pdv_max_total) * Decimal('100')),
        })

    consultant_totals = defaultdict(lambda: Decimal('0.00'))
    for item in cn_entries:
        if item.goal_value is None:
            continue
        consultant_key = item.user_name or 'SEM CONSULTOR'
        consultant_totals[consultant_key] += item.goal_value
    top_consultants = sorted(
        [{'name': key, 'total': value} for key, value in consultant_totals.items()],
        key=lambda x: x['total'],
        reverse=True
    )[:10]

    store_totals = defaultdict(lambda: Decimal('0.00'))
    if is_gerente_user or not is_standard_user:
        for item in pdv_entries:
            if item.goal_value is None:
                continue
            store_key = item.store_name or 'SEM LOJA'
            store_totals[store_key] += item.goal_value
    top_stores = sorted(
        [{'store': key, 'total': value} for key, value in store_totals.items()],
        key=lambda x: x['total'],
        reverse=True
    )[:10]

    context = {
        'uploads': uploads[:24],
        'current_upload': current_upload,
        'selected_store': selected_store,
        'selected_pilar': selected_pilar,
        'all_stores': all_stores,
        'all_pilares': all_pilares,
        'is_standard_user': is_standard_user,
        'is_gerente_user': is_gerente_user,
        'is_cn_user': is_cn_user,
        'selected_cn': selected_cn,
        'available_cns': available_cns,
        'total_value': total_value,
        'entries_count': entries.count() if hasattr(entries, 'count') else len(entries),
        'cn_total': cn_total_value,
        'pdv_total': pdv_total_value,
        'cn_entries_count': cn_entries.count(),
        'pdv_entries_count': pdv_entries.count() if (is_gerente_user or not is_standard_user) else 0,
        'cn_pilar_rows': cn_pilar_rows,
        'pdv_pilar_rows': pdv_pilar_rows,
        'top_consultants': top_consultants,
        'top_stores': top_stores,
        'now': timezone.now(),
    }
    return render(request, 'power_bi/goals_list.html', context)


@login_required
def manage_goals_view(request):
    if not _is_superadmin(request.user):
        messages.error(request, 'Apenas SUPERADMIN pode gerenciar metas.')
        return redirect('dashboard')

    form = GoalUploadForm()
    uploads = GoalUpload.objects.order_by('-year', '-month', '-updated_at')

    return render(
        request,
        'power_bi/manage_goals.html',
        {
            'form': form,
            'uploads': uploads,
        }
    )


@login_required
def upload_goals_view(request):
    if not _is_superadmin(request.user):
        messages.error(request, 'Apenas SUPERADMIN pode gerenciar metas.')
        return redirect('dashboard')

    if request.method != 'POST':
        return redirect('power_bi:manage_goals')

    form = GoalUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        uploads = GoalUpload.objects.order_by('-year', '-month', '-updated_at')
        return render(
            request,
            'power_bi/manage_goals.html',
            {
                'form': form,
                'uploads': uploads,
            }
        )

    uploaded_file = form.cleaned_data['file']
    year = form.cleaned_data['year']
    month = form.cleaned_data['month']

    try:
        uploaded_file.seek(0)
        parsed_entries = _load_goal_entries_from_workbook(uploaded_file)
    except Exception as exc:
        messages.error(request, f'Erro ao processar planilha de metas: {exc}')
        uploads = GoalUpload.objects.order_by('-year', '-month', '-updated_at')
        return render(
            request,
            'power_bi/manage_goals.html',
            {
                'form': form,
                'uploads': uploads,
            }
        )

    with transaction.atomic():
        upload, _ = GoalUpload.objects.get_or_create(year=year, month=month)
        upload.source_file_name = uploaded_file.name
        upload.uploaded_by = request.user
        upload.save()

        upload.entries.all().delete()
        GoalEntry.objects.bulk_create([
            GoalEntry(upload=upload, **entry) for entry in parsed_entries
        ])

    messages.success(request, f'Metas de {month:02d}/{year} importadas com sucesso ({len(parsed_entries)} linhas).')
    return redirect('power_bi:manage_goals')


@login_required
def delete_goals_upload_view(request, upload_id):
    if not _is_superadmin(request.user):
        messages.error(request, 'Apenas SUPERADMIN pode gerenciar metas.')
        return redirect('dashboard')

    if request.method != 'POST':
        return redirect('power_bi:manage_goals')

    upload = get_object_or_404(GoalUpload, id=upload_id)
    period = f'{upload.month:02d}/{upload.year}'
    upload.delete()
    messages.success(request, f'Competencia {period} excluida com sucesso.')
    return redirect('power_bi:manage_goals')
