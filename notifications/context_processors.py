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
    Context processor para disponibilizar setores que possuem categorias cadastradas
    para o chat de suporte. Exclui setores que contêm "loja" no nome e setores sem
    categorias ativas.
    """
    if request.user.is_authenticated:
        from users.models import Sector
        from django.db.models import Count, Q
        # Retornar apenas setores que possuem ao menos uma categoria ativa, excluindo "loja"
        all_sectors = Sector.objects.exclude(
            name__icontains='loja'
        ).annotate(
            active_categories_count=Count(
                'support_categories',
                filter=Q(support_categories__is_active=True)
            )
        ).filter(active_categories_count__gt=0).order_by('name')
        sectors_data = [{'id': sector.id, 'name': sector.name} for sector in all_sectors]
        return {
            'user_support_sectors': sectors_data,
            'user_support_sectors_json': json.dumps(sectors_data)
        }
    
    return {
        'user_support_sectors': [],
        'user_support_sectors_json': '[]'
    }