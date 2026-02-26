from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, FileResponse
from django.utils import timezone
from django.db.models import Q

from .models import Payslip
from .pdf_parser import extract_payslip_data
from users.models import User

HIERARCHY_RANK = {
    'PADRAO': 0,
    'ADMINISTRATIVO': 1,
    'SUPERVISOR': 2,
    'ADMIN': 3,
    'SUPERADMIN': 4,
}


def is_superadmin(user):
    return HIERARCHY_RANK.get(user.hierarchy, 0) >= HIERARCHY_RANK['SUPERADMIN']


# ─── Área Pessoal ────────────────────────────────────────────────────────────

@login_required
def my_payslips(request):
    """Lista de contracheques do usuário logado."""
    payslips = Payslip.objects.filter(user=request.user)

    year_filter = request.GET.get('year')
    if year_filter:
        payslips = payslips.filter(year=year_filter)

    years = (
        Payslip.objects.filter(user=request.user)
        .values_list('year', flat=True)
        .distinct()
        .order_by('-year')
    )

    return render(request, 'contracheque/my_payslips.html', {
        'payslips': payslips,
        'years': years,
        'selected_year': year_filter,
    })


@login_required
def payslip_detail(request, pk):
    """Detalhe de um contracheque."""
    payslip = get_object_or_404(Payslip, pk=pk)

    # Verificar permissão: dono ou superadmin
    if payslip.user != request.user and not is_superadmin(request.user):
        messages.error(request, 'Sem permissão para visualizar este contracheque.')
        return redirect('contracheque:my_payslips')

    return render(request, 'contracheque/payslip_detail.html', {
        'payslip': payslip,
    })


