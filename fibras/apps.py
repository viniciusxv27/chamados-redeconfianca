from django.apps import AppConfig


class FibrasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'fibras'
    verbose_name = 'Acompanhamento de Fibras'

    def ready(self):
        # Cache do quadro (kanban/lista): qualquer gravação derruba (fibras/quadro.py).
        from .quadro import ligar_sinais
        ligar_sinais()
