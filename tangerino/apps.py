from django.apps import AppConfig


class TangerinoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tangerino'
    verbose_name = 'Integração Tangerino (Sólides Ponto)'

    def ready(self):
        # Registra o checker do popup de férias no portal_popups.
        from . import popup_checkers  # noqa: F401
