from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Contestation


@receiver(post_save, sender=Contestation, dispatch_uid='contestacao_resumo_do_ciclo')
def verificar_fechamento_do_ciclo(sender, instance, **kwargs):
    from .resumo_whatsapp import ao_salvar_contestacao

    ao_salvar_contestacao(instance)
