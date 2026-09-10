from .models import InventoryManager


def _get_inventory_manager(user):
    """Retorna o perfil de gestor de inventário do usuário, ou None"""
    try:
        return user.inventory_manager_profile
    except (InventoryManager.DoesNotExist, AttributeError):
        return None


def e_gestor_de_inventario(user):
    """Gere o estoque: SUPERADMIN/ADMIN, perfil de gestor ativo, ou liberado."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if getattr(user, 'hierarchy', '') in ('SUPERADMIN', 'ADMIN'):
        return True
    from users.module_access import user_has_module
    if user_has_module(user, 'almoxarifado.gestao'):
        return True
    manager = _get_inventory_manager(user)
    return bool(manager is not None and manager.is_active)


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

    is_active_manager = e_gestor_de_inventario(user)
    from .views import can_approve_requests

    return {
        'is_inventory_manager': is_active_manager,
        'is_inventory_approver': is_active_manager or can_approve_requests(user),
    }
