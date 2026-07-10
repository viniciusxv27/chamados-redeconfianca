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
