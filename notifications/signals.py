"""
Signals para notificações automáticas.
Disparados quando eventos relevantes acontecem no sistema.
"""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


# Armazenar estado anterior para comparação
_ticket_previous_status = {}
_ticket_previous_assigned = {}


@receiver(pre_save, sender='tickets.Ticket')
def store_ticket_previous_state(sender, instance, **kwargs):
    """
    Armazena estado anterior do ticket para comparação no post_save.
    """
    if instance.pk:
        try:
            from tickets.models import Ticket
            old_instance = Ticket.objects.get(pk=instance.pk)
            _ticket_previous_status[instance.pk] = old_instance.status
            _ticket_previous_assigned[instance.pk] = old_instance.assigned_to
        except Ticket.DoesNotExist:
            pass


@receiver(post_save, sender='tickets.Ticket')
def notify_ticket_events(sender, instance, created, **kwargs):
    """
    Dispara notificações para eventos de tickets:
    - Criação de novo chamado
    - Mudança de status
    - Atribuição de responsável
    """
    from .services import notification_service
    
    # 1. Novo ticket criado
    if created:
        if instance.sector:
            try:
                logger.info(f"Ticket #{instance.id} created, sending notifications...")
                
                result = notification_service.notify_ticket_created(
                    ticket=instance,
                    notify_sector=True,
                    notify_admins=True
                )
                
                logger.info(f"Ticket creation notification result: {result}")
                
            except Exception as e:
                logger.error(f"Error sending ticket creation notification for #{instance.id}: {e}")
        return
    
    # 2. Ticket atualizado - verificar mudanças
    old_status = _ticket_previous_status.pop(instance.pk, None)
    old_assigned = _ticket_previous_assigned.pop(instance.pk, None)
    
    # 2a. Notificar mudança de status
    if old_status and old_status != instance.status:
        try:
            logger.info(f"Ticket #{instance.id} status changed from {old_status} to {instance.status}")
            
            # Determinar quem fez a mudança (último usuário no log)
            from tickets.models import TicketLog
            last_log = TicketLog.objects.filter(
                ticket=instance,
                new_status=instance.status
            ).order_by('-created_at').first()
            
            changed_by = last_log.user if last_log else instance.created_by
            
            result = notification_service.notify_ticket_status_changed(
                ticket=instance,
                old_status=old_status,
                new_status=instance.status,
                changed_by=changed_by
            )
            
            logger.info(f"Status change notification result: {result}")
            
        except Exception as e:
            logger.error(f"Error sending status change notification for #{instance.id}: {e}")
    
    # 2b. Notificar nova atribuição (responsável principal)
    if old_assigned != instance.assigned_to and instance.assigned_to:
        try:
            logger.info(f"Ticket #{instance.id} assigned to user {instance.assigned_to.id}")
            
            # Determinar quem fez a atribuição
            from tickets.models import TicketLog
            last_log = TicketLog.objects.filter(
                ticket=instance
            ).order_by('-created_at').first()
            
            assigned_by = last_log.user if last_log else instance.created_by
            
            result = notification_service.notify_ticket_assigned(
                ticket=instance,
                assigned_user=instance.assigned_to,
                assigned_by=assigned_by
            )
            
            logger.info(f"Assignment notification result: {result}")
            
        except Exception as e:
            logger.error(f"Error sending assignment notification for #{instance.id}: {e}")


@receiver(post_save, sender='tickets.TicketComment')
def notify_ticket_comment(sender, instance, created, **kwargs):
    """
    Dispara notificação quando um novo comentário é adicionado.
    Notifica criador, responsável e usuários atribuídos.
    """
    if not created:
        return
    
    # Ignorar comentários de sistema (mudanças de status automáticas)
    if instance.comment_type in ['STATUS_CHANGE', 'ASSUMPTION', 'PRIORITY_CHANGE']:
        return
    
    from .services import notification_service
    
    try:
        logger.info(f"New comment on ticket #{instance.ticket.id}, sending notifications...")
        
        result = notification_service.notify_ticket_comment(
            ticket=instance.ticket,
            comment=instance,
            comment_by=instance.user
        )
        
        logger.info(f"Comment notification result: {result}")
        
    except Exception as e:
        logger.error(f"Error sending comment notification for ticket #{instance.ticket.id}: {e}")


@receiver(post_save, sender='tickets.TicketAssignment')
def notify_ticket_assignment(sender, instance, created, **kwargs):
    """
    Dispara notificação quando alguém é atribuído adicionalmente a um chamado.
    Notifica o usuário atribuído.
    """
    if not created:
        return
    
    from .services import notification_service
    
    try:
        logger.info(f"User {instance.user.id} assigned to ticket #{instance.ticket.id}")
        
        result = notification_service.notify_ticket_assigned(
            ticket=instance.ticket,
            assigned_user=instance.user,
            assigned_by=instance.assigned_by
        )
        
        logger.info(f"Additional assignment notification result: {result}")
        
    except Exception as e:
        logger.error(f"Error sending additional assignment notification: {e}")


@receiver(post_save, sender='communications.Communication')
def notify_communication_created(sender, instance, created, **kwargs):
    """
    Dispara notificação quando um novo comunicado é criado.
    Notifica todos os destinatários configurados.
    """
    if not created:
        return
    
    from .services import notification_service
    
    try:
        logger.info(f"Communication #{instance.id} created, sending notifications...")
        
        result = notification_service.notify_communication_created(
            communication=instance
        )
        
        logger.info(f"Communication notification result: {result}")
        
    except Exception as e:
        logger.error(f"Error sending communication notification for #{instance.id}: {e}")