from django.apps import AppConfig


class AcessConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'acess'
    verbose_name = 'Confiança Acess'

    def ready(self):
        # Usuário inativado no portal vira aviso no Acess (desligamento imediato ou agendado).
        from .sinais import conectar
        conectar()