@login_required
def payslip_pdf(request, pk):
    """Download/visualização do PDF do contracheque."""
    payslip = get_object_or_404(Payslip, pk=pk)

    if payslip.user != request.user and not is_superadmin(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contracheque:my_payslips')

    return FileResponse(payslip.pdf_file.open('rb'), content_type='application/pdf')


# ─── Área Administrativa (SUPERADMIN) ────────────────────────────────────────

@login_required
def admin_payslips(request):
    """Painel administrativo de contracheques (SUPERADMIN)."""
    if not is_superadmin(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('contracheque:my_payslips')

    payslips = Payslip.objects.select_related('user', 'uploaded_by').all()

    search = request.GET.get('q', '').strip()
    year_filter = request.GET.get('year')
    month_filter = request.GET.get('month')

    if search:
        payslips = payslips.filter(
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) |
            Q(employee_name__icontains=search) |
            Q(cpf__icontains=search)
        )
    if year_filter:
        payslips = payslips.filter(year=year_filter)
    if month_filter:
        payslips = payslips.filter(month=month_filter)

    years = Payslip.objects.values_list('year', flat=True).distinct().order_by('-year')

    return render(request, 'contracheque/admin_payslips.html', {
        'payslips': payslips,
        'years': years,
        'search': search,
        'selected_year': year_filter,
        'selected_month': month_filter,
        'month_choices': Payslip.MONTH_CHOICES,
    })


@login_required
def admin_import(request):
    """Página de importação de PDFs de contracheque (SUPERADMIN)."""
    if not is_superadmin(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('contracheque:my_payslips')

    users = User.objects.filter(is_active=True).order_by('first_name', 'last_name')
    current_year = timezone.now().year

    return render(request, 'contracheque/admin_import.html', {
        'users': users,
        'month_choices': Payslip.MONTH_CHOICES,
        'current_year': current_year,
        'year_range': range(current_year - 2, current_year + 2),
    })


@login_required
def api_import_payslip(request):
    """API para importar um contracheque (POST com PDF)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    if not is_superadmin(request.user):
        return JsonResponse({'error': 'Acesso restrito'}, status=403)

    user_id = request.POST.get('user_id')
    month = request.POST.get('month')
    year = request.POST.get('year')
    pdf_file = request.FILES.get('pdf_file')

    if not all([user_id, month, year, pdf_file]):
        return JsonResponse({'error': 'Preencha todos os campos obrigatórios.'}, status=400)

    try:
        target_user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'Usuário não encontrado.'}, status=404)

    month = int(month)
    year = int(year)

    # Verificar duplicidade
    existing = Payslip.objects.filter(user=target_user, month=month, year=year).first()
    if existing:
        return JsonResponse({
            'error': f'Já existe contracheque para {target_user.full_name} em {dict(Payslip.MONTH_CHOICES)[month]}/{year}. Delete o existente antes de reimportar.'
        }, status=409)

    # Extrair dados do PDF
    pdf_data = extract_payslip_data(pdf_file)
    if 'error' in pdf_data:
        # Cria mesmo assim, mas sem dados extraídos
        pdf_data_clean = {}
    else:
        pdf_data_clean = {k: v for k, v in pdf_data.items() if k not in ('error',)}

    payslip = Payslip(
        user=target_user,
        month=month,
        year=year,
        pdf_file=pdf_file,
        uploaded_by=request.user,
        **pdf_data_clean,
    )
    payslip.save()

    return JsonResponse({
        'success': True,
        'message': f'Contracheque importado para {target_user.full_name} – {payslip.period_display}',
        'payslip_id': payslip.pk,
        'extracted': 'error' not in pdf_data,
    })


@login_required
def api_bulk_import(request):
    """API para importar múltiplos PDFs de uma vez."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    if not is_superadmin(request.user):
        return JsonResponse({'error': 'Acesso restrito'}, status=403)

    month = request.POST.get('month')
    year = request.POST.get('year')
    files = request.FILES.getlist('pdf_files')

    if not month or not year or not files:
        return JsonResponse({'error': 'Informe mês, ano e selecione os arquivos.'}, status=400)

    month = int(month)
    year = int(year)
    results = []

    for pdf_file in files:
        # Tentar encontrar usuário pelo nome do arquivo ou conteúdo do PDF
        pdf_data = extract_payslip_data(pdf_file)
        employee_name = pdf_data.get('employee_name', '')
        cpf = pdf_data.get('cpf', '')

        target_user = None

        # Tentar match por CPF
        if cpf:
            cpf_clean = cpf.replace('.', '').replace('-', '').replace('/', '')
            target_user = User.objects.filter(cpf__icontains=cpf_clean).first()

        # Tentar match por nome
        if not target_user and employee_name:
            name_parts = employee_name.split()
            if len(name_parts) >= 2:
                target_user = User.objects.filter(
                    first_name__icontains=name_parts[0],
                    last_name__icontains=name_parts[-1],
                ).first()

        if not target_user:
            results.append({
                'file': pdf_file.name,
                'status': 'error',
                'message': f'Funcionário não encontrado: {employee_name or "Nome não identificado"} (CPF: {cpf or "N/A"})',
            })
            continue

        # Verificar duplicidade
        if Payslip.objects.filter(user=target_user, month=month, year=year).exists():
            results.append({
                'file': pdf_file.name,
                'status': 'skip',
                'message': f'Já existe para {target_user.full_name}',
            })
            continue

        pdf_data_clean = {k: v for k, v in pdf_data.items() if k not in ('error',)}
        payslip = Payslip(
            user=target_user,
            month=month,
            year=year,
            pdf_file=pdf_file,
            uploaded_by=request.user,
            **pdf_data_clean,
        )
        payslip.save()

        results.append({
            'file': pdf_file.name,
            'status': 'success',
            'message': f'Importado para {target_user.full_name}',
        })

    success_count = sum(1 for r in results if r['status'] == 'success')
    return JsonResponse({
        'results': results,
        'total': len(files),
        'success_count': success_count,
    })


@login_required
def admin_delete_payslip(request, pk):
    """Excluir um contracheque (SUPERADMIN)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    if not is_superadmin(request.user):
        return JsonResponse({'error': 'Acesso restrito'}, status=403)

    payslip = get_object_or_404(Payslip, pk=pk)
    info = str(payslip)
    payslip.pdf_file.delete(save=False)
    payslip.delete()

    messages.success(request, f'Contracheque excluído: {info}')
    return redirect('contracheque:admin_payslips')
