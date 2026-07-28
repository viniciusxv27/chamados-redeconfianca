from django.apps import AppConfig


class KnowledgeTrailsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'knowledge_trails'
    verbose_name = 'Trilhas de Conhecimento'

    def ready(self):
        # Registra o checker do popup bloqueante da trilha obrigatória.
        try:
            from . import popup_checkers  # noqa: F401
        except Exception:
            pass
