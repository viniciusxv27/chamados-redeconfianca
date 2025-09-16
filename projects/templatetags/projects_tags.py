from django import template
from projects.models import ProjectSectorAccess

register = template.Library()


@register.filter
def can_access_projects(user):
    """Verifica se o usuário pode acessar o sistema de projetos"""
    if not user or not user.is_authenticated:
        return False
    
    if user.is_superuser:
        return True
    
    try:
        access = ProjectSectorAccess.objects.get(sector=user.sector)
        return access.can_view_projects
    except (ProjectSectorAccess.DoesNotExist, AttributeError):
        return False


@register.filter
def can_create_projects(user):
    """Verifica se o usuário pode criar projetos"""
    if not user or not user.is_authenticated:
        return False
    
    if user.is_superuser:
        return True
    
    try:
        access = ProjectSectorAccess.objects.get(sector=user.sector)
        return access.can_create_projects
    except (ProjectSectorAccess.DoesNotExist, AttributeError):
        return False


@register.filter
def can_manage_all_projects(user):
    """Verifica se o usuário pode gerenciar todos os projetos"""
    if not user or not user.is_authenticated:
        return False
    
    if user.is_superuser:
        return True
    
    try:
        access = ProjectSectorAccess.objects.get(sector=user.sector)
        return access.can_manage_all_projects
    except (ProjectSectorAccess.DoesNotExist, AttributeError):
        return False