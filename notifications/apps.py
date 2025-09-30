from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'notifications'
    verbose_name = 'Notificações'
    
    def ready(self):
        # Importar signals quando a app estiver pronta
        try:
            import notifications.signals
        except ImportError:
            pass