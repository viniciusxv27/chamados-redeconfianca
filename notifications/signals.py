from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from tickets.models import Ticket
from communications.models import Communication
from .models import PushNotification, NotificationCategory


@receiver(post_save, sender=Ticket)
def create_ticket_notification(sender, instance, created, **kwargs):
    """Cria notificação quando um chamado é aberto"""
    if created and instance.sector:
        try:
            # Buscar ou criar categoria de chamados
            category, _ = NotificationCategory.objects.get_or_create(
                name="Chamados",
                defaults={
                    'icon': 'fas fa-ticket-alt',
                    'color': 'blue'
                }
            )
            
            # Criar notificação para usuários do setor
            notification = PushNotification.objects.create(
                title=f"Novo Chamado: {instance.title}",
                message=f"Foi aberto um novo chamado no setor {instance.sector.name}. Categoria: {instance.category.name}",
                category=category,
                notification_type='TICKET',
                priority='NORMAL',
                icon='fas fa-ticket-alt',
                action_url=f'/tickets/{instance.id}/',
                action_text='Ver Chamado',
                created_by=instance.created_by,
                extra_data={
                    'ticket_id': instance.id,
                    'sector_id': instance.sector.id,
                    'category_id': instance.category.id
                }
            )
            
            # Adicionar setor como alvo
            notification.target_sectors.add(instance.sector)
            
            # Enviar notificação
            notification.send_notification()
            
        except Exception as e:
            # Log do erro silenciosamente
            pass


@receiver(post_save, sender=Communication)
def create_communication_notification(sender, instance, created, **kwargs):
    """Cria notificação quando um comunicado é postado"""
    if created:
        try:
            # Buscar ou criar categoria de comunicados
            category, _ = NotificationCategory.objects.get_or_create(
                name="Comunicados",
                defaults={
                    'icon': 'fas fa-bullhorn',
                    'color': 'green'
                }
            )
            
            # Criar notificação
            notification = PushNotification.objects.create(
                title=f"Novo Comunicado: {instance.title}",
                message=instance.message[:200] + ('...' if len(instance.message) > 200 else ''),
                category=category,
                notification_type='COMMUNICATION',
                priority='HIGH' if instance.is_pinned else 'NORMAL',
                icon='fas fa-bullhorn',
                action_url=f'/communications/{instance.id}/',
                action_text='Ver Comunicado',
                send_to_all=instance.send_to_all,
                created_by=instance.sender,
                extra_data={
                    'communication_id': instance.id,
                    'is_pinned': instance.is_pinned,
                    'is_popup': instance.is_popup
                }
            )
            
            # Se não é para todos, adicionar recipients específicos
            if not instance.send_to_all:
                notification.target_users.set(instance.recipients.all())
            
            # Enviar notificação
            notification.send_notification()
            
        except Exception as e:
            # Log do erro silenciosamente
            pass