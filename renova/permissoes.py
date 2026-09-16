"""Quem faz, quem aprova, quem recebe e quem vê os Renovas."""
from django.db.models import Q


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


def grupo_gerentes():
    """O grupo GERENTES — há também "Gerentes 1..4" e "Gerente / ADM": o nome exato vem primeiro."""
    from communications.models import CommunicationGroup

    return (CommunicationGroup.objects.filter(name__iexact='GERENTES').first()
            or CommunicationGroup.objects.filter(name__icontains='GERENTES').first())


def e_gerente(user):
    if not (user and user.is_authenticated):
        return False
    grupo = grupo_gerentes()
    return bool(grupo and grupo.members.filter(pk=user.pk).exists())


def pode_aprovar(user, renova):
    """Aprova ou reprova a troca: o gerente da loja da avaliação (setor principal) ou o SUPERADMIN."""
    if e_superadmin(user):
        return True
    return bool(renova.loja_id and getattr(user, 'sector_id', None) == renova.loja_id and e_gerente(user))


def pode_ver_gestao(user, cfg=None):
    """Quadro de gestão: o SUPERADMIN e quem ele pôs como financeiro."""
    if not (user and user.is_authenticated):
        return False
    if e_superadmin(user):
        return True
    cfg = cfg or configuracao()
    return cfg.financeiro.filter(pk=user.pk).exists()


def pode_ver_modulo(user, cfg=None):
    if not (user and user.is_authenticated):
        return False
    cfg = cfg or configuracao()
    return pode_fazer(user, cfg) or pode_receber(user, cfg) or e_gerente(user) or pode_ver_gestao(user, cfg)


def renovas_visiveis(user, cfg=None):
    """SUPERADMIN, quem recebe e o financeiro veem todos; o gerente, os da loja; quem faz, os que fez."""
    from .models import Renova

    qs = Renova.objects.select_related('loja', 'criado_por', 'chamado', 'recebido_por', 'aprovacao_por')
    if e_superadmin(user) or pode_receber(user, cfg) or pode_ver_gestao(user, cfg):
        return qs
    filtro = Q(criado_por=user)
    if getattr(user, 'sector_id', None) and e_gerente(user):
        filtro |= Q(loja_id=user.sector_id)       # o gerente vê as trocas da loja dele
    return qs.filter(filtro)


def pode_ver(user, renova, cfg=None):
    if not (user and user.is_authenticated):
        return False
    return (renova.criado_por_id == user.pk or e_superadmin(user) or pode_receber(user, cfg)
            or pode_ver_gestao(user, cfg)
            or bool(renova.loja_id and renova.loja_id == getattr(user, 'sector_id', None) and e_gerente(user)))
