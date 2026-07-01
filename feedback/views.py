from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Avg, Count, Max, Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from users.models import Sector, User

from .ai import generate_ai_summary, transcribe_feedback_audio
from .forms import AssignmentForm, FeedbackForm
from .models import (
    ClimateSurveyParticipation,
    ClimateSurveyResponse,
    ExitInterviewParticipation,
    ExitInterviewResponse,
    Feedback,
    FeedbackAssignment,
    FeedbackReminderDismissal,
    SurveyManagerPermission,
    SurveySettings,
)
from .reminders import get_pending_reminders


def _is_superadmin(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(user, 'hierarchy', '') == 'SUPERADMIN'


def _can_manage_feedback(user) -> bool:
    """Pode atribuir, ver relatórios e gerenciar feedbacks.

    Liberado para superadministradores e para a hierarquia "Administração" (ADMIN).
    """
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return getattr(user, 'hierarchy', '') in ('SUPERADMIN', 'ADMIN')


def _is_padrao(user) -> bool:
    return getattr(user, 'hierarchy', '') in ('PADRAO', 'PADRÃO')


def _is_gerentes_group_user(user) -> bool:
    """Verifica se o usuario pertence ao grupo GERENTES (CommunicationGroup)."""
    try:
        from communications.models import CommunicationGroup

        return CommunicationGroup.objects.filter(
            name__iexact='GERENTES', members=user
        ).exists()
    except Exception:
        return False


def _user_sector_ids(user):
    """IDs de todos os setores do usuario (setores M2M + setor principal)."""
    ids = set(user.sectors.values_list('id', flat=True))
    if getattr(user, 'sector_id', None):
        ids.add(user.sector_id)
    return ids


def _sector_feedback_targets(user):
    """Colaboradores PADRAO que um gerente PADRAO pode avaliar pelo setor.

    Regra: usuario PADRAO pertencente ao grupo GERENTES pode dar feedback em
    todos os usuarios PADRAO que compartilham pelo menos um setor com ele.
    """
    if not (_is_padrao(user) and _is_gerentes_group_user(user)):
        return User.objects.none()

    sector_ids = _user_sector_ids(user)
    if not sector_ids:
        return User.objects.none()

    return (
        User.objects.filter(is_active=True, hierarchy__in=['PADRAO', 'PADRÃO'])
        .filter(Q(sectors__in=sector_ids) | Q(sector_id__in=sector_ids))
        .exclude(id=user.id)
        .distinct()
        .order_by('first_name', 'last_name')
    )


def _can_give_sector_feedback(user, evaluatee) -> bool:
    if evaluatee is None:
        return False
    return _sector_feedback_targets(user).filter(id=evaluatee.id).exists()


CLIMATE_SURVEY_KEY = 'clima_organizacional_2026'

CLIMATE_LIKERT_OPTIONS = [
    (1, 'Discordo totalmente'),
    (2, 'Discordo'),
    (3, 'Neutro'),
    (4, 'Concordo'),
    (5, 'Concordo totalmente'),
]

CLIMATE_SURVEY_SECTIONS = [
    {
        'key': 'ambiente',
        'title': 'Ambiente de trabalho',
        'questions': [
            {'key': 'ambiente_recursos', 'label': 'Tenho os recursos e ferramentas necessários para realizar meu trabalho.'},
            {'key': 'ambiente_condicoes', 'label': 'As condições físicas do meu ambiente de trabalho são adequadas.'},
            {'key': 'ambiente_carga', 'label': 'Minha carga de trabalho é equilibrada.'},
            {'key': 'ambiente_seguranca', 'label': 'Sinto-me seguro(a) para realizar minhas atividades diárias.'},
        ],
    },
    {
        'key': 'lideranca',
        'title': 'Liderança',
        'questions': [
            {'key': 'lideranca_orientacao', 'label': 'Recebo orientações claras da minha liderança.'},
            {'key': 'lideranca_feedback', 'label': 'Recebo feedbacks que ajudam no meu desenvolvimento.'},
            {'key': 'lideranca_respeito', 'label': 'Minha liderança me trata com respeito.'},
            {'key': 'lideranca_abertura', 'label': 'Tenho abertura para falar com minha liderança quando necessário.'},
        ],
    },
    {
        'key': 'comunicacao',
        'title': 'Comunicação e relacionamento',
        'questions': [
            {'key': 'comunicacao_clareza', 'label': 'A comunicação interna é clara e chega no momento certo.'},
            {'key': 'comunicacao_equipe', 'label': 'Minha equipe coopera para atingir os resultados.'},
            {'key': 'comunicacao_respeito', 'label': 'Os conflitos são tratados com respeito.'},
            {'key': 'comunicacao_integracao', 'label': 'Sinto que existe integração entre os setores/lojas.'},
            {'key': 'comunicacao_etica', 'label': 'Confio que a Rede Confiança procura agir de maneira ética e transparente.'},
        ],
    },
    {
        'key': 'reconhecimento',
        'title': 'Reconhecimento e desenvolvimento',
        'questions': [
            {'key': 'reconhecimento_trabalho', 'label': 'Meu trabalho é reconhecido pela empresa.'},
            {'key': 'reconhecimento_crescimento', 'label': 'Vejo oportunidades de crescimento profissional.'},
            {'key': 'reconhecimento_treinamento', 'label': 'Os treinamentos recebidos me preparam para a função.'},
            {'key': 'reconhecimento_justica', 'label': 'Percebo justiça nas decisões que impactam minha rotina.'},
            {'key': 'reconhecimento_consistencia', 'label': 'As regras de campanhas, comissões e reconhecimentos são aplicadas de forma consistente.'},
            {'key': 'reconhecimento_desempenho', 'label': 'Pessoas que apresentam um bom desempenho recebem reconhecimento.'},
            {'key': 'reconhecimento_remuneracao', 'label': 'Considero que minha remuneração e meus benefícios são compatíveis com minhas responsabilidades.'},
        ],
    },
    {
        'key': 'inovacao',
        'title': 'Inovação e melhoria contínua',
        'questions': [
            {'key': 'inovacao_causas', 'label': 'A empresa procura resolver as causas dos problemas, e não apenas situações pontuais.'},
            {'key': 'inovacao_praticas', 'label': 'Boas práticas e aprendizados são compartilhados entre as lojas e equipes.'},
            {'key': 'inovacao_liberdade', 'label': 'Tenho liberdade para propor formas mais simples e eficientes de realizar meu trabalho.'},
        ],
    },
    {
        'key': 'engajamento',
        'title': 'Cultura e engajamento',
        'questions': [
            {'key': 'engajamento_orgulho', 'label': 'Tenho orgulho de trabalhar na empresa.'},
            {'key': 'engajamento_metas', 'label': 'Conheço as metas e objetivos esperados para meu trabalho.'},
            {'key': 'engajamento_recomendacao', 'label': 'Eu recomendaria a empresa como um bom lugar para trabalhar.'},
            {'key': 'engajamento_permanencia', 'label': 'Tenho vontade de continuar trabalhando aqui.'},
            {'key': 'engajamento_missao', 'label': 'A missão e os valores da Rede Confiança são percebidos nas decisões do dia a dia.'},
            {'key': 'engajamento_direcao', 'label': 'Confio na direção que a empresa está seguindo.'},
            {'key': 'engajamento_cliente', 'label': 'As decisões da empresa demonstram preocupação com a experiência do cliente.'},
        ],
    },
    {
        'key': 'bem_estar',
        'title': 'Bem-estar',
        'questions': [
            {'key': 'bem_estar_cobranca', 'label': 'A cobrança por resultados acontece de forma respeitosa.'},
            {'key': 'bem_estar_seguranca', 'label': 'Sinto segurança para comunicar comportamentos inadequados, assédio, discriminação ou atitudes antiéticas.'},
            {'key': 'bem_estar_preocupacao', 'label': 'A empresa demonstra preocupação verdadeira com o bem-estar dos colaboradores.'},
            {'key': 'bem_estar_respeito', 'label': 'As relações de trabalho são respeitosas, independentemente do cargo da pessoa.'},
        ],
    },
]

CLIMATE_FUNCTION_OPTIONS = [
    'Vendas',
    'Liderança de loja',
    'Administrativo de loja',
    'Administrativo escritório',
]

CLIMATE_TENURE_OPTIONS = [
    'Menos de 3 meses',
    'De 3 a 6 meses',
    'De 7 meses a 1 ano',
    'De 1 a 2 anos',
    'Mais de 2 anos',
]

CLIMATE_OPEN_QUESTIONS = [
    {'key': 'aberta_pontos_positivos', 'label': 'O que você mais gosta no seu ambiente de trabalho?'},
    {'key': 'aberta_melhorias', 'label': 'O que precisa melhorar para o clima ficar melhor?'},
    {'key': 'aberta_sugestoes', 'label': 'Deixe sugestões, comentários ou observações.'},
]


def _climate_question_keys():
    keys = []
    for section in CLIMATE_SURVEY_SECTIONS:
        keys.extend(question['key'] for question in section['questions'])
    return keys


def _climate_question_label_map():
    labels = {}
    for section in CLIMATE_SURVEY_SECTIONS:
        for question in section['questions']:
            labels[question['key']] = question['label']
    return labels


def _primary_sector_for_user(user):
    primary_sector = getattr(user, 'primary_sector', None)
    if callable(primary_sector):
        primary_sector = primary_sector()
    return primary_sector


def _can_manage_surveys(user) -> bool:
    """Pode gerenciar a Pesquisa de Clima e a Entrevista de Desligamento
    (e ver os relatórios): superadmins ou usuários liberados manualmente."""
    if not user.is_authenticated:
        return False
    if _is_superadmin(user):
        return True
    return SurveyManagerPermission.objects.filter(user=user).exists()


def superadmin_required(view_func):
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not _is_superadmin(request.user):
            return HttpResponseForbidden('Acesso restrito a superadministradores.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def feedback_manager_required(view_func):
    """Acesso liberado a superadmins e à hierarquia Administração (ADMIN)."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not _can_manage_feedback(request.user):
            return HttpResponseForbidden('Acesso restrito aos gestores de feedback.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def survey_manager_required(view_func):
    """Acesso liberado a superadmins e a usuários autorizados a gerenciar as pesquisas."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not _can_manage_surveys(request.user):
            return HttpResponseForbidden('Acesso restrito aos gestores das pesquisas.')
        return view_func(request, *args, **kwargs)
    return _wrapped


@login_required
def dashboard(request):
    user = request.user
    my_targets = FeedbackAssignment.objects.filter(
        evaluator=user, status='ACTIVE'
    ).select_related('evaluatee').order_by('-created_at')

    # Gerentes PADRAO podem avaliar todos os PADRAO do seu setor (sem atribuicao).
    assigned_ids = set(my_targets.values_list('evaluatee_id', flat=True))
    sector_targets = [
        u for u in _sector_feedback_targets(user) if u.id not in assigned_ids
    ]

    given = Feedback.objects.filter(evaluator=user).select_related('evaluatee').order_by('-created_at')[:10]
    received = Feedback.objects.filter(evaluatee=user).select_related('evaluator').order_by('-created_at')[:10]

    stats = {
        'targets_count': my_targets.count() + len(sector_targets),
        'given_count': Feedback.objects.filter(evaluator=user).count(),
        'received_count': Feedback.objects.filter(evaluatee=user).count(),
    }

    context = {
        'my_targets': my_targets,
        'sector_targets': sector_targets,
        'given': given,
        'received': received,
        'stats': stats,
        'is_superadmin': _is_superadmin(user),
        'can_manage_feedback': _can_manage_feedback(user),
    }
    return render(request, 'feedback/dashboard.html', context)


@login_required
def my_pending(request):
    my_targets = FeedbackAssignment.objects.filter(
        evaluator=request.user, status='ACTIVE'
    ).select_related('evaluatee').order_by('-created_at')
    assigned_ids = set(my_targets.values_list('evaluatee_id', flat=True))
    sector_targets = [
        u for u in _sector_feedback_targets(request.user) if u.id not in assigned_ids
    ]
    return render(request, 'feedback/pending.html', {
        'my_targets': my_targets,
        'sector_targets': sector_targets,
    })


@login_required
@require_http_methods(['GET', 'POST'])
def climate_survey(request):
    user = request.user
    # Identificação do setor mostra todas as lojas/setores cadastrados.
    available_sectors = Sector.objects.all().order_by('name')

    selected_sector = _primary_sector_for_user(user) or available_sectors.first()
    participation, _ = ClimateSurveyParticipation.objects.get_or_create(
        survey_key=CLIMATE_SURVEY_KEY,
        user=user,
        defaults={
            'sector': selected_sector,
            'last_step': 'Identificação',
        },
    )

    if request.method == 'POST':
        sector_id = request.POST.get('sector')
        sector = Sector.objects.filter(id=sector_id).first()
        if not sector:
            messages.error(request, 'Selecione o setor para responder a pesquisa.')
            return redirect('feedback:climate_survey')

        funcao = (request.POST.get('funcao') or '').strip()
        tempo_empresa = (request.POST.get('tempo_empresa') or '').strip()
        if funcao not in CLIMATE_FUNCTION_OPTIONS or tempo_empresa not in CLIMATE_TENURE_OPTIONS:
            messages.error(request, 'Informe sua função e o tempo de empresa para responder a pesquisa.')
            return redirect('feedback:climate_survey')

        answers = {'likert': {}, 'open': {}, 'profile': {'funcao': funcao, 'tempo_empresa': tempo_empresa}}
        missing = []
        for key in _climate_question_keys():
            value = request.POST.get(key)
            try:
                value_int = int(value)
            except (TypeError, ValueError):
                missing.append(key)
                continue
            if value_int not in [1, 2, 3, 4, 5]:
                missing.append(key)
                continue
            answers['likert'][key] = value_int

        if missing:
            messages.error(request, 'Responda todas as perguntas de escala antes de enviar.')
            return redirect('feedback:climate_survey')

        for question in CLIMATE_OPEN_QUESTIONS:
            answers['open'][question['key']] = (request.POST.get(question['key']) or '').strip()

        now = timezone.now()
        duration = None
        if participation.started_at:
            duration = max(int((now - participation.started_at).total_seconds()), 0)

        ClimateSurveyResponse.objects.create(
            survey_key=CLIMATE_SURVEY_KEY,
            user=user,
            sector=sector,
            answers=answers,
            duration_seconds=duration,
        )

        participation.sector = sector
        participation.status = 'COMPLETED'
        participation.last_step = 'Concluída'
        participation.completed_at = now
        participation.save(update_fields=['sector', 'status', 'last_step', 'completed_at', 'updated_at'])

        messages.success(request, 'Pesquisa de Clima enviada com sucesso. Obrigado por participar.')
        return redirect('feedback:climate_survey')

    if participation.status != 'COMPLETED':
        if selected_sector and participation.sector_id != selected_sector.id:
            participation.sector = selected_sector
        if not participation.last_step:
            participation.last_step = 'Identificação'
        participation.save(update_fields=['sector', 'last_step', 'updated_at'])

    return render(request, 'feedback/climate_survey.html', {
        'survey_key': CLIMATE_SURVEY_KEY,
        'sections': CLIMATE_SURVEY_SECTIONS,
        'open_questions': CLIMATE_OPEN_QUESTIONS,
        'likert_options': CLIMATE_LIKERT_OPTIONS,
        'function_options': CLIMATE_FUNCTION_OPTIONS,
        'tenure_options': CLIMATE_TENURE_OPTIONS,
        'available_sectors': available_sectors,
        'selected_sector': selected_sector,
        'participation': participation,
        'already_completed': participation.status == 'COMPLETED',
        'is_superadmin': _is_superadmin(user),
        'can_manage_surveys': _can_manage_surveys(user),
    })


@login_required
@require_POST
def climate_survey_progress(request):
    import json

    try:
        payload = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        payload = {}

    step = (payload.get('step') or request.POST.get('step') or '').strip()[:120]
    sector_id = payload.get('sector') or request.POST.get('sector')
    sector = Sector.objects.filter(id=sector_id).first() if sector_id else _primary_sector_for_user(request.user)

    participation, _ = ClimateSurveyParticipation.objects.get_or_create(
        survey_key=CLIMATE_SURVEY_KEY,
        user=request.user,
        defaults={
            'sector': sector,
            'last_step': step or 'Identificação',
        },
    )

    if participation.status != 'COMPLETED':
        if step:
            participation.last_step = step
        if sector:
            participation.sector = sector
        participation.save(update_fields=['sector', 'last_step', 'updated_at'])

    return JsonResponse({'success': True})


@survey_manager_required
def climate_survey_report(request):
    sector_filter_id = request.GET.get('sector')
    status_filter = (request.GET.get('status') or '').strip().lower()

    users_qs = User.objects.filter(is_active=True).select_related('sector').prefetch_related('sectors').order_by('first_name', 'last_name')
    participations = {
        item.user_id: item
        for item in ClimateSurveyParticipation.objects.filter(survey_key=CLIMATE_SURVEY_KEY).select_related('user', 'sector')
    }

    rows = []
    for user in users_qs:
        sector = _primary_sector_for_user(user)
        participation = participations.get(user.id)
        if participation and participation.sector:
            sector = participation.sector
        status = participation.status if participation else 'NOT_STARTED'
        rows.append({
            'user': user,
            'sector': sector,
            'participation': participation,
            'status': status,
            'status_label': {
                'COMPLETED': 'Concluída',
                'IN_PROGRESS': 'Em andamento',
                'NOT_STARTED': 'Não iniciou',
            }.get(status, status),
        })

    selected_sector = None
    if sector_filter_id:
        try:
            selected_sector = Sector.objects.filter(id=int(sector_filter_id)).first()
            rows = [row for row in rows if row['sector'] and selected_sector and row['sector'].id == selected_sector.id]
        except (TypeError, ValueError):
            selected_sector = None

    if status_filter in ['completed', 'in_progress', 'not_started']:
        status_map = {
            'completed': 'COMPLETED',
            'in_progress': 'IN_PROGRESS',
            'not_started': 'NOT_STARTED',
        }
        rows = [row for row in rows if row['status'] == status_map[status_filter]]

    overview_map = {}
    all_users_rows = []
    for user in users_qs:
        sector = _primary_sector_for_user(user)
        participation = participations.get(user.id)
        if participation and participation.sector:
            sector = participation.sector
        status = participation.status if participation else 'NOT_STARTED'
        key = sector.id if sector else 0
        bucket = overview_map.setdefault(key, {
            'sector': sector,
            'sector_name': sector.name if sector else 'Sem setor',
            'total': 0,
            'completed': 0,
            'in_progress': 0,
            'not_started': 0,
        })
        bucket['total'] += 1
        if status == 'COMPLETED':
            bucket['completed'] += 1
        elif status == 'IN_PROGRESS':
            bucket['in_progress'] += 1
        else:
            bucket['not_started'] += 1
        all_users_rows.append({'sector': sector, 'status': status})

    overview = sorted(overview_map.values(), key=lambda item: item['sector_name'].lower())
    for bucket in overview:
        bucket['completed_pct'] = round((bucket['completed'] / bucket['total']) * 100, 1) if bucket['total'] else 0.0

    funcao_filter = (request.GET.get('funcao') or '').strip()
    tempo_filter = (request.GET.get('tempo') or '').strip()
    answer_key = (request.GET.get('q_key') or '').strip()
    answer_val = (request.GET.get('q_val') or '').strip()

    responses = ClimateSurveyResponse.objects.filter(survey_key=CLIMATE_SURVEY_KEY).select_related('sector', 'user')
    if selected_sector:
        responses = responses.filter(sector=selected_sector)
    responses = list(responses)
    if funcao_filter in CLIMATE_FUNCTION_OPTIONS:
        responses = [r for r in responses if (r.answers or {}).get('profile', {}).get('funcao') == funcao_filter]
    if tempo_filter in CLIMATE_TENURE_OPTIONS:
        responses = [r for r in responses if (r.answers or {}).get('profile', {}).get('tempo_empresa') == tempo_filter]
    label_map = _climate_question_label_map()
    if answer_key in label_map and answer_val.isdigit():
        responses = [r for r in responses if (r.answers or {}).get('likert', {}).get(answer_key) == int(answer_val)]

    question_stats = []
    for section in CLIMATE_SURVEY_SECTIONS:
        for question in section['questions']:
            values = []
            for response in responses:
                value = (response.answers or {}).get('likert', {}).get(question['key'])
                if isinstance(value, int):
                    values.append(value)
            avg = round(sum(values) / len(values), 2) if values else None
            question_stats.append({
                'section': section['title'],
                'question': question['label'],
                'avg': avg,
                'count': len(values),
            })

    # Perfil dos respondentes (função e tempo de empresa) com base nas respostas anônimas.
    def _profile_distribution(field, options):
        counter = {opt: 0 for opt in options}
        total = 0
        for response in responses:
            value = (response.answers or {}).get('profile', {}).get(field)
            if value:
                counter[value] = counter.get(value, 0) + 1
                total += 1
        return [
            {'label': opt, 'count': cnt, 'pct': round((cnt / total) * 100, 1) if total else 0.0}
            for opt, cnt in counter.items()
        ]

    profile_stats = {
        'funcao': _profile_distribution('funcao', CLIMATE_FUNCTION_OPTIONS),
        'tempo_empresa': _profile_distribution('tempo_empresa', CLIMATE_TENURE_OPTIONS),
    }

    # Respostas individuais (não anônimas) para análise por colaborador.
    open_label_map = {q['key']: q['label'] for q in CLIMATE_OPEN_QUESTIONS}
    response_rows = []
    duration_values = []
    for response in sorted(responses, key=lambda r: r.submitted_at, reverse=True):
        likert = (response.answers or {}).get('likert', {})
        profile = (response.answers or {}).get('profile', {})
        open_answers = (response.answers or {}).get('open', {})
        scores = [v for v in likert.values() if isinstance(v, int)]
        avg = round(sum(scores) / len(scores), 2) if scores else None
        if response.duration_seconds:
            duration_values.append(response.duration_seconds)
        likert_items = [
            {'label': label_map.get(k, k), 'value': likert.get(k)}
            for k in label_map if k in likert
        ]
        open_items = [
            {'label': open_label_map.get(k, k), 'text': v}
            for k, v in open_answers.items() if v
        ]
        response_rows.append({
            'response': response,
            'user': response.user,
            'sector': response.sector,
            'funcao': profile.get('funcao', ''),
            'tempo_empresa': profile.get('tempo_empresa', ''),
            'avg': avg,
            'duration_display': response.duration_display(),
            'likert_items': likert_items,
            'open_items': open_items,
        })

    avg_duration_seconds = round(sum(duration_values) / len(duration_values)) if duration_values else None
    from .models import _format_duration
    avg_duration_display = _format_duration(avg_duration_seconds)

    answer_filter_options = [
        {'key': k, 'label': label_map[k]} for k in _climate_question_keys()
    ]

    dropout = (
        ClimateSurveyParticipation.objects
        .filter(survey_key=CLIMATE_SURVEY_KEY, status='IN_PROGRESS')
        .values('last_step')
        .annotate(total=Count('id'))
        .order_by('-total', 'last_step')
    )

    totals = {
        'total': len(all_users_rows),
        'completed': sum(1 for row in all_users_rows if row['status'] == 'COMPLETED'),
        'in_progress': sum(1 for row in all_users_rows if row['status'] == 'IN_PROGRESS'),
        'not_started': sum(1 for row in all_users_rows if row['status'] == 'NOT_STARTED'),
        'responses': ClimateSurveyResponse.objects.filter(survey_key=CLIMATE_SURVEY_KEY).count(),
    }
    totals['completed_pct'] = round((totals['completed'] / totals['total']) * 100, 1) if totals['total'] else 0.0

    # Médias por categoria (radar).
    section_avgs = {}
    for item in question_stats:
        section_avgs.setdefault(item['section'], []).append(item['avg'])
    section_labels, section_values = [], []
    for section in CLIMATE_SURVEY_SECTIONS:
        vals = [v for v in section_avgs.get(section['title'], []) if v is not None]
        section_labels.append(section['title'])
        section_values.append(round(sum(vals) / len(vals), 2) if vals else 0)

    import json
    charts = {
        'status': {
            'labels': ['Concluíram', 'Em andamento', 'Não iniciaram'],
            'data': [totals['completed'], totals['in_progress'], totals['not_started']],
        },
        'overview': {
            'labels': [b['sector_name'] for b in overview],
            'completed': [b['completed'] for b in overview],
            'in_progress': [b['in_progress'] for b in overview],
            'not_started': [b['not_started'] for b in overview],
        },
        'sections': {'labels': section_labels, 'data': section_values},
        'questions': {
            'labels': [item['question'] for item in question_stats],
            'data': [item['avg'] or 0 for item in question_stats],
        },
        'funcao': {
            'labels': [i['label'] for i in profile_stats['funcao']],
            'data': [i['count'] for i in profile_stats['funcao']],
        },
        'tempo': {
            'labels': [i['label'] for i in profile_stats['tempo_empresa']],
            'data': [i['count'] for i in profile_stats['tempo_empresa']],
        },
    }

    return render(request, 'feedback/climate_report.html', {
        'totals': totals,
        'overview': overview,
        'detailed': rows,
        'question_stats': question_stats,
        'profile_stats': profile_stats,
        'dropout': dropout,
        'selected_sector': selected_sector,
        'status_filter': status_filter,
        'funcao_filter': funcao_filter,
        'tempo_filter': tempo_filter,
        'function_options': CLIMATE_FUNCTION_OPTIONS,
        'tenure_options': CLIMATE_TENURE_OPTIONS,
        'all_sectors': Sector.objects.all().order_by('name'),
        'survey_key': CLIMATE_SURVEY_KEY,
        'analysis_count': len(responses),
        'charts_json': json.dumps(charts),
        'response_rows': response_rows,
        'avg_duration_display': avg_duration_display,
        'answer_filter_options': answer_filter_options,
        'answer_key': answer_key,
        'answer_val': answer_val,
        'likert_scale_values': [1, 2, 3, 4, 5],
    })


# ---------------------------------------------------------------------------
# Entrevista de Desligamento (formulário nativo + relatório)
# ---------------------------------------------------------------------------

EXIT_INTERVIEW_KEY = 'desligamento_2026'

EXIT_INTERVIEW_INTRO = (
    'A Entrevista de Desligamento é um momento estratégico para a Rede Confiança. '
    'Por meio dela, buscamos compreender melhor a experiência de cada colaborador '
    'durante sua jornada conosco, identificando pontos fortes e oportunidades de '
    'melhoria. Suas respostas serão analisadas pela equipe de gestão/RH.'
)

EXIT_STORE_OPTIONS = [
    'MASTERPLACE', 'JARDIM CAMBURI', 'CENTRO DE VILA VELHA', 'CENTRO DE VITÓRIA',
    'GLÓRIA', 'NORTE SUL', 'MONTSERRAT', 'SHOPPING LARANJEIRAS',
    'MARCILIO DE NORONHA', 'PORTO CANOA', 'JACARAÍPE', 'ITACIBÁ', 'SERRA SEDE',
    'ANCHIETA', 'PIUMA', 'ICONHA', 'BOM JESUS DE ITABAPOANA',
    'SANTO ANTONIO DE PÁDUA', 'MIRACEMA', 'SÃO FIDÉLIS', 'QUISSAMÃ',
]

# Estrutura do formulário. Tipos suportados: 'text', 'paragraph', 'choice', 'scale'.
EXIT_INTERVIEW_SECTIONS = [
    {
        'key': 'dados_gerais',
        'title': 'Bloco 1 – Dados Gerais',
        'questions': [
            {'key': 'nome', 'type': 'text', 'required': False,
             'label': 'Qual seu nome? (deixe em branco caso não queira se identificar)'},
            {'key': 'loja', 'type': 'choice', 'required': False,
             'label': 'Qual sua loja atual?', 'options': EXIT_STORE_OPTIONS},
            {'key': 'cargo', 'type': 'text', 'required': True,
             'label': 'Qual seu cargo?'},
            {'key': 'tempo_empresa', 'type': 'choice', 'required': True,
             'label': 'Qual seu tempo de empresa?',
             'options': ['Menos de 6 meses', '6 meses a 1 ano', '1 a 2 anos', '2 a 5 anos', 'Mais de 5 anos']},
        ],
    },
    {
        'key': 'motivo',
        'title': 'Bloco 2 – Motivo do Desligamento',
        'questions': [
            {'key': 'motivo_desligamento', 'type': 'choice', 'required': True,
             'label': 'Qual o motivo do desligamento?',
             'options': ['Nova oportunidade de trabalho', 'Salário/benefícios',
                         'Falta de reconhecimento', 'Falta de perspectiva de crescimento',
                         'Problemas com liderança', 'Clima de equipe', 'Questões pessoais',
                         'Fui desligado pela empresa']},
            {'key': 'nota_treinamentos', 'type': 'scale', 'required': True,
             'label': 'Em uma escala de 1 a 5, qual nota você dá para os treinamentos aplicados pela empresa?'},
            {'key': 'nota_lideranca', 'type': 'scale', 'required': True,
             'label': 'Em uma escala de 1 a 5, qual a avaliação que você dá para sua liderança direta?'},
            {'key': 'nota_comunicacao', 'type': 'scale', 'required': True,
             'label': 'Em uma escala de 1 a 5, qual avaliação você dá para a comunicação interna da empresa?'},
            {'key': 'nota_reconhecimento', 'type': 'scale', 'required': True,
             'label': 'Em uma escala de 1 a 5, qual avaliação você dá para o reconhecimento e valorização da empresa?'},
            {'key': 'nota_beneficios', 'type': 'scale', 'required': True,
             'label': 'Em uma escala de 1 a 5, qual avaliação você dá para os benefícios e remuneração que a empresa oferece?'},
            {'key': 'nota_clima', 'type': 'scale', 'required': True,
             'label': 'Em uma escala de 1 a 5, qual avaliação você dá para o clima da sua equipe de trabalho?'},
            {'key': 'experiencia_geral', 'type': 'text', 'required': True,
             'label': 'Agora, compartilhe conosco como foi sua experiência geral na Rede Confiança.'},
            {'key': 'valores_presentes', 'type': 'choice', 'required': True,
             'label': 'Você sentiu que os valores da Rede Confiança estavam presentes no dia a dia?',
             'options': ['Sim, totalmente', 'Parcialmente', 'Não']},
            {'key': 'recomendaria', 'type': 'choice', 'required': True,
             'label': 'Você recomendaria a Rede Confiança como lugar para trabalhar?',
             'options': ['Sim, totalmente', 'Talvez', 'Não']},
            {'key': 'lideranca_melhorar', 'type': 'paragraph', 'required': True,
             'label': 'O que a liderança poderia melhorar?'},
            {'key': 'mudaria', 'type': 'paragraph', 'required': True,
             'label': 'O que você mudaria no dia a dia da empresa?'},
        ],
    },
    {
        'key': 'encerramento',
        'title': 'Bloco 3 – Encerramento',
        'questions': [
            {'key': 'possivel_recontratacao', 'type': 'choice', 'required': True,
             'label': 'Possível Recontratação?',
             'options': ['SIM', 'NÃO']},
        ],
    },
]

EXIT_SCALE_OPTIONS = [1, 2, 3, 4, 5]


def _exit_questions():
    """Lista achatada de todas as perguntas do formulário."""
    out = []
    for section in EXIT_INTERVIEW_SECTIONS:
        out.extend(section['questions'])
    return out


@survey_manager_required
@require_http_methods(['GET', 'POST'])
def exit_interview(request):
    """Entrevista de Desligamento conduzida pelo entrevistador (Superadmin ou
    liberado). O entrevistador seleciona o colaborador desligado e registra as
    respostas por ele (preenchimento unilateral)."""
    if request.method == 'POST':
        subject_id = (request.POST.get('subject_id') or '').strip()
        subject = User.objects.filter(pk=subject_id).first() if subject_id else None

        dismissal_raw = (request.POST.get('dismissal_date') or '').strip()
        dismissal_date = None
        if dismissal_raw:
            try:
                dismissal_date = datetime.datetime.strptime(dismissal_raw, '%Y-%m-%d').date()
            except ValueError:
                dismissal_date = None

        answers = {'scale': {}, 'choice': {}, 'text': {}}
        missing = []
        for question in _exit_questions():
            raw = (request.POST.get(question['key']) or '').strip()
            qtype = question['type']
            if qtype == 'scale':
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    value = None
                if value not in EXIT_SCALE_OPTIONS:
                    if question['required']:
                        missing.append(question['key'])
                    continue
                answers['scale'][question['key']] = value
            elif qtype == 'choice':
                if not raw:
                    if question['required']:
                        missing.append(question['key'])
                    continue
                if question.get('options') and raw not in question['options']:
                    missing.append(question['key'])
                    continue
                answers['choice'][question['key']] = raw
            else:  # text / paragraph
                if not raw and question['required']:
                    missing.append(question['key'])
                    continue
                answers['text'][question['key']] = raw

        if subject is None:
            messages.error(request, 'Selecione o colaborador desligado antes de registrar a entrevista.')
            return redirect('feedback:exit_interview')
        if dismissal_date is None:
            messages.error(request, 'Informe a data de desligamento.')
            return redirect('feedback:exit_interview')
        if missing:
            messages.error(request, 'Responda todas as perguntas obrigatórias antes de registrar.')
            return redirect('feedback:exit_interview')

        subject_sector = _primary_sector_for_user(subject)
        now = timezone.now()

        ExitInterviewResponse.objects.create(
            survey_key=EXIT_INTERVIEW_KEY,
            user=subject,
            interviewer=request.user,
            sector=subject_sector,
            answers=answers,
            duration_seconds=None,
        )

        participation, _ = ExitInterviewParticipation.objects.get_or_create(
            survey_key=EXIT_INTERVIEW_KEY,
            user=subject,
            defaults={'sector': subject_sector},
        )
        participation.interviewer = request.user
        participation.sector = subject_sector
        participation.status = 'COMPLETED'
        participation.last_step = 'Concluída'
        participation.completed_at = now
        participation.dismissal_date = dismissal_date
        participation.save()

        messages.success(
            request,
            f'Entrevista de {subject.get_full_name() or subject.username} registrada. '
            'Confira abaixo e efetue o desligamento de acesso quando desejar.',
        )
        return redirect('feedback:exit_interview')

    # GET — formulário + colaboradores com entrevista feita aguardando desligamento
    pending_dismissal = (
        ExitInterviewParticipation.objects
        .filter(
            survey_key=EXIT_INTERVIEW_KEY,
            status='COMPLETED',
            dismissal_executed_at__isnull=True,
            user__is_active=True,
        )
        .select_related('user', 'sector', 'interviewer')
        .order_by('-completed_at')
    )
    candidate_users = (
        User.objects.filter(is_active=True)
        .exclude(pk=request.user.pk)
        .select_related('sector')
        .order_by('first_name', 'last_name')
    )

    return render(request, 'feedback/exit_interview.html', {
        'survey_key': EXIT_INTERVIEW_KEY,
        'intro': EXIT_INTERVIEW_INTRO,
        'sections': EXIT_INTERVIEW_SECTIONS,
        'scale_options': EXIT_SCALE_OPTIONS,
        'candidate_users': candidate_users,
        'pending_dismissal': pending_dismissal,
        'today': timezone.localdate(),
        'is_superadmin': _is_superadmin(request.user),
        'can_manage_surveys': _can_manage_surveys(request.user),
    })


@survey_manager_required
@require_POST
def exit_interview_dismiss(request, user_id):
    """Efetua o desligamento de acesso do colaborador: inativa o login e grava a
    data de demissão. Vinculado à Entrevista de Desligamento."""
    target = get_object_or_404(User, pk=user_id)

    if target.pk == request.user.pk:
        messages.error(request, 'Você não pode efetuar o próprio desligamento.')
        return redirect('feedback:exit_interview')

    participation = (
        ExitInterviewParticipation.objects
        .filter(survey_key=EXIT_INTERVIEW_KEY, user=target)
        .first()
    )

    dismissal_raw = (request.POST.get('dismissal_date') or '').strip()
    dismissal_date = None
    if dismissal_raw:
        try:
            dismissal_date = datetime.datetime.strptime(dismissal_raw, '%Y-%m-%d').date()
        except ValueError:
            dismissal_date = None
    if dismissal_date is None and participation and participation.dismissal_date:
        dismissal_date = participation.dismissal_date
    if dismissal_date is None:
        dismissal_date = timezone.localdate()

    target.is_active = False
    target.demission_date = dismissal_date
    target.save(update_fields=['is_active', 'demission_date'])

    if participation:
        participation.dismissal_date = dismissal_date
        participation.dismissal_executed_at = timezone.now()
        participation.save(update_fields=['dismissal_date', 'dismissal_executed_at', 'updated_at'])

    messages.success(
        request,
        f'Desligamento efetuado: login de {target.get_full_name() or target.username} inativado '
        f'e data de demissão registrada em {dismissal_date:%d/%m/%Y}.',
    )
    return redirect('feedback:exit_interview')


@survey_manager_required
def exit_interview_report(request):
    status_filter = (request.GET.get('status') or '').strip().lower()
    sector_filter_id = request.GET.get('sector')
    answer_key = (request.GET.get('q_key') or '').strip()
    answer_val = (request.GET.get('q_val') or '').strip()

    selected_sector = None
    if sector_filter_id:
        try:
            selected_sector = Sector.objects.filter(id=int(sector_filter_id)).first()
        except (TypeError, ValueError):
            selected_sector = None

    participations = {
        item.user_id: item
        for item in ExitInterviewParticipation.objects
        .filter(survey_key=EXIT_INTERVIEW_KEY)
        .select_related('user', 'sector', 'interviewer')
    }
    # Inclui colaboradores já desligados (inativos) que possuem entrevista, para
    # que continuem aparecendo no relatório após o desligamento de acesso.
    users_qs = (
        User.objects.filter(Q(is_active=True) | Q(id__in=list(participations.keys())))
        .select_related('sector')
        .order_by('first_name', 'last_name')
    )

    rows = []
    for user in users_qs:
        participation = participations.get(user.id)
        status = participation.status if participation else 'NOT_STARTED'
        rows.append({
            'user': user,
            'sector': participation.sector if participation and participation.sector else _primary_sector_for_user(user),
            'participation': participation,
            'status': status,
            'status_label': {
                'COMPLETED': 'Concluída',
                'IN_PROGRESS': 'Em andamento',
                'NOT_STARTED': 'Não iniciou',
            }.get(status, status),
        })

    detailed = rows
    if selected_sector:
        detailed = [r for r in detailed if r['sector'] and r['sector'].id == selected_sector.id]
    if status_filter in ['completed', 'in_progress', 'not_started']:
        status_map = {'completed': 'COMPLETED', 'in_progress': 'IN_PROGRESS', 'not_started': 'NOT_STARTED'}
        detailed = [row for row in detailed if row['status'] == status_map[status_filter]]

    responses = ExitInterviewResponse.objects.filter(survey_key=EXIT_INTERVIEW_KEY).select_related('sector', 'user', 'interviewer')
    if selected_sector:
        responses = responses.filter(sector=selected_sector)
    responses = list(responses)
    exit_label_map = {q['key']: q['label'] for q in _exit_questions()}
    exit_type_map = {q['key']: q['type'] for q in _exit_questions()}
    if answer_key in exit_type_map:
        qtype = exit_type_map[answer_key]
        if qtype == 'scale' and answer_val.isdigit():
            responses = [r for r in responses if (r.answers or {}).get('scale', {}).get(answer_key) == int(answer_val)]
        elif qtype == 'choice' and answer_val:
            responses = [r for r in responses if (r.answers or {}).get('choice', {}).get(answer_key) == answer_val]

    # Estatísticas das notas (escala 1-5).
    scale_stats = []
    for question in _exit_questions():
        if question['type'] != 'scale':
            continue
        values = []
        for response in responses:
            value = (response.answers or {}).get('scale', {}).get(question['key'])
            if isinstance(value, int):
                values.append(value)
        avg = round(sum(values) / len(values), 2) if values else None
        scale_stats.append({
            'key': question['key'],
            'label': question['label'],
            'short': question['label'].split('?')[0][:60],
            'avg': avg,
            'count': len(values),
        })

    # Distribuição das perguntas de múltipla escolha.
    choice_stats = []
    for question in _exit_questions():
        if question['type'] != 'choice':
            continue
        counter = {opt: 0 for opt in (question.get('options') or [])}
        total = 0
        for response in responses:
            value = (response.answers or {}).get('choice', {}).get(question['key'])
            if value:
                counter[value] = counter.get(value, 0) + 1
                total += 1
        choice_stats.append({
            'key': question['key'],
            'label': question['label'],
            'total': total,
            'items': [
                {'option': opt, 'count': cnt,
                 'pct': round((cnt / total) * 100, 1) if total else 0.0}
                for opt, cnt in counter.items()
            ],
        })

    # Comentários abertos (texto/paragraph).
    open_blocks = []
    for question in _exit_questions():
        if question['type'] not in ('text', 'paragraph'):
            continue
        texts = []
        for response in responses:
            value = (response.answers or {}).get('text', {}).get(question['key'])
            if value and value.strip():
                texts.append(value.strip())
        open_blocks.append({'label': question['label'], 'answers': texts})

    dropout = (
        ExitInterviewParticipation.objects
        .filter(survey_key=EXIT_INTERVIEW_KEY, status='IN_PROGRESS')
        .values('last_step')
        .annotate(total=Count('id'))
        .order_by('-total', 'last_step')
    )

    # Respostas individuais (não anônimas) por colaborador.
    from .models import _format_duration
    response_rows = []
    duration_values = []
    for response in sorted(responses, key=lambda r: r.submitted_at, reverse=True):
        ans = response.answers or {}
        scale = ans.get('scale', {})
        choice = ans.get('choice', {})
        text = ans.get('text', {})
        scores = [v for v in scale.values() if isinstance(v, int)]
        avg = round(sum(scores) / len(scores), 2) if scores else None
        if response.duration_seconds:
            duration_values.append(response.duration_seconds)
        items = []
        for q in _exit_questions():
            k = q['key']
            if q['type'] == 'scale' and k in scale:
                items.append({'label': q['label'], 'value': scale[k], 'kind': 'scale'})
            elif q['type'] == 'choice' and k in choice:
                items.append({'label': q['label'], 'value': choice[k], 'kind': 'choice'})
            elif q['type'] in ('text', 'paragraph') and text.get(k):
                items.append({'label': q['label'], 'value': text[k], 'kind': 'text'})
        response_rows.append({
            'response': response,
            'user': response.user,
            'interviewer': response.interviewer,
            'sector': response.sector,
            'avg': avg,
            'duration_display': response.duration_display(),
            'items': items,
        })

    avg_duration_seconds = round(sum(duration_values) / len(duration_values)) if duration_values else None
    avg_duration_display = _format_duration(avg_duration_seconds)

    answer_filter_options = [
        {'key': q['key'], 'label': q['label'], 'type': q['type'], 'options': q.get('options') or []}
        for q in _exit_questions() if q['type'] in ('scale', 'choice')
    ]

    totals = {
        'total': len(rows),
        'completed': sum(1 for r in rows if r['status'] == 'COMPLETED'),
        'in_progress': sum(1 for r in rows if r['status'] == 'IN_PROGRESS'),
        'not_started': sum(1 for r in rows if r['status'] == 'NOT_STARTED'),
        'responses': len(responses),
    }
    totals['completed_pct'] = round((totals['completed'] / totals['total']) * 100, 1) if totals['total'] else 0.0

    import json
    return render(request, 'feedback/exit_interview_report.html', {
        'totals': totals,
        'detailed': detailed,
        'scale_stats': scale_stats,
        'choice_stats': choice_stats,
        'open_blocks': open_blocks,
        'dropout': dropout,
        'status_filter': status_filter,
        'selected_sector': selected_sector,
        'all_sectors': Sector.objects.all().order_by('name'),
        'survey_key': EXIT_INTERVIEW_KEY,
        'response_rows': response_rows,
        'avg_duration_display': avg_duration_display,
        'answer_filter_options': answer_filter_options,
        'answer_key': answer_key,
        'answer_val': answer_val,
        'scale_labels_json': json.dumps([s['short'] for s in scale_stats]),
        'scale_values_json': json.dumps([s['avg'] or 0 for s in scale_stats]),
        'choice_stats_json': json.dumps([
            {'label': c['label'], 'labels': [i['option'] for i in c['items']],
             'data': [i['count'] for i in c['items']]}
            for c in choice_stats
        ]),
    })


@survey_manager_required
@require_POST
def exit_interview_reset(request, user_id):
    """Zera o controle de participação da entrevista de um colaborador,
    permitindo que o entrevistador registre novamente. Não altera o login."""
    target = get_object_or_404(User, pk=user_id)
    deleted, _ = ExitInterviewParticipation.objects.filter(
        survey_key=EXIT_INTERVIEW_KEY, user=target
    ).delete()
    if deleted:
        messages.success(request, f'Entrevista de {target.get_full_name() or target.username} zerada. Pode ser registrada novamente.')
    else:
        messages.info(request, f'{target.get_full_name() or target.username} ainda não possui entrevista registrada.')
    return redirect('feedback:exit_interview_report')


# ---------------------------------------------------------------------------
# Gestão de acessos às pesquisas (somente superadmin)
# ---------------------------------------------------------------------------

@superadmin_required
@require_http_methods(['GET', 'POST'])
def survey_access(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        # Ações que não dependem de um usuário-alvo.
        if action == 'clear_climate':
            resp_count = ClimateSurveyResponse.objects.filter(survey_key=CLIMATE_SURVEY_KEY).count()
            part_count = ClimateSurveyParticipation.objects.filter(survey_key=CLIMATE_SURVEY_KEY).count()
            ClimateSurveyResponse.objects.filter(survey_key=CLIMATE_SURVEY_KEY).delete()
            ClimateSurveyParticipation.objects.filter(survey_key=CLIMATE_SURVEY_KEY).delete()
            messages.success(
                request,
                f'Pesquisa de Clima zerada: {resp_count} resposta(s) e {part_count} participação(ões) removidas.',
            )
            return redirect('feedback:survey_access')

        if action == 'toggle_climate_menu':
            config = SurveySettings.load()
            config.climate_menu_visible = not config.climate_menu_visible
            config.save(update_fields=['climate_menu_visible', 'updated_at'])
            if config.climate_menu_visible:
                messages.success(request, 'A "Pesquisa de Clima" voltou a aparecer no menu para todos os usuários.')
            else:
                messages.success(request, 'A "Pesquisa de Clima" foi ocultada no menu (visível apenas para superadmins e gestores).')
            return redirect('feedback:survey_access')

        user_id = request.POST.get('user_id')
        target = User.objects.filter(pk=user_id).first()
        if not target:
            messages.error(request, 'Usuário não encontrado.')
            return redirect('feedback:survey_access')

        if action == 'grant':
            if _is_superadmin(target):
                messages.info(request, f'{target.get_full_name() or target.username} já é superadmin e tem acesso.')
            else:
                SurveyManagerPermission.objects.get_or_create(
                    user=target, defaults={'granted_by': request.user}
                )
                messages.success(request, f'Acesso liberado para {target.get_full_name() or target.username}.')
        elif action == 'revoke':
            SurveyManagerPermission.objects.filter(user=target).delete()
            messages.success(request, f'Acesso removido de {target.get_full_name() or target.username}.')
        return redirect('feedback:survey_access')

    permissions = (
        SurveyManagerPermission.objects
        .select_related('user', 'user__sector', 'granted_by')
        .order_by('user__first_name', 'user__last_name')
    )
    return render(request, 'feedback/survey_access.html', {
        'permissions': permissions,
        'climate_menu_visible': SurveySettings.load().climate_menu_visible,
        'climate_response_count': ClimateSurveyResponse.objects.filter(survey_key=CLIMATE_SURVEY_KEY).count(),
        'climate_participation_count': ClimateSurveyParticipation.objects.filter(survey_key=CLIMATE_SURVEY_KEY).count(),
    })


@login_required
def create_feedback(request, assignment_id=None):
    assignment = None
    evaluatee = None

    if assignment_id:
        assignment = get_object_or_404(FeedbackAssignment, pk=assignment_id)
        if assignment.evaluator_id != request.user.id and not _is_superadmin(request.user):
            return HttpResponseForbidden('Você não pode aplicar este feedback.')
        evaluatee = assignment.evaluatee
    else:
        # Sem atribuição: apenas superadmin pode escolher livremente,
        # ou usuário comum se foi indicado como avaliador em alguma atribuição.
        target_id = request.GET.get('evaluatee') or request.POST.get('evaluatee')
        if target_id:
            evaluatee = get_object_or_404(User, pk=target_id)
            if not _is_superadmin(request.user):
                has_assignment = FeedbackAssignment.objects.filter(
                    evaluator=request.user, evaluatee=evaluatee, status='ACTIVE'
                ).exists()
                if not has_assignment and not _can_give_sector_feedback(request.user, evaluatee):
                    return HttpResponseForbidden('Você não tem atribuição para avaliar este colaborador.')

    if request.method == 'POST':
        if not evaluatee:
            messages.error(request, 'Selecione o colaborador a ser avaliado.')
            return redirect('feedback:dashboard')

        form = FeedbackForm(request.POST)
        if form.is_valid():
            fb = form.save(commit=False)
            fb.evaluator = request.user
            fb.evaluatee = evaluatee
            fb.assignment = assignment
            if not fb.nome_colaborador:
                fb.nome_colaborador = evaluatee.get_full_name() or evaluatee.username

            audio_upload = request.FILES.get('audio')
            if audio_upload:
                fb.audio_file = audio_upload
            fb.save()

            # Transcreve o áudio (se enviado) antes de gerar o resumo IA.
            if fb.audio_file:
                try:
                    transcribe_feedback_audio(fb)
                except Exception:
                    pass

            # Tenta gerar resumo IA imediatamente (silencioso em caso de falha).
            try:
                generate_ai_summary(fb)
            except Exception:
                pass

            messages.success(request, 'Feedback registrado com sucesso.')
            return redirect('feedback:detail', feedback_id=fb.pk)
    else:
        initial = {}
        if evaluatee:
            initial['nome_colaborador'] = evaluatee.get_full_name() or evaluatee.username
            primary_sector = getattr(evaluatee, 'primary_sector', None)
            if callable(primary_sector):
                try:
                    primary_sector = primary_sector()
                except Exception:
                    primary_sector = None
            if primary_sector:
                initial['setor_area'] = getattr(primary_sector, 'name', '') or ''
        form = FeedbackForm(initial=initial)

    return render(request, 'feedback/create.html', {
        'form': form,
        'assignment': assignment,
        'evaluatee': evaluatee,
    })


@login_required
def feedback_detail(request, feedback_id):
    fb = get_object_or_404(
        Feedback.objects.select_related('evaluator', 'evaluatee', 'assignment'),
        pk=feedback_id,
    )

    is_admin = _can_manage_feedback(request.user)
    if not (is_admin or request.user.id in (fb.evaluator_id, fb.evaluatee_id)):
        return HttpResponseForbidden('Você não tem permissão para ver este feedback.')

    show_ai = is_admin
    ai_text = ''
    if show_ai:
        ai_text = fb.ai_summary or generate_ai_summary(fb)

    # O avaliado (sem ser admin nem o avaliador) não deve ver a nota geral.
    is_evaluatee_only = (
        not is_admin
        and request.user.id == fb.evaluatee_id
        and request.user.id != fb.evaluator_id
    )

    return render(request, 'feedback/detail.html', {
        'fb': fb,
        'show_ai': show_ai,
        'ai_text': ai_text,
        'previous': fb.previous_feedback(),
        'evolution_delta': fb.evolution_delta(),
        'is_evaluatee_only': is_evaluatee_only,
    })


@feedback_manager_required
@require_POST
def regenerate_ai_summary(request, feedback_id):
    fb = get_object_or_404(Feedback, pk=feedback_id)
    text = generate_ai_summary(fb, force=True)
    if not text and fb.ai_summary_error:
        messages.error(request, f'Erro ao gerar resumo: {fb.ai_summary_error}')
    else:
        messages.success(request, 'Resumo IA atualizado.')
    return redirect('feedback:detail', feedback_id=fb.pk)


@login_required
def user_history(request, user_id):
    target = get_object_or_404(User, pk=user_id)

    is_admin = _can_manage_feedback(request.user)
    if not (is_admin or request.user.id == target.id):
        # Permite também ao avaliador atual ver histórico de quem ele avalia.
        has_relation = FeedbackAssignment.objects.filter(
            evaluator=request.user, evaluatee=target
        ).exists() or Feedback.objects.filter(
            evaluator=request.user, evaluatee=target
        ).exists()
        if not has_relation:
            return HttpResponseForbidden('Sem permissão para ver este histórico.')

    feedbacks = Feedback.objects.filter(evaluatee=target).select_related('evaluator').order_by('-data', '-created_at')

    # Série temporal de evolução das médias
    timeline = []
    for fb in reversed(list(feedbacks)):
        avg = fb.average_score()
        if avg is not None:
            timeline.append({'date': fb.data.isoformat(), 'avg': avg, 'id': fb.pk})

    overall_avg = feedbacks.aggregate(avg=Avg('nota_comunicacao'))['avg']  # placeholder

    return render(request, 'feedback/history.html', {
        'target': target,
        'feedbacks': feedbacks,
        'timeline': timeline,
        'is_superadmin': is_admin,
    })


@feedback_manager_required
def manage_all(request):
    q = (request.GET.get('q') or '').strip()
    feedbacks = Feedback.objects.select_related('evaluator', 'evaluatee').order_by('-created_at')
    assignments = FeedbackAssignment.objects.select_related('evaluator', 'evaluatee').order_by('-created_at')

    if q:
        feedbacks = feedbacks.filter(
            Q(evaluatee__first_name__icontains=q) | Q(evaluatee__last_name__icontains=q)
            | Q(evaluator__first_name__icontains=q) | Q(evaluator__last_name__icontains=q)
            | Q(setor_area__icontains=q)
        )
        assignments = assignments.filter(
            Q(evaluatee__first_name__icontains=q) | Q(evaluatee__last_name__icontains=q)
            | Q(evaluator__first_name__icontains=q) | Q(evaluator__last_name__icontains=q)
        )

    user_stats = (
        Feedback.objects.values('evaluatee_id', 'evaluatee__first_name', 'evaluatee__last_name')
        .annotate(total=Count('id'))
        .order_by('-total')[:20]
    )

    return render(request, 'feedback/manage.html', {
        'feedbacks': feedbacks[:100],
        'assignments': assignments[:100],
        'user_stats': user_stats,
        'q': q,
    })


@feedback_manager_required
@require_http_methods(['GET', 'POST'])
def assign_view(request):
    if request.method == 'POST':
        form = AssignmentForm(request.POST)
        if form.is_valid():
            evaluator_id = form.cleaned_data['evaluator']
            evaluatee_ids_raw = form.cleaned_data['evaluatees']
            evaluatee_ids = [int(x) for x in evaluatee_ids_raw.split(',') if x.strip().isdigit()]

            if not evaluatee_ids:
                messages.error(request, 'Selecione pelo menos um colaborador a ser avaliado.')
                return redirect('feedback:assign')

            evaluator = get_object_or_404(User, pk=evaluator_id)
            created = 0
            for ev_id in evaluatee_ids:
                if ev_id == evaluator.id:
                    continue
                evaluatee = User.objects.filter(pk=ev_id).first()
                if not evaluatee:
                    continue
                exists = FeedbackAssignment.objects.filter(
                    evaluator=evaluator, evaluatee=evaluatee, status='ACTIVE'
                ).exists()
                if exists:
                    continue
                FeedbackAssignment.objects.create(
                    evaluator=evaluator,
                    evaluatee=evaluatee,
                    notes=form.cleaned_data.get('notes') or '',
                    monthly=form.cleaned_data.get('monthly') or False,
                    created_by=request.user,
                )
                created += 1
            messages.success(request, f'{created} atribuição(ões) criada(s).')
            return redirect('feedback:manage')
        else:
            messages.error(request, 'Dados inválidos.')

    return render(request, 'feedback/assign.html', {})


@feedback_manager_required
@require_POST
def delete_assignment(request, assignment_id):
    a = get_object_or_404(FeedbackAssignment, pk=assignment_id)
    a.delete()
    messages.success(request, 'Atribuição removida.')
    return redirect('feedback:manage')


@login_required
def api_search_users(request):
    q = (request.GET.get('q') or '').strip()
    qs = User.objects.filter(is_active=True)
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q)
        )
    qs = qs.order_by('first_name', 'last_name')[:20]
    return JsonResponse({
        'results': [
            {
                'id': u.id,
                'name': u.get_full_name() or u.username,
                'email': u.email,
                'sector': getattr(getattr(u, 'sector', None), 'name', '') or '',
            }
            for u in qs
        ]
    })


# ---------------------------------------------------------------------------
# Relatórios de cumprimento de feedback por período (regra por tempo de casa)
# ---------------------------------------------------------------------------

# Regras de periodicidade (em dias), conforme tempo de casa do colaborador.
PERIOD_RULES = [
    {'key': 'novato_15d', 'label': 'Novato (até 4 meses)', 'max_months': 4, 'period_days': 15, 'period_label': '15 em 15 dias'},
    {'key': 'novato_30d', 'label': 'Novato (5 a 12 meses)', 'max_months': 12, 'period_days': 30, 'period_label': '1 em 1 mês'},
    {'key': 'veterano_90d', 'label': 'Veterano (mais de 12 meses)', 'max_months': None, 'period_days': 90, 'period_label': '3 em 3 meses'},
]


def _tenure_months(user, today):
    """Meses de casa baseados em date_joined (data de criação do usuário)."""
    base = getattr(user, 'date_joined', None)
    if not base:
        return 0
    base_date = base.date() if hasattr(base, 'date') else base
    days = max((today - base_date).days, 0)
    return days / 30.44


def _rule_for_user(user, today):
    months = _tenure_months(user, today)
    for rule in PERIOD_RULES:
        if rule['max_months'] is None or months <= rule['max_months']:
            return rule, months
    return PERIOD_RULES[-1], months


@feedback_manager_required
def reports(request):
    today = timezone.localdate()
    sector_filter_id = request.GET.get('sector')
    status_filter = (request.GET.get('status') or '').strip().lower()  # '', 'ok', 'pending'

    # Universo: usuários ativos. Exclui superuser explicito do relatório.
    users_qs = (
        User.objects.filter(is_active=True)
        .select_related('sector')
        .order_by('first_name', 'last_name')
    )

    # Pré-carrega contagem de feedbacks por evaluatee dentro de janela máxima (90 dias).
    max_window_start = today - timezone.timedelta(days=90)
    recent_feedbacks = (
        Feedback.objects
        .filter(created_at__date__gte=max_window_start)
        .values('evaluatee_id', 'created_at')
    )
    feedbacks_by_user = {}
    for row in recent_feedbacks:
        feedbacks_by_user.setdefault(row['evaluatee_id'], []).append(row['created_at'].date() if hasattr(row['created_at'], 'date') else row['created_at'])

    # Última data de feedback por usuário (qualquer data, para mostrar referência).
    last_feedback_by_user = dict(
        Feedback.objects
        .values('evaluatee_id')
        .annotate(last=Max('created_at'))
        .values_list('evaluatee_id', 'last')
    )

    rows = []
    for u in users_qs:
        rule, months = _rule_for_user(u, today)
        period_start = today - timezone.timedelta(days=rule['period_days'])
        dates = feedbacks_by_user.get(u.id, [])
        in_period = [d for d in dates if d >= period_start]
        compliant = len(in_period) > 0
        last_dt = last_feedback_by_user.get(u.id)
        rows.append({
            'user': u,
            'sector': u.sector,
            'rule': rule,
            'tenure_months': round(months, 1),
            'period_start': period_start,
            'count_in_period': len(in_period),
            'compliant': compliant,
            'last_feedback': last_dt,
        })

    # Visão geral por setor (PDV).
    sectors_map = {}
    for r in rows:
        sec = r['sector']
        sec_key = sec.id if sec else 0
        bucket = sectors_map.setdefault(sec_key, {
            'sector': sec,
            'sector_name': sec.name if sec else 'Sem setor',
            'total': 0,
            'ok': 0,
            'pending': 0,
        })
        bucket['total'] += 1
        if r['compliant']:
            bucket['ok'] += 1
        else:
            bucket['pending'] += 1

    overview = sorted(
        sectors_map.values(),
        key=lambda b: (b['sector_name'] or '').lower(),
    )
    for b in overview:
        b['ok_pct'] = round((b['ok'] / b['total']) * 100, 1) if b['total'] else 0.0

    # Detalhamento por setor selecionado (ou todos quando sem filtro).
    detailed = rows
    selected_sector = None
    if sector_filter_id:
        try:
            sid = int(sector_filter_id)
            selected_sector = Sector.objects.filter(id=sid).first()
            detailed = [r for r in rows if (r['sector'].id if r['sector'] else 0) == sid]
        except (TypeError, ValueError):
            pass

    if status_filter == 'ok':
        detailed = [r for r in detailed if r['compliant']]
    elif status_filter == 'pending':
        detailed = [r for r in detailed if not r['compliant']]

    totals = {
        'total': len(rows),
        'ok': sum(1 for r in rows if r['compliant']),
        'pending': sum(1 for r in rows if not r['compliant']),
    }
    totals['ok_pct'] = round((totals['ok'] / totals['total']) * 100, 1) if totals['total'] else 0.0

    return render(request, 'feedback/reports.html', {
        'overview': overview,
        'detailed': detailed,
        'totals': totals,
        'rules': PERIOD_RULES,
        'today': today,
        'selected_sector': selected_sector,
        'status_filter': status_filter,
        'all_sectors': Sector.objects.all().order_by('name'),
    })


@login_required
def api_reminders(request):
    reminders = get_pending_reminders(request.user)
    return JsonResponse({'reminders': reminders, 'count': len(reminders)})


@login_required
@require_POST
def api_dismiss_reminder(request):
    key = (request.POST.get('key') or '').strip()
    if not key:
        return JsonResponse({'ok': False, 'error': 'key requerido'}, status=400)
    FeedbackReminderDismissal.objects.get_or_create(user=request.user, key=key)
    return JsonResponse({'ok': True})
