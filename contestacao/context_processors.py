from users.models import SystemConfig


QUALITY_ISLAND_SECTOR_ID = 8


def _has_contestacao_global_access(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        config = SystemConfig.get_config()
        return config.contestacao_global_managers.filter(pk=user.pk).exists()
    except Exception:
        return False


def contestacao_menu_context(request):
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {
            'can_access_contestacao_menu': False,
        }

    user = request.user
    can_access = False

    if user.can_create_contestations():
        can_access = True
    elif user.hierarchy in ['ADMINISTRATIVO', 'ADMIN', 'SUPERADMIN']:
        can_access = True
    elif user.sector_id == QUALITY_ISLAND_SECTOR_ID or user.sectors.filter(pk=QUALITY_ISLAND_SECTOR_ID).exists():
        can_access = True
    elif _has_contestacao_global_access(user):
        can_access = True

    return {
        'can_access_contestacao_menu': can_access,
    }
