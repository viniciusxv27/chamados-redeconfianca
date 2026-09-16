"""Quem usa o módulo, quem vê cada apresentação e quem mexe em cada template."""
from django.db.models import Q


def e_superadmin(user):
    return bool(user and user.is_authenticated
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def configuracao():
    from .models import ConfiguracaoApresentacoes

    return ConfiguracaoApresentacoes.get()


def pode_usar(user, cfg=None):
    """O SUPERADMIN sempre; os demais só se o SUPERADMIN liberou."""
    if not (user and user.is_authenticated):
        return False
    if e_superadmin(user):
        return True
    cfg = cfg or configuracao()
    return cfg.liberados.filter(pk=user.pk).exists()


def apresentacoes_visiveis(user):
    """O SUPERADMIN vê todas; cada um, as suas."""
    from .models import Apresentacao

    qs = Apresentacao.objects.select_related('dono', 'template')
    return qs if e_superadmin(user) else qs.filter(dono=user)


def pode_editar(user, apresentacao):
    return bool(user and user.is_authenticated
                and (apresentacao.dono_id == user.pk or e_superadmin(user)))


def templates_visiveis(user):
    """Os da rede (para todos) e os que a própria pessoa importou; o SUPERADMIN vê todos."""
    from .models import TemplateApresentacao

    qs = TemplateApresentacao.objects.filter(ativo=True)
    if e_superadmin(user):
        return qs
    return qs.filter(Q(padrao_da_rede=True) | Q(criado_por=user))


def pode_editar_template(user, template):
    if e_superadmin(user):
        return True
    # O template do sistema é da rede: só o SUPERADMIN mexe nele.
    return bool(template.origem != template.Origem.SISTEMA and template.criado_por_id == user.pk)


def pode_ver_midia(user, midia):
    """Mídia de apresentação: quem pode editar a apresentação. De template: quem vê o template.

    Mídia solta (enviada antes de existir apresentação, como os prints do
    formulário) só para quem enviou e para o SUPERADMIN.
    """
    if not (user and user.is_authenticated):
        return False
    if e_superadmin(user) or midia.dono_id == user.pk:
        return True
    if midia.apresentacao_id:
        return pode_editar(user, midia.apresentacao)
    if midia.template_id:
        return templates_visiveis(user).filter(pk=midia.template_id).exists()
    return False
