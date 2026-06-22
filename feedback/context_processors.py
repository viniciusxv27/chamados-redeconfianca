def survey_menu(request):
    """Controla a exibição do item 'Pesquisa de Clima' no menu.

    Superadmins e gestores das pesquisas sempre veem o item. Para os demais
    usuários, depende da configuração `SurveySettings.climate_menu_visible`.
    """
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {'show_climate_menu': False}

    try:
        from .models import SurveySettings

        if SurveySettings.load().climate_menu_visible:
            return {'show_climate_menu': True}
    except Exception:
        return {'show_climate_menu': True}

    try:
        from .views import _can_manage_surveys

        return {'show_climate_menu': _can_manage_surveys(request.user)}
    except Exception:
        return {'show_climate_menu': False}
