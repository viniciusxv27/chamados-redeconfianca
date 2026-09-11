import logging

from django.apps import AppConfig


class AgendaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'agenda'
    verbose_name = 'Agenda'

    def ready(self):
        # Retoma transcrições paradas e fecha gravações órfãs — só nos workers do
        # gunicorn (ver agenda/processamento.py). Nunca derruba a subida do portal.
        try:
            from . import processamento
            processamento.garantir_varredura()
        except Exception:                                           # noqa: BLE001
            logging.getLogger('agenda.transcricao').exception('A varredura de transcrições não ligou')
