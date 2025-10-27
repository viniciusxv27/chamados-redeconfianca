from .models import UserNotification
import json

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


def user_support_sectors(request):
    """
    Context processor para disponibilizar os setores do usuário para o chat de suporte
    """
    if request.user.is_authenticated:
        user_sectors = request.user.sectors.all()
        sectors_data = [{'id': sector.id, 'name': sector.name} for sector in user_sectors]
        return {
            'user_support_sectors': sectors_data,
            'user_support_sectors_json': json.dumps(sectors_data)
        }
    
    return {
        'user_support_sectors': [],
        'user_support_sectors_json': '[]'
    }