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
    Context processor para disponibilizar TODOS os setores para o chat de suporte.
    Qualquer usuário autenticado pode abrir chat de suporte para qualquer setor.
    Exclui setores que contêm "loja" no nome.
    """
    if request.user.is_authenticated:
        from users.models import Sector
        # Retornar TODOS os setores, excluindo os que contêm "loja" no nome
        all_sectors = Sector.objects.exclude(name__icontains='loja').order_by('name')
        sectors_data = [{'id': sector.id, 'name': sector.name} for sector in all_sectors]
        return {
            'user_support_sectors': sectors_data,
            'user_support_sectors_json': json.dumps(sectors_data)
        }
    
    return {
        'user_support_sectors': [],
        'user_support_sectors_json': '[]'
    }