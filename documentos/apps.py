from django.apps import AppConfig


class DocumentosConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'documentos'
    verbose_name = 'Documentos'

    def ready(self):
        # Registra o checker de "documentos pendentes" no sistema de popups.
        try:
            from . import popup_checkers  # noqa: F401
        except Exception:
            pass
