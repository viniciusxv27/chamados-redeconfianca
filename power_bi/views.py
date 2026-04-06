from decimal import Decimal, InvalidOperation
import unicodedata

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


def _header_aliases():
    return {
        'user_name': ['NOME', 'NOME CN', 'CONSULTOR', 'VENDEDOR', 'COLABORADOR', 'CN'],
        'store_name': ['LOJA', 'FILIAL', 'PDV', 'PONTO DE VENDA'],
        'pilar': ['PILAR', 'PRODUTO', 'CATEGORIA'],
        'goal_value': ['META', 'META REAL', 'VALOR META', 'META R$', 'OBJETIVO'],
    }


def _resolve_column_indexes(headers):
    aliases = _header_aliases()
    normalized = [_normalize_text(h) for h in headers]
    indexes = {}

    for target, words in aliases.items():
        target_idx = None
        for idx, header in enumerate(normalized):
            if not header:
                continue
            if any(word in header for word in words):
                target_idx = idx
                break
        indexes[target] = target_idx

    return indexes


def _extract_sheet_entries(worksheet, sheet_type):
    rows = list(worksheet.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(cell).strip() if cell is not None else '' for cell in rows[0]]
    indexes = _resolve_column_indexes(headers)
    entries = []

    for row_number, row in enumerate(rows[1:], start=2):
        if all(cell in (None, '') for cell in row):
            continue

        row_data = {}
        for col_idx, header in enumerate(headers):
            if not header:
                continue
            value = row[col_idx] if col_idx < len(row) else None
            row_data[header] = value

        def get_value(key):
            idx = indexes.get(key)
            if idx is None or idx >= len(row):
                return ''
            value = row[idx]
            return '' if value is None else str(value).strip()

        goal_value = None
        value_idx = indexes.get('goal_value')
        if value_idx is not None and value_idx < len(row):
            goal_value = _parse_decimal(row[value_idx])

        if not row_data:
            continue

        entries.append({
            'sheet_type': sheet_type,
            'user_name': get_value('user_name'),
            'store_name': get_value('store_name'),
            'pilar': get_value('pilar'),
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
    for normalized_sheet, sheet_type in required.items():
        real_name = normalized_to_real[normalized_sheet]
        worksheet = workbook[real_name]
        all_entries.extend(_extract_sheet_entries(worksheet, sheet_type))

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
    selected_year = request.GET.get('year')
    selected_month = request.GET.get('month')

    uploads = GoalUpload.objects.order_by('-year', '-month', '-updated_at')
    current_upload = None

    if selected_year and selected_month:
        current_upload = uploads.filter(year=selected_year, month=selected_month).first()
    if current_upload is None:
        current_upload = uploads.first()

    entries = GoalEntry.objects.none()
    selected_store = request.GET.get('store', '').strip()
    selected_pilar = request.GET.get('pilar', '').strip()

    if current_upload:
        entries = GoalEntry.objects.filter(upload=current_upload).order_by('sheet_type', 'store_name', 'pilar', 'user_name')

        if _is_standard_user(request.user):
            user_tokens = {
                _normalize_text(request.user.full_name),
                _normalize_text(request.user.get_full_name()),
                _normalize_text(request.user.first_name),
                _normalize_text(request.user.username),
            }
            user_tokens = {token for token in user_tokens if token}

            filtered_ids = []
            for entry in entries:
                entry_name = _normalize_text(entry.user_name)
                if not entry_name:
                    continue
                if any(token in entry_name or entry_name in token for token in user_tokens):
                    filtered_ids.append(entry.id)
            entries = entries.filter(id__in=filtered_ids)
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

    all_stores = []
    all_pilares = []
    if current_upload and not _is_standard_user(request.user):
        all_entries = GoalEntry.objects.filter(upload=current_upload)
        all_stores = sorted({entry.store_name for entry in all_entries if entry.store_name})
        all_pilares = sorted({entry.pilar for entry in all_entries if entry.pilar})

    total_value = Decimal('0.00')
    for item in entries:
        if item.goal_value is not None:
            total_value += item.goal_value

    context = {
        'uploads': uploads[:24],
        'current_upload': current_upload,
        'entries': entries,
        'selected_store': selected_store,
        'selected_pilar': selected_pilar,
        'all_stores': all_stores,
        'all_pilares': all_pilares,
        'is_standard_user': _is_standard_user(request.user),
        'total_value': total_value,
        'entries_count': entries.count() if hasattr(entries, 'count') else len(entries),
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
