from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models_chat import SupportChat
from notifications.models import PushNotification

User = get_user_model()

@receiver(post_save, sender=SupportChat)
def notify_support_chat_resolved(sender, instance, created, **kwargs):
    """
    Notifica o usuário quando um chat de suporte é resolvido
    """
    if not created and instance.status == 'RESOLVIDO':
        # Verifica se havia um status anterior diferente
        if hasattr(instance, '_original_status') and instance._original_status != 'RESOLVIDO':
            try:
                # Criar notificação para o usuário que abriu o chamado
                PushNotification.objects.create(
                    user=instance.user,
                    title="Chamado Resolvido",
                    message=f"Seu chamado '{instance.title}' foi resolvido. Por favor, avalie o atendimento.",
                    type="SUPPORT_RESOLVED",
                    data={
                        'chat_id': instance.id,
                        'title': instance.title,
                        'resolved_by': instance.assigned_to.get_full_name() if instance.assigned_to else 'Sistema',
                    },
                    url=f"/projects/support/chat/{instance.id}/",
                    is_read=False
                )
            except Exception as e:
                # Log do erro se necessário
                print(f"Erro ao criar notificação de suporte resolvido: {e}")

@receiver(post_save, sender=SupportChat)
def track_status_changes(sender, instance, **kwargs):
    """
    Rastreia mudanças de status para poder detectar quando foi resolvido
    """
    # Salva o status original para detectar mudanças na próxima atualização
    if hasattr(instance, 'pk') and instance.pk:
        try:
            original = SupportChat.objects.get(pk=instance.pk)
            instance._original_status = original.status
        except SupportChat.DoesNotExist:
            instance._original_status = None