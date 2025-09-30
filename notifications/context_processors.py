from .models import UserNotification

def unread_notifications_count(request):
    """
    Context processor para contar notificações não lidas do usuário
    """
    if request.user.is_authenticated:
        count = UserNotification.objects.filter(
            user=request.user,
            is_read=False
        ).count()
        return {'unread_notifications_count': count}
    
    return {'unread_notifications_count': 0}