from django.apps import AppConfig


class ApiMobileConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api_mobile'
    verbose_name = 'API do app mobile'

    def ready(self):
        # Push no celular: post_save dos dois sinos do portal (api_mobile/push.py).
        from . import push  # noqa: F401
