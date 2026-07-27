from django.apps import AppConfig


class CommunicationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'communications'

    def ready(self):
        # Registra o checker do popup bloqueante de comunicados.
        try:
            from . import popup_checkers  # noqa: F401
        except Exception:
            pass
