from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404
from django.db import models
from users.models import Sector, User
from .views import user_can_access_projects


@login_required
@require_http_methods(["GET"])
def sector_users_api(request, sector_id):
    """API para buscar usuários de um setor específico"""
    # Verificar permissão para acessar projetos
    if not user_can_access_projects(request.user):
        return JsonResponse({'error': 'Sem permissão para acessar esta área'}, status=403)
    
    try:
        sector = get_object_or_404(Sector, id=sector_id)
        
        # Buscar usuários do setor (tanto no setor principal quanto nos setores secundários)
        users = User.objects.filter(
            models.Q(sector=sector) | models.Q(sectors=sector)
        ).distinct().filter(
            is_active=True
        ).order_by('first_name', 'last_name')
        
        # Preparar dados para retorno
        users_data = []
        for user in users:
            users_data.append({
                'id': user.id,
                'username': user.username,
                'full_name': user.full_name,
                'email': user.email,
                'hierarchy': user.get_hierarchy_display(),
            })
        
        return JsonResponse({
            'success': True,
            'users': users_data,
            'sector_name': sector.name
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)