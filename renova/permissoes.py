"""Quem faz, quem recebe e quem vê os Renovas."""


def e_superadmin(user):
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def configuracao():
    from .models import ConfiguracaoRenova

    return ConfiguracaoRenova.get()


def setor_recebedor(cfg=None):
    """O setor da categoria do chamado — é ele que recebe os aparelhos."""
    cfg = cfg or configuracao()
    categoria = cfg.categoria
    return categoria.sector if categoria else None


def pode_fazer(user, cfg=None):
    """Preenche o checklist: o SUPERADMIN ou quem ele habilitou."""
    if not (user and user.is_authenticated):
        return False
    if e_superadmin(user):
        return True
    cfg = cfg or configuracao()
    return cfg.habilitados.filter(pk=user.pk).exists()


def pode_receber(user, cfg=None):
    """Marca a chegada: o SUPERADMIN ou quem é do setor da categoria do chamado.

    Vale o setor principal e os vinculados. As telas de chamado olham só o
    principal e deixariam de fora quem está no setor pelo vínculo.
    """
    if not (user and user.is_authenticated):
        return False
    if e_superadmin(user):
        return True
    setor = setor_recebedor(cfg)
    if setor is None:
        return False
    return user.sector_id == setor.pk or user.sectors.filter(pk=setor.pk).exists()


def pode_ver_modulo(user, cfg=None):
    if not (user and user.is_authenticated):
        return False
    cfg = cfg or configuracao()
    return pode_fazer(user, cfg) or pode_receber(user, cfg)


def renovas_visiveis(user, cfg=None):
    """Quem recebe (e o SUPERADMIN) vê todos; quem faz vê os que fez."""
    from .models import Renova

    qs = Renova.objects.select_related('loja', 'criado_por', 'chamado', 'recebido_por')
    if e_superadmin(user) or pode_receber(user, cfg):
        return qs
    return qs.filter(criado_por=user)


def pode_ver(user, renova, cfg=None):
    if not (user and user.is_authenticated):
        return False
    return renova.criado_por_id == user.pk or e_superadmin(user) or pode_receber(user, cfg)
