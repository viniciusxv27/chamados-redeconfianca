import csv
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import models
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_POST

from users.models import Sector, User

from .models import (
    LimpezaAnswer,
    LimpezaQuestion,
    LimpezaTemplate,
    LimpezaTodo,
)


OPCOES_RESPOSTA = [
    ('sim', 'Sim', 'peer-checked:bg-green-600'),
    ('nao', 'Não', 'peer-checked:bg-red-600'),
    ('nao_se_aplica', 'Não se aplica', 'peer-checked:bg-gray-500'),
]


def _is_gerente_or_superadmin(user):
    """Verifica se o usuário é gerente (no grupo 'Gerentes') ou superadmin."""
    if user.hierarchy == 'SUPERADMIN' or user.hierarchy == 'ADMINISTRATIVO' or user.is_superuser:
        return True
    return (
        user.groups.filter(name='Gerentes').exists()
        or user.groups.filter(name__iexact='ADMINS').exists()
    )


def _is_superadmin(user):
    return user.hierarchy == 'SUPERADMIN' or user.is_superuser


def _get_user_sectors(user):
    """Retorna os setores do usuário."""
    sectors = list(user.sectors.all())
    if user.sector and user.sector not in sectors:
        sectors.append(user.sector)
    return sectors


# ─── Dashboard ────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    """Lista as limpezas já feitas e dá o atalho para registrar uma nova.

    Sem avaliação e sem to-do mensal: quem limpa registra, e o registro já
    nasce concluído com o nome e a data de quem passou a limpeza.
    """
    user = request.user
    is_superadmin = _is_superadmin(user)
    is_gerente = _is_gerente_or_superadmin(user)
    user_sectors = _get_user_sectors(user)

    registros = (LimpezaTodo.objects
                 .filter(realizada_em__isnull=False)
                 .select_related('sector', 'template', 'realizada_por'))
    if not is_gerente:
        # Quem não é gestor vê os setores dele e o que ele mesmo registrou.
        registros = registros.filter(
            models.Q(sector__in=user_sectors) | models.Q(realizada_por=user))

    setor_id = request.GET.get('setor') or ''
    if setor_id.isdigit():
        registros = registros.filter(sector_id=int(setor_id))

    hoje = timezone.localdate()
    inicio_mes = hoje.replace(day=1)

    context = {
        'registros': registros[:100],
        'total_registros': registros.count(),
        'registros_mes': registros.filter(realizada_em__date__gte=inicio_mes).count(),
        'registros_hoje': registros.filter(realizada_em__date=hoje).count(),
        'is_superadmin': is_superadmin,
        'is_gerente': is_gerente,
        'setores': (Sector.objects.all().order_by('name')
                    if is_gerente else user_sectors),
        'setor_id': setor_id,
        'tem_template': LimpezaTemplate.objects.filter(is_active=True).exists(),
    }
    return render(request, 'limpeza/dashboard.html', context)


# ─── Registrar uma limpeza ────────────────────────────────────────────────────

@login_required
def registro_novo(request):
    """Checklist da limpeza: uma pergunta, três opções, e pronto."""
    template = (LimpezaTemplate.objects.filter(is_active=True)
                .order_by('-created_at').first())
    if not template:
        messages.error(request, 'Nenhum checklist de limpeza cadastrado ainda.')
        return redirect('limpeza:dashboard')

    is_gerente = _is_gerente_or_superadmin(request.user)
    setores = (Sector.objects.all().order_by('name')
               if is_gerente else _get_user_sectors(request.user))
    if not setores:
        messages.error(request, 'Seu usuário não está ligado a nenhum setor. Fale com o RH.')
        return redirect('limpeza:dashboard')

    perguntas = list(template.questions.all())

    if request.method == 'POST':
        setor = None
        setor_enviado = (request.POST.get('setor') or '').strip()
        if setor_enviado.isdigit():
            setor = next((s for s in setores if s.id == int(setor_enviado)), None)
        if not setor:
            messages.error(request, 'Escolha o setor onde a limpeza foi feita.')
            return redirect('limpeza:registro_novo')

        # Todas as perguntas precisam de resposta. Sem isso, uma pergunta em
        # branco viraria "não" silenciosamente — o mesmo defeito que já foi
        # corrigido no módulo de Experiência.
        respostas = {}
        faltando = []
        for q in perguntas:
            valor = request.POST.get(f'resposta_{q.id}', '')
            if valor not in ('sim', 'nao', 'nao_se_aplica'):
                faltando.append(q)
            else:
                respostas[q.id] = valor
        if faltando:
            ids_faltando = {q.id for q in faltando}
            messages.error(
                request,
                f'{len(faltando)} pergunta(s) ficaram sem resposta e nada foi salvo. '
                f'Responda todas e envie novamente.')
            return render(request, 'limpeza/registro_form.html', {
                'template': template,
                'perguntas': _perguntas_para_tela(perguntas, respostas, ids_faltando,
                                                  request.POST),
                'opcoes': OPCOES_RESPOSTA,
                'setores': setores,
                'setor_escolhido': setor_enviado,
            })

        agora = timezone.now()
        with transaction.atomic():
            registro = LimpezaTodo.objects.create(
                template=template,
                sector=setor,
                month=timezone.localdate().month,
                year=timezone.localdate().year,
                status='finalizado',
                launched_by=request.user,
                submitted_by=request.user,
                realizada_por=request.user,
                realizada_em=agora,
            )
            LimpezaAnswer.objects.bulk_create([
                LimpezaAnswer(
                    todo=registro, question=q, response=respostas[q.id],
                    observation=(request.POST.get(f'obs_{q.id}') or '').strip()[:2000],
                    status='aprovado',          # não há validação: já nasce aceita
                    answered_by=request.user, answered_at=agora,
                ) for q in perguntas
            ])

        messages.success(
            request,
            f'Limpeza de {setor.name} registrada em {timezone.localtime(agora):%d/%m/%Y às %H:%M}.')
        return redirect('limpeza:registro_detalhe', registro_id=registro.id)

    return render(request, 'limpeza/registro_form.html', {
        'template': template,
        'perguntas': _perguntas_para_tela(perguntas, {}, set(), {}),
        'opcoes': OPCOES_RESPOSTA,
        'setores': setores,
        'setor_escolhido': str(setores[0].id) if len(setores) == 1 else '',
    })


def _perguntas_para_tela(perguntas, respostas, ids_faltando, dados_post):
    """Anota cada pergunta com o que já foi marcado.

    O template do Django não busca chave dinâmica em dicionário, então as
    opções e a resposta atual vêm prontas daqui — assim um reenvio com erro
    não perde o que a pessoa já tinha marcado.
    """
    montadas = []
    for q in perguntas:
        atual = respostas.get(q.id, '')
        montadas.append({
            'id': q.id,
            'texto': q.text,
            'detalhamento': q.detalhamento,
            'observacao': (dados_post.get(f'obs_{q.id}') or '') if dados_post else '',
            'faltando': q.id in ids_faltando,
            'opcoes': [
                {'valor': v, 'rotulo': r, 'cor': c, 'marcada': atual == v}
                for v, r, c in OPCOES_RESPOSTA
            ],
        })
    return montadas


@login_required
def registro_detalhe(request, registro_id):
    """Ver uma limpeza registrada: quem fez, quando e o checklist."""
    registro = get_object_or_404(
        LimpezaTodo.objects.select_related('sector', 'template', 'realizada_por'),
        id=registro_id)

    if not _is_gerente_or_superadmin(request.user):
        permitido = (registro.realizada_por_id == request.user.id
                     or registro.sector in _get_user_sectors(request.user))
        if not permitido:
            messages.error(request, 'Você não tem acesso a este registro.')
            return redirect('limpeza:dashboard')

    respostas = registro.answers.select_related('question').all()
    return render(request, 'limpeza/registro_detalhe.html', {
        'registro': registro,
        'respostas': respostas,
        'total_sim': sum(1 for r in respostas if r.response == 'sim'),
        'total_nao': sum(1 for r in respostas if r.response == 'nao'),
        'total_na': sum(1 for r in respostas if r.response == 'nao_se_aplica'),
        'is_gerente': _is_gerente_or_superadmin(request.user),
    })



@login_required
def template_list(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Você não tem permissão para acessar esta área.')
        return redirect('limpeza:dashboard')

    templates = LimpezaTemplate.objects.filter(is_active=True).select_related('created_by')
    if not _is_superadmin(user):
        templates = templates.filter(created_by=user)

    return render(request, 'limpeza/template_list.html', {
        'templates': templates,
        'is_superadmin': _is_superadmin(user),
    })


@login_required
def template_create(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('limpeza:dashboard')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        questions_text = request.POST.getlist('question_text')
        questions_points = request.POST.getlist('question_points')
        questions_pilar = request.POST.getlist('question_pilar')
        questions_item = request.POST.getlist('question_item')
        questions_gravidade = request.POST.getlist('question_gravidade')
        questions_detalhamento = request.POST.getlist('question_detalhamento')
        questions_contestavel = request.POST.getlist('question_contestavel')

        if not name:
            messages.error(request, 'O nome do template é obrigatório.')
            return redirect('limpeza:template_create')

        if not questions_text or not any(q.strip() for q in questions_text):
            messages.error(request, 'Adicione pelo menos uma pergunta.')
            return redirect('limpeza:template_create')

        template = LimpezaTemplate.objects.create(
            name=name,
            description=description,
            created_by=user,
        )

        for i, text in enumerate(questions_text):
            text = text.strip()
            if text:
                try:
                    points = int(questions_points[i]) if i < len(questions_points) else 0
                except (ValueError, TypeError):
                    points = 0
                LimpezaQuestion.objects.create(
                    template=template,
                    text=text,
                    pilar=questions_pilar[i].strip() if i < len(questions_pilar) else '',
                    item=questions_item[i].strip() if i < len(questions_item) else '',
                    gravidade=questions_gravidade[i] if i < len(questions_gravidade) else '',
                    detalhamento=questions_detalhamento[i].strip() if i < len(questions_detalhamento) else '',
                    contestavel=questions_contestavel[i] == '1' if i < len(questions_contestavel) else True,
                    order=i,
                    points=max(0, points),
                )

        messages.success(request, f'Template "{name}" criado com sucesso!')
        return redirect('limpeza:template_list')

    return render(request, 'limpeza/template_form.html', {
        'is_superadmin': _is_superadmin(user),
    })


@login_required
def template_edit(request, template_id):
    user = request.user
    template = get_object_or_404(LimpezaTemplate, id=template_id, is_active=True)

    if not _is_superadmin(user) and template.created_by != user:
        messages.error(request, 'Você não tem permissão.')
        return redirect('limpeza:template_list')

    if request.method == 'POST':
        template.name = request.POST.get('name', '').strip() or template.name
        template.description = request.POST.get('description', '').strip()
        template.save()

        # Remove perguntas antigas e recria
        template.questions.all().delete()

        questions_text = request.POST.getlist('question_text')
        questions_points = request.POST.getlist('question_points')
        questions_pilar = request.POST.getlist('question_pilar')
        questions_item = request.POST.getlist('question_item')
        questions_gravidade = request.POST.getlist('question_gravidade')
        questions_detalhamento = request.POST.getlist('question_detalhamento')
        questions_contestavel = request.POST.getlist('question_contestavel')

        for i, text in enumerate(questions_text):
            text = text.strip()
            if text:
                try:
                    points = int(questions_points[i]) if i < len(questions_points) else 0
                except (ValueError, TypeError):
                    points = 0
                LimpezaQuestion.objects.create(
                    template=template,
                    text=text,
                    pilar=questions_pilar[i].strip() if i < len(questions_pilar) else '',
                    item=questions_item[i].strip() if i < len(questions_item) else '',
                    gravidade=questions_gravidade[i] if i < len(questions_gravidade) else '',
                    detalhamento=questions_detalhamento[i].strip() if i < len(questions_detalhamento) else '',
                    contestavel=questions_contestavel[i] == '1' if i < len(questions_contestavel) else True,
                    order=i,
                    points=max(0, points),
                )

        messages.success(request, f'Template "{template.name}" atualizado!')
        return redirect('limpeza:template_list')

    return render(request, 'limpeza/template_form.html', {
        'template': template,
        'questions': template.questions.all(),
        'is_superadmin': _is_superadmin(user),
    })


@login_required
@require_POST
def template_delete(request, template_id):
    user = request.user
    template = get_object_or_404(LimpezaTemplate, id=template_id)

    if not _is_superadmin(user) and template.created_by != user:
        messages.error(request, 'Você não tem permissão.')
        return redirect('limpeza:template_list')

    template.is_active = False
    template.save()
    messages.success(request, f'Template "{template.name}" removido.')
    return redirect('limpeza:template_list')


@login_required
def import_template_pdf(request):
    """Importa template a partir de PDF de checklist."""
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Você não tem permissão.')
        return redirect('limpeza:dashboard')

    preview_data = None

    if request.method == 'POST':
        action = request.POST.get('action', 'preview')

        if action == 'preview':
            pdf_file = request.FILES.get('pdf_file')
            if not pdf_file:
                messages.error(request, 'Selecione um arquivo PDF.')
                return redirect('limpeza:import_template_pdf')

            if not pdf_file.name.lower().endswith('.pdf'):
                messages.error(request, 'O arquivo deve ser um PDF.')
                return redirect('limpeza:import_template_pdf')

            from .pdf_parser import parse_checklist_pdf
            try:
                questions = parse_checklist_pdf(pdf_file)
            except Exception:
                messages.error(request, 'Erro ao processar o PDF. Verifique se o formato está correto.')
                return redirect('limpeza:import_template_pdf')

            if not questions:
                messages.error(request, 'Nenhuma pergunta encontrada no PDF.')
                return redirect('limpeza:import_template_pdf')

            # Guardar na sessão para o confirm
            request.session['pdf_import_questions'] = questions
            template_name = request.POST.get('name', '').strip() or pdf_file.name.replace('.pdf', '')
            template_description = request.POST.get('description', '').strip()
            request.session['pdf_import_name'] = template_name
            request.session['pdf_import_description'] = template_description

            total_points = sum(q['pontuacao'] for q in questions)
            preview_data = {
                'questions': questions,
                'total': len(questions),
                'total_points': total_points,
                'name': template_name,
                'description': template_description,
            }

        elif action == 'confirm':
            questions = request.session.pop('pdf_import_questions', None)
            name = request.session.pop('pdf_import_name', 'Template Importado')
            description = request.session.pop('pdf_import_description', '')

            if not questions:
                messages.error(request, 'Dados da importação expiraram. Tente novamente.')
                return redirect('limpeza:import_template_pdf')

            template = LimpezaTemplate.objects.create(
                name=name,
                description=description,
                created_by=user,
            )

            for q in questions:
                LimpezaQuestion.objects.create(
                    template=template,
                    text=q['pergunta'],
                    pilar=q['pilar'],
                    item=q['item'],
                    gravidade=q['gravidade'],
                    detalhamento=q['detalhamento'],
                    contestavel=q['contestavel'],
                    order=q['ordem'],
                    points=q['pontuacao'],
                )

            messages.success(request, f'Template "{name}" importado com {len(questions)} perguntas!')
            return redirect('limpeza:template_list')

    return render(request, 'limpeza/import_pdf.html', {
        'is_superadmin': _is_superadmin(user),
        'preview_data': preview_data,
    })


# ─── Relatórios ──────────────────────────────────────────────────────────────

@login_required
def reports(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Sem permissão.')
        return redirect('limpeza:dashboard')

    is_superadmin = _is_superadmin(user)

    # Filtros
    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')
    sector_filter = request.GET.get('sector')
    status_filter = request.GET.get('status')

    todos = LimpezaTodo.objects.select_related(
        'template', 'sector', 'launched_by', 'submitted_by', 'evaluated_by'
    )

    if not is_superadmin:
        user_sectors = _get_user_sectors(user)
        todos = todos.filter(sector__in=user_sectors)

    if month_filter:
        try:
            todos = todos.filter(month=int(month_filter))
        except ValueError:
            pass
    if year_filter:
        try:
            todos = todos.filter(year=int(year_filter))
        except ValueError:
            pass
    if sector_filter:
        todos = todos.filter(sector_id=sector_filter)
    if status_filter:
        todos = todos.filter(status=status_filter)

    # Estatísticas
    total = todos.count()
    finalized = todos.filter(status='finalizado').count()
    avg_score = todos.filter(status='finalizado').aggregate(
        avg=models.Avg('score_percentage')
    )['avg'] or 0

    # Contagem de respostas por tipo (todos os to-dos filtrados)
    todo_ids = todos.values_list('id', flat=True)
    all_answers = LimpezaAnswer.objects.filter(todo_id__in=todo_ids)
    total_sim = all_answers.filter(response='sim').count()
    total_nao = all_answers.filter(response='nao').count()
    total_nao_se_aplica = all_answers.filter(response='nao_se_aplica').count()

    sectors = Sector.objects.all().order_by('name') if is_superadmin else Sector.objects.filter(
        id__in=[s.id for s in _get_user_sectors(user)]
    ).order_by('name')

    now = timezone.now()

    context = {
        'todos': todos.order_by('-year', '-month', 'sector__name'),
        'sectors': sectors,
        'total': total,
        'finalized': finalized,
        'avg_score': round(avg_score, 1),
        'total_sim': total_sim,
        'total_nao': total_nao,
        'total_nao_se_aplica': total_nao_se_aplica,
        'is_superadmin': is_superadmin,
        'current_month': now.month,
        'current_year': now.year,
        'filter_month': month_filter or '',
        'filter_year': year_filter or '',
        'filter_sector': sector_filter or '',
        'filter_status': status_filter or '',
    }
    return render(request, 'limpeza/reports.html', context)


@login_required
def export_report(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        return HttpResponse(status=403)

    is_superadmin = _is_superadmin(user)

    todos = LimpezaTodo.objects.select_related(
        'template', 'sector', 'launched_by', 'submitted_by', 'evaluated_by'
    )
    if not is_superadmin:
        user_sectors = _get_user_sectors(user)
        todos = todos.filter(sector__in=user_sectors)

    month_filter = request.GET.get('month')
    year_filter = request.GET.get('year')
    sector_filter = request.GET.get('sector')
    status_filter = request.GET.get('status')

    if month_filter:
        try:
            todos = todos.filter(month=int(month_filter))
        except ValueError:
            pass
    if year_filter:
        try:
            todos = todos.filter(year=int(year_filter))
        except ValueError:
            pass
    if sector_filter:
        todos = todos.filter(sector_id=sector_filter)
    if status_filter:
        todos = todos.filter(status=status_filter)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="limpeza_relatorio.csv"'
    response.write('\ufeff')  # BOM for Excel

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'Setor', 'Mês/Ano', 'Template', 'Status', 'Pontuação (%)',
        'Lançado por', 'Enviado por', 'Avaliado por', 'Data Avaliação',
    ])

    for todo in todos.order_by('-year', '-month'):
        writer.writerow([
            todo.sector.name,
            f"{todo.month:02d}/{todo.year}",
            todo.template.name,
            todo.get_status_display(),
            f"{todo.score_percentage:.1f}",
            todo.launched_by.get_full_name() if todo.launched_by else '',
            todo.submitted_by.get_full_name() if todo.submitted_by else '',
            todo.evaluated_by.get_full_name() if todo.evaluated_by else '',
            todo.evaluation_date.strftime('%d/%m/%Y %H:%M') if todo.evaluation_date else '',
        ])

    return response


# ─── Histórico / Arquivo ─────────────────────────────────────────────────────

@login_required
def archive(request):
    user = request.user
    if not _is_gerente_or_superadmin(user):
        messages.error(request, 'Sem permissão.')
        return redirect('limpeza:dashboard')

    is_superadmin = _is_superadmin(user)

    if is_superadmin:
        todos = LimpezaTodo.objects.all()
    else:
        user_sectors = _get_user_sectors(user)
        todos = LimpezaTodo.objects.filter(sector__in=user_sectors)

    todos = todos.select_related(
        'template', 'sector', 'launched_by'
    ).order_by('-year', '-month', 'sector__name')

    return render(request, 'limpeza/archive.html', {
        'todos': todos,
        'is_superadmin': is_superadmin,
    })


