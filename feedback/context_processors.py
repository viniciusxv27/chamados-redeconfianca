def survey_menu(request):
    """Controla a exibição dos itens de pesquisas no menu.

    - 'Pesquisa de Clima': superadmins e gestores das pesquisas sempre veem;
      para os demais depende de `SurveySettings.climate_menu_visible`.
    - 'Entrevista de Desligamento': apenas superadmins e usuários liberados
      especificamente para conduzir a entrevista.
    """
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {'show_climate_menu': False, 'show_exit_interview_menu': False}

    can_manage = False
    can_access_exit_interview = False
    try:
        from .views import _can_access_exit_interview, _can_manage_surveys
        can_manage = _can_manage_surveys(request.user)
        can_access_exit_interview = _can_access_exit_interview(request.user)
    except Exception:
        can_manage = False
        can_access_exit_interview = False

    show_climate = can_manage
    try:
        from .models import SurveySettings
        if SurveySettings.load().climate_menu_visible:
            show_climate = True
    except Exception:
        show_climate = True

    return {
        'show_climate_menu': show_climate,
        'show_exit_interview_menu': can_access_exit_interview,
    }


# Prazo final para responder a Pesquisa de Clima (sexta-feira, 10/07/2026).
# Até o fim desse dia o popup pode ser "pulado"; a partir do dia seguinte ele
# passa a bloquear a navegação de quem ainda não respondeu.
CLIMATE_GATE_DEADLINE = (2026, 7, 10, 23, 59, 59)


def climate_survey_gate(request):
    """Controla o popup obrigatório da Pesquisa de Clima na home / portal.

    - Até o prazo (sexta 10/07): mostra o popup apenas na home, com opção de pular.
    - Depois do prazo: mostra o popup em qualquer página do portal (exceto a
      própria pesquisa e páginas essenciais), sem opção de pular — bloqueia a
      navegação de quem ainda não respondeu.
    """
    empty = {'climate_survey_show': False, 'climate_survey_blocking': False}

    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return empty

    import datetime
    from django.utils import timezone

    deadline = timezone.make_aware(datetime.datetime(*CLIMATE_GATE_DEADLINE))
    after_deadline = timezone.now() > deadline

    path = request.path or ''
    resolver = getattr(request, 'resolver_match', None)
    is_home = bool(resolver) and resolver.url_name == 'home' and not resolver.namespace

    # Páginas que nunca podem ser bloqueadas (senão o usuário não consegue nem
    # responder a pesquisa, nem sair do portal).
    excluded = (
        path.startswith('/feedback/pesquisa-clima')
        or path.startswith('/admin')
        or path.startswith('/login')
        or path.startswith('/logout')
        or path.startswith('/static')
        or path.startswith('/media')
    )

    if after_deadline:
        # Fase de bloqueio: aparece em todo o portal, menos nas páginas essenciais.
        if excluded:
            return empty
    else:
        # Fase "pulável": aparece somente na home.
        if not is_home:
            return empty

    # Só consulta o banco quando o popup realmente pode aparecer.
    try:
        from .models import ClimateSurveyParticipation
        try:
            from .views import CLIMATE_SURVEY_KEY
        except Exception:
            CLIMATE_SURVEY_KEY = 'clima_organizacional_2026'
        completed = ClimateSurveyParticipation.objects.filter(
            survey_key=CLIMATE_SURVEY_KEY,
            user=user,
            status='COMPLETED',
        ).exists()
    except Exception:
        # Em caso de erro, nunca prende o usuário no portal.
        completed = True

    if completed:
        return empty

    return {
        'climate_survey_show': True,
        'climate_survey_blocking': after_deadline,
    }
