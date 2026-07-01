from django.apps import AppConfig


class UsersConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'users'

    def ready(self):
        # Registra os signals de rastreamento de sessão (login/atividade).
        from . import session_tracking  # noqa: F401
