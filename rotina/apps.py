import logging

from django.apps import AppConfig


class RotinaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rotina'
    verbose_name = 'Rotina Gerencial'

    def ready(self):
        # Lembrete da rotina pelo WhatsApp: a varredura roda só nos workers do
        # gunicorn (ver rotina/whatsapp.py). Nunca derruba a subida do portal.
        try:
            from . import whatsapp
            whatsapp.garantir_varredura()
        except Exception:                                           # noqa: BLE001
            logging.getLogger('rotina.whatsapp').exception('A varredura de WhatsApp da rotina não ligou')
