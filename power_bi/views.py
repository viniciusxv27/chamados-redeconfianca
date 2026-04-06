from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import PowerBIReportForm
from .models import PowerBIReport


def _is_superadmin(user):
    return user.is_superuser or user.hierarchy == 'SUPERADMIN'


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
