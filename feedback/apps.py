from django.apps import AppConfig


class FeedbackConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'feedback'
    verbose_name = 'Feedback Geral'

    def ready(self):
        # Registra o checker da Pesquisa de Clima no sistema de popups.
        try:
            from . import popup_checkers  # noqa: F401
        except Exception:
            pass
