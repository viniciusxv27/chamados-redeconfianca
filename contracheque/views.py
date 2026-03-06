from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, FileResponse
from django.utils import timezone
from django.db.models import Q

from io import BytesIO

from .models import Payslip, IncomeReport
from .pdf_parser import extract_payslip_data, extract_all_payslips, normalize_name, extract_single_page_pdf, extract_all_income_reports
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
    """Download/visualização do PDF do contracheque (somente a página do funcionário)."""
    payslip = get_object_or_404(Payslip, pk=pk)

    if payslip.user != request.user and not is_superadmin(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contracheque:my_payslips')

    from django.http import HttpResponse

    # Verificar se há arquivo vinculado
    if not payslip.pdf_file:
        messages.error(request, 'PDF não disponível para este contracheque.')
        return redirect('contracheque:payslip_detail', pk=payslip.pk)

    # Tentar ler o conteúdo do PDF (funciona tanto com S3 quanto filesystem local)
    pdf_content = None
    try:
        payslip.pdf_file.open('rb')
        pdf_content = payslip.pdf_file.read()
        payslip.pdf_file.close()
    except Exception:
        pass

    # Se não conseguiu ler o conteúdo, tentar redirecionar para a URL do arquivo
    if not pdf_content:
        try:
            file_url = payslip.pdf_file.url
            return redirect(file_url)
        except Exception:
            messages.error(request, 'Não foi possível acessar o arquivo PDF. O arquivo pode precisar ser reimportado.')
            return redirect('contracheque:payslip_detail', pk=payslip.pk)

    # Se o PDF armazenado tem pdf_page_number, pode ser um PDF multi-página antigo
    # Novos imports já salvam a página individual. Mas por segurança, verificamos:
    if payslip.pdf_page_number is not None:
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(pdf_content)
            num_pages = len(doc)
            doc.close()

            if num_pages > 1:
                single_page = extract_single_page_pdf(pdf_content, payslip.pdf_page_number)
                if single_page:
                    response = HttpResponse(single_page, content_type='application/pdf')
                    response['Content-Disposition'] = f'inline; filename="contracheque_{payslip.user.pk}_{payslip.year}_{payslip.month:02d}.pdf"'
                    return response
        except Exception:
            pass

    # Retornar o PDF inteiro (já é página individual ou fallback)
    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="contracheque_{payslip.user.pk}_{payslip.year}_{payslip.month:02d}.pdf"'
    return response


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


def _find_user_by_name(employee_name, users_cache):
    """
    Busca um usuário da plataforma que corresponda ao nome extraído do PDF.
    Usa múltiplas estratégias de matching (exact, contains, first+last word).
    """
    if not employee_name:
        return None

    emp_normalized = normalize_name(employee_name)
    emp_words = emp_normalized.split()

    if not emp_words:
        return None

    # Estratégia 1: Nome completo exato (normalizado)
    for user in users_cache:
        user_full = normalize_name(f"{user.first_name} {user.last_name}")
        if user_full == emp_normalized:
            return user

    # Estratégia 2: Nome completo do usuário contido no nome do PDF ou vice-versa
    for user in users_cache:
        user_full = normalize_name(f"{user.first_name} {user.last_name}")
        if user_full and (user_full in emp_normalized or emp_normalized in user_full):
            return user

    # Estratégia 3: Primeiro nome + último nome do PDF coincidem com first_name + last_name
    for user in users_cache:
        first = normalize_name(user.first_name)
        last = normalize_name(user.last_name)
        if first and last and len(emp_words) >= 2:
            if first == emp_words[0] and last == emp_words[-1]:
                return user

    # Estratégia 4: Primeiro nome coincide e último nome do user está entre as palavras do PDF
    for user in users_cache:
        first = normalize_name(user.first_name)
        last = normalize_name(user.last_name)
        if first and last:
            last_words = last.split()
            if first in emp_words and all(lw in emp_words for lw in last_words):
                return user

    # Estratégia 5: Todas as palavras do first_name + last_name estão no nome do PDF
    for user in users_cache:
        user_full = normalize_name(f"{user.first_name} {user.last_name}")
        user_words = user_full.split()
        if len(user_words) >= 2 and all(uw in emp_words for uw in user_words):
            return user

    return None


@login_required
def api_bulk_import(request):
    """API para importar múltiplos PDFs de uma vez.
    Suporta:
    - PDF único com múltiplos contracheques (1 por página)
    - Múltiplos PDFs individuais
    """
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

    # Pré-carregar todos os usuários ativos para matching eficiente
    users_cache = list(User.objects.filter(is_active=True))

    for pdf_file in files:
        # Extrair TODOS os contracheques do PDF (suporta multi-funcionário)
        pdf_bytes = pdf_file.read()
        pdf_file.seek(0)
        all_payslips = extract_all_payslips(BytesIO(pdf_bytes))

        # Se retornou erro global
        if len(all_payslips) == 1 and 'error' in all_payslips[0] and not all_payslips[0].get('employee_name'):
            results.append({
                'file': pdf_file.name,
                'status': 'error',
                'message': f'Erro ao processar PDF: {all_payslips[0]["error"]}',
            })
            continue

        # Se não encontrou nenhum contracheque, tentar com parser individual
        if not all_payslips:
            results.append({
                'file': pdf_file.name,
                'status': 'skip',
                'message': 'Nenhum contracheque encontrado no PDF.',
            })
            continue

        # Para cada contracheque encontrado no PDF
        for pdf_data in all_payslips:
            employee_name = pdf_data.get('employee_name', '')
            cpf = pdf_data.get('cpf', '')

            target_user = None

            # Tentar match por CPF primeiro
            if cpf:
                cpf_clean = cpf.replace('.', '').replace('-', '').replace('/', '').replace(' ', '')
                if cpf_clean:
                    target_user = User.objects.filter(cpf__icontains=cpf_clean).first()

            # Tentar match por nome usando múltiplas estratégias
            if not target_user and employee_name:
                target_user = _find_user_by_name(employee_name, users_cache)

            if not target_user:
                results.append({
                    'file': pdf_file.name,
                    'status': 'skip',
                    'message': f'Funcionário não encontrado (pulado): {employee_name or "Nome não identificado"}',
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

            # Limpar dados para salvar (remover campos internos)
            page_num = pdf_data.pop('_page_number', None)
            pdf_data_clean = {k: v for k, v in pdf_data.items() if k not in ('error',)}

            # Extrair página individual do PDF para este funcionário
            from django.core.files.base import ContentFile
            individual_pdf_name = f"contracheque_{target_user.pk}_{year}_{month:02d}.pdf"

            if len(all_payslips) == 1:
                # PDF individual, usar direto
                pdf_content = pdf_file
                saved_page_number = None
            elif page_num is not None:
                # Extrair apenas a página do funcionário
                single_page_bytes = extract_single_page_pdf(pdf_bytes, page_num)
                if single_page_bytes:
                    pdf_content = ContentFile(single_page_bytes, name=individual_pdf_name)
                    saved_page_number = page_num
                else:
                    # Fallback: salvar o PDF inteiro + número da página
                    pdf_content = ContentFile(pdf_bytes, name=individual_pdf_name)
                    saved_page_number = page_num
            else:
                pdf_content = ContentFile(pdf_bytes, name=individual_pdf_name)
                saved_page_number = None

            payslip = Payslip(
                user=target_user,
                month=month,
                year=year,
                pdf_file=pdf_content,
                pdf_page_number=saved_page_number,
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
    total_processed = sum(1 for r in results)
    return JsonResponse({
        'results': results,
        'total': total_processed,
        'success_count': success_count,
    })


@login_required
def api_sign_payslip(request, pk):
    """API para assinar digitalmente um contracheque."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    payslip = get_object_or_404(Payslip, pk=pk)

    # Somente o dono pode assinar
    if payslip.user != request.user:
        return JsonResponse({'error': 'Sem permissão para assinar este contracheque.'}, status=403)

    if payslip.is_signed:
        return JsonResponse({'error': 'Este contracheque já foi assinado.'}, status=409)

    import json
    import hashlib

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Dados inválidos.'}, status=400)

    signature_data = body.get('signature', '')
    if not signature_data:
        return JsonResponse({'error': 'Assinatura não fornecida.'}, status=400)

    # Validar que é uma imagem base64 válida (PNG)
    if not signature_data.startswith('data:image/png;base64,'):
        return JsonResponse({'error': 'Formato de assinatura inválido.'}, status=400)

    # Gerar hash de verificação (SHA-256 dos dados do contracheque + assinatura)
    hash_content = (
        f"{payslip.pk}|{payslip.user.pk}|{payslip.month}|{payslip.year}|"
        f"{payslip.net_pay}|{payslip.total_earnings}|{payslip.total_deductions}|"
        f"{signature_data[:100]}"
    )
    signature_hash = hashlib.sha256(hash_content.encode('utf-8')).hexdigest()

    # Capturar IP do cliente
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        client_ip = x_forwarded_for.split(',')[0].strip()
    else:
        client_ip = request.META.get('REMOTE_ADDR', '')

    user_agent = request.META.get('HTTP_USER_AGENT', '')

    # Salvar assinatura
    payslip.signed_at = timezone.now()
    payslip.signature_image = signature_data
    payslip.signature_ip = client_ip
    payslip.signature_user_agent = user_agent[:500]
    payslip.signature_hash = signature_hash
    payslip.save()

    return JsonResponse({
        'success': True,
        'message': 'Contracheque assinado com sucesso.',
        'signed_at': payslip.signed_at.strftime('%d/%m/%Y às %H:%M'),
        'hash': signature_hash[:16] + '...',
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


# ─── Excluir em lote por mês ─────────────────────────────────────────────────

@login_required
def api_bulk_delete(request):
    """Excluir todos os contracheques de um determinado mês/ano."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    if not is_superadmin(request.user):
        return JsonResponse({'error': 'Acesso restrito'}, status=403)

    month = request.POST.get('month')
    year = request.POST.get('year')

    if not month or not year:
        return JsonResponse({'error': 'Mês e ano são obrigatórios.'}, status=400)

    try:
        month = int(month)
        year = int(year)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Mês e ano inválidos.'}, status=400)

    payslips = Payslip.objects.filter(month=month, year=year)
    count = payslips.count()

    if count == 0:
        return JsonResponse({'error': f'Nenhum contracheque encontrado para {month:02d}/{year}.'}, status=404)

    # Excluir arquivos PDF
    for ps in payslips:
        try:
            ps.pdf_file.delete(save=False)
        except Exception:
            pass
    payslips.delete()

    month_name = dict(Payslip.MONTH_CHOICES).get(month, str(month))
    messages.success(request, f'{count} contracheques de {month_name}/{year} excluídos com sucesso.')
    return JsonResponse({'success': True, 'deleted': count, 'message': f'{count} contracheques excluídos.'})


# ─── Relatório de assinaturas ─────────────────────────────────────────────────

@login_required
def export_signature_report(request):
    """Exportar relatório CSV de assinaturas de contracheques para um mês/ano."""
    if not is_superadmin(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('contracheque:admin_payslips')

    import csv
    from django.http import HttpResponse

    month = request.GET.get('month')
    year = request.GET.get('year')

    if not month or not year:
        messages.error(request, 'Selecione mês e ano para exportar.')
        return redirect('contracheque:admin_payslips')

    try:
        month = int(month)
        year = int(year)
    except (ValueError, TypeError):
        messages.error(request, 'Mês e ano inválidos.')
        return redirect('contracheque:admin_payslips')

    month_name = dict(Payslip.MONTH_CHOICES).get(month, str(month))

    payslips = Payslip.objects.filter(month=month, year=year).select_related('user', 'user__sector')

    # Obter todos os usuários ativos para encontrar quem NÃO recebeu
    all_users = User.objects.filter(is_active=True).select_related('sector').order_by('first_name', 'last_name')
    users_with_payslip = set(payslips.values_list('user_id', flat=True))

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="relatorio_assinaturas_{year}_{month:02d}.csv"'
    response.write('\ufeff')

    writer = csv.writer(response, delimiter=';')

    # Resumo
    total_payslips = payslips.count()
    signed_count = payslips.filter(signed_at__isnull=False).count()
    unsigned_count = total_payslips - signed_count
    without_payslip = all_users.count() - len(users_with_payslip)

    writer.writerow([f'Relatório de Assinaturas – {month_name}/{year}'])
    writer.writerow([])
    writer.writerow(['Total de contracheques', total_payslips])
    writer.writerow(['Assinados', signed_count])
    writer.writerow(['Não assinados', unsigned_count])
    writer.writerow(['Usuários sem contracheque', without_payslip])
    writer.writerow([])

    # Setores faltantes (com pelo menos 1 não assinado)
    unsigned_payslips = payslips.filter(signed_at__isnull=True).select_related('user__sector')
    sectors_missing = set()
    for ps in unsigned_payslips:
        sector = ps.user.sector
        if sector:
            sectors_missing.add(sector.name)
    if sectors_missing:
        writer.writerow(['Setores com assinaturas pendentes', ', '.join(sorted(sectors_missing))])
    writer.writerow([])

    # Detalhes
    writer.writerow(['Funcionário', 'CPF', 'Setor', 'Status', 'Data Assinatura', 'IP', 'Hash'])

    for ps in payslips.order_by('user__first_name', 'user__last_name'):
        sector_name = ps.user.sector.name if ps.user.sector else ''
        if ps.is_signed:
            status = 'Assinado'
            signed_date = ps.signed_at.strftime('%d/%m/%Y %H:%M') if ps.signed_at else ''
        else:
            status = 'Não assinado'
            signed_date = ''

        writer.writerow([
            ps.user.full_name,
            ps.user.cpf or ps.cpf,
            sector_name,
            status,
            signed_date,
            ps.signature_ip or '',
            ps.signature_hash[:16] if ps.signature_hash else '',
        ])

    # Seção de usuários SEM contracheque
    users_without = all_users.exclude(id__in=users_with_payslip)
    if users_without.exists():
        writer.writerow([])
        writer.writerow(['--- Usuários ativos SEM contracheque ---'])
        writer.writerow(['Funcionário', 'CPF', 'Setor'])
        for u in users_without:
            sector_name = u.sector.name if u.sector else ''
            writer.writerow([u.full_name, u.cpf or '', sector_name])

    return response


# ─── Informe de Rendimentos ──────────────────────────────────────────────────

@login_required
def my_income_reports(request):
    """Lista de informes de rendimentos do usuário logado."""
    reports = IncomeReport.objects.filter(user=request.user)
    return render(request, 'contracheque/my_income_reports.html', {
        'reports': reports,
    })


@login_required
def income_report_detail(request, pk):
    """Detalhe de um informe de rendimentos."""
    report = get_object_or_404(IncomeReport, pk=pk)

    if report.user != request.user and not is_superadmin(request.user):
        messages.error(request, 'Sem permissão para visualizar este informe.')
        return redirect('contracheque:my_income_reports')

    return render(request, 'contracheque/income_report_detail.html', {
        'report': report,
    })


@login_required
def income_report_pdf(request, pk):
    """Download/visualização do PDF do informe de rendimentos."""
    report = get_object_or_404(IncomeReport, pk=pk)

    if report.user != request.user and not is_superadmin(request.user):
        messages.error(request, 'Sem permissão.')
        return redirect('contracheque:my_income_reports')

    from django.http import HttpResponse

    if not report.pdf_file:
        messages.error(request, 'PDF não disponível para este informe.')
        return redirect('contracheque:income_report_detail', pk=report.pk)

    pdf_content = None
    try:
        report.pdf_file.open('rb')
        pdf_content = report.pdf_file.read()
        report.pdf_file.close()
    except Exception:
        pass

    if not pdf_content:
        try:
            return redirect(report.pdf_file.url)
        except Exception:
            messages.error(request, 'Não foi possível acessar o arquivo PDF.')
            return redirect('contracheque:income_report_detail', pk=report.pk)

    # Se é multi-página, extrair apenas a página do funcionário
    if report.pdf_page_number is not None:
        try:
            import pypdfium2 as pdfium
            doc = pdfium.PdfDocument(pdf_content)
            if len(doc) > 1:
                single = extract_single_page_pdf(pdf_content, report.pdf_page_number)
                if single:
                    pdf_content = single
            doc.close()
        except Exception:
            pass

    response = HttpResponse(pdf_content, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="informe_{report.user.pk}_{report.base_year}.pdf"'
    return response


@login_required
def admin_income_reports(request):
    """Painel administrativo de informes de rendimentos."""
    if not is_superadmin(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('contracheque:my_income_reports')

    reports = IncomeReport.objects.select_related('user', 'uploaded_by').all()

    search = request.GET.get('q', '').strip()
    year_filter = request.GET.get('year')

    if search:
        reports = reports.filter(
            Q(user__first_name__icontains=search) |
            Q(user__last_name__icontains=search) |
            Q(employee_name__icontains=search) |
            Q(cpf__icontains=search)
        )
    if year_filter:
        reports = reports.filter(base_year=year_filter)

    years = IncomeReport.objects.values_list('base_year', flat=True).distinct().order_by('-base_year')

    return render(request, 'contracheque/admin_income_reports.html', {
        'reports': reports,
        'years': years,
        'search': search,
        'selected_year': year_filter,
    })


@login_required
def admin_income_import(request):
    """Página de importação de informes de rendimentos."""
    if not is_superadmin(request.user):
        messages.error(request, 'Acesso restrito.')
        return redirect('contracheque:my_income_reports')

    return render(request, 'contracheque/admin_income_import.html', {
        'current_year': timezone.now().year,
    })


@login_required
def api_bulk_import_income(request):
    """API para importar informes de rendimentos em lote (PDF multi-página)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    if not is_superadmin(request.user):
        return JsonResponse({'error': 'Acesso restrito'}, status=403)

    files = request.FILES.getlist('pdf_files')
    if not files:
        return JsonResponse({'error': 'Nenhum arquivo enviado.'}, status=400)

    users_cache = list(User.objects.filter(is_active=True))
    results = []

    for pdf_file in files:
        pdf_bytes = pdf_file.read()
        pdf_file.seek(0)

        all_reports = extract_all_income_reports(pdf_file)

        if not all_reports:
            results.append({'file': pdf_file.name, 'status': 'error', 'message': 'Nenhum informe encontrado no PDF.'})
            continue

        if len(all_reports) == 1 and 'error' in all_reports[0] and not all_reports[0].get('employee_name'):
            results.append({'file': pdf_file.name, 'status': 'error', 'message': all_reports[0].get('error', 'Erro ao processar')})
            continue

        for report_data in all_reports:
            if 'error' in report_data and not report_data.get('employee_name'):
                results.append({'file': pdf_file.name, 'status': 'error', 'message': report_data.get('error', 'Erro')})
                continue

            emp_name = report_data.get('employee_name', '')
            target_user = _find_user_by_name(emp_name, users_cache)

            if not target_user:
                results.append({
                    'file': pdf_file.name,
                    'status': 'skip',
                    'message': f'Funcionário não encontrado: {emp_name}',
                })
                continue

            base_year = report_data.get('base_year', timezone.now().year - 1)
            exercise_year = report_data.get('exercise_year', base_year + 1)

            existing = IncomeReport.objects.filter(user=target_user, base_year=base_year).first()
            if existing:
                results.append({
                    'file': pdf_file.name,
                    'status': 'skip',
                    'message': f'Informe {base_year} já existe para {target_user.full_name}',
                })
                continue

            page_num = report_data.get('_page_number')
            fields_to_save = {k: v for k, v in report_data.items() if not k.startswith('_') and k not in ('error',)}

            # Extrair página individual
            from django.core.files.base import ContentFile
            individual_pdf_name = f"informe_{target_user.pk}_{base_year}.pdf"

            if page_num is not None:
                single_page_bytes = extract_single_page_pdf(pdf_bytes, page_num)
                if single_page_bytes:
                    pdf_content = ContentFile(single_page_bytes, name=individual_pdf_name)
                else:
                    pdf_content = ContentFile(pdf_bytes, name=individual_pdf_name)
            else:
                pdf_content = ContentFile(pdf_bytes, name=individual_pdf_name)

            income_report = IncomeReport(
                user=target_user,
                base_year=base_year,
                exercise_year=exercise_year,
                pdf_file=pdf_content,
                pdf_page_number=page_num,
                uploaded_by=request.user,
                **fields_to_save,
            )
            income_report.save()

            results.append({
                'file': pdf_file.name,
                'status': 'success',
                'message': f'Importado para {target_user.full_name} (Ano {base_year})',
            })

    success_count = sum(1 for r in results if r['status'] == 'success')
    return JsonResponse({
        'results': results,
        'total': len(results),
        'success_count': success_count,
    })


@login_required
def admin_delete_income_report(request, pk):
    """Excluir um informe de rendimentos."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    if not is_superadmin(request.user):
        return JsonResponse({'error': 'Acesso restrito'}, status=403)

    report = get_object_or_404(IncomeReport, pk=pk)
    info = str(report)
    try:
        report.pdf_file.delete(save=False)
    except Exception:
        pass
    report.delete()

    messages.success(request, f'Informe excluído: {info}')
    return redirect('contracheque:admin_income_reports')


@login_required
def api_bulk_delete_income(request):
    """Excluir todos os informes de um determinado ano."""
    if request.method != 'POST':
        return JsonResponse({'error': 'Método não permitido'}, status=405)

    if not is_superadmin(request.user):
        return JsonResponse({'error': 'Acesso restrito'}, status=403)

    year = request.POST.get('year')
    if not year:
        return JsonResponse({'error': 'Ano é obrigatório.'}, status=400)

    try:
        year = int(year)
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Ano inválido.'}, status=400)

    reports = IncomeReport.objects.filter(base_year=year)
    count = reports.count()

    if count == 0:
        return JsonResponse({'error': f'Nenhum informe encontrado para {year}.'}, status=404)

    for r in reports:
        try:
            r.pdf_file.delete(save=False)
        except Exception:
            pass
    reports.delete()

    messages.success(request, f'{count} informes de {year} excluídos com sucesso.')
    return JsonResponse({'success': True, 'deleted': count})
