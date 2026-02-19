from .models import InventoryManager


def _get_inventory_manager(user):
    """Retorna o perfil de gestor de inventário do usuário, ou None"""
    try:
        return user.inventory_manager_profile
    except (InventoryManager.DoesNotExist, AttributeError):
        return None


def inventory_context(request):
    """Injeta variáveis de permissão de inventário em todos os templates."""
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {
            'is_inventory_manager': False,
            'is_inventory_approver': False,
        }

    user = request.user

    # Superadmin e Admin têm acesso total
    if user.hierarchy in ['SUPERADMIN', 'ADMIN']:
        return {
            'is_inventory_manager': True,
            'is_inventory_approver': True,
        }

    manager = _get_inventory_manager(user)
    is_active_manager = manager is not None and manager.is_active

    return {
        'is_inventory_manager': is_active_manager,
        'is_inventory_approver': is_active_manager,
    }
