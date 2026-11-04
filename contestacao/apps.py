from django.apps import AppConfig


class ContestacaoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'contestacao'
    verbose_name = 'Contestação'

    def ready(self):
        from . import signals  # noqa: F401
