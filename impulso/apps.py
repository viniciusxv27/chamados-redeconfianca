from django.apps import AppConfig


class ImpulsoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'impulso'
    verbose_name = 'Impulso'

    def ready(self):
        # Chamado de suporte fechado conclui a meta que foi puxada dele (impulso/chamados_suporte.py).
        from . import chamados_suporte
        chamados_suporte.conectar()
