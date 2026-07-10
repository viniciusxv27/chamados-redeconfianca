"""Registra os checkers de popup fornecidos pelo app de feedback.

Importado no `ready()` do FeedbackConfig, para que o sistema de popups
(portal_popups) saiba verificar a conclusão da Pesquisa de Clima sem depender
diretamente do app de feedback.
"""
from portal_popups.checkers import register_popup_checker


@register_popup_checker('climate_survey', 'Pesquisa de Clima respondida (ou isento)')
def climate_survey_completed(user):
    from .models import (
        CLIMATE_SURVEY_KEY,
        ClimateSurveyParticipation,
        is_exempt_from_climate_survey,
    )
    if is_exempt_from_climate_survey(user, CLIMATE_SURVEY_KEY):
        return True
    return ClimateSurveyParticipation.objects.filter(
        survey_key=CLIMATE_SURVEY_KEY,
        user=user,
        status='COMPLETED',
    ).exists()
