"""Quem analisa as solicitações e quem recebe os avisos."""
from django.contrib.auth import get_user_model
from django.db.models import Q

SETORES_DP = r'^\s*(rh|dp|departamento pessoal|recursos humanos)\s*$'


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def e_analista(user):
    """Analisa e aprova: SUPERADMIN, quem o SUPERADMIN pôs como responsável e o RH/DP (os mesmos que veem atestado)."""
    if not (user and getattr(user, 'is_authenticated', False) and user.is_active):
        return False
    if not hasattr(user, '_dp_analista'):
        from .models import ResponsavelDP
        user._dp_analista = (e_superadmin(user) or ResponsavelDP.objects.filter(user=user).exists()
                             or user.pode_ver_atestados())
    return user._dp_analista


def pode_ver(user, solicitacao):
    return solicitacao.colaborador_id == getattr(user, 'pk', None) or e_analista(user)


def destinatarios():
    """Quem recebe o aviso de solicitação nova: os responsáveis escolhidos; sem nenhum, o RH/DP; sem RH/DP, o SUPERADMIN."""
    from .models import ResponsavelDP
    User = get_user_model()
    escolhidos = list(User.objects.filter(is_active=True, responsavel_dp__recebe_avisos=True))
    if escolhidos:
        return escolhidos
    do_dp = list(User.objects.filter(is_active=True).filter(
        Q(sector__name__iregex=SETORES_DP) | Q(communication_groups__name__in=['RH', 'DP'])).distinct())
    if do_dp:
        return do_dp
    if ResponsavelDP.objects.exists():
        return []
    return list(User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(hierarchy='SUPERADMIN')))
