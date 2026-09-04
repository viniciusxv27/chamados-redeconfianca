"""Quem usa o banco de talentos.

Currículo é dado pessoal de gente que ainda nem trabalha aqui: endereço,
telefone, histórico. O acesso nasce fechado — SUPERADMIN e os grupos que ele
escolher na configuração.
"""


def e_superadmin(user):
    if not (user and user.is_authenticated):
        return False
    return bool(user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN')


def pode_usar(user, cfg=None):
    """Enxerga e pesquisa o banco de talentos."""
    if not (user and user.is_authenticated):
        return False
    if e_superadmin(user):
        return True
    try:
        from .models import ConfiguracaoCurriculos
        cfg = cfg or ConfiguracaoCurriculos.get()
        ids = list(cfg.grupos.values_list('id', flat=True))
        if not ids:
            return False
        return user.communication_groups.filter(id__in=ids).exists()
    except Exception:                                        # nunca derruba a tela
        return False
