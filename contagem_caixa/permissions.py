"""Quem enxerga a Contagem de Caixa.

Fica separado das views porque o menu do portal também precisa da resposta, e
importar views num context processor puxaria o módulo inteiro a cada request.
"""
from users.models import Sector

GESTORES = ('SUPERADMIN', 'ADMIN', 'ADMINISTRATIVO')


def e_gestor(user):
    """Vê todas as lojas e importa a base."""
    if not user or not user.is_authenticated:
        return False
    return bool(user.is_superuser or getattr(user, 'hierarchy', '') in GESTORES)


def lojas():
    """Só os setores que são loja de fato.

    O portal tem 38 setores e a maioria é área administrativa (DP, Compras,
    Jurídico…), que não tem caixa. Loja é quem tem código ADABAS — o mesmo
    código que a base de vendas traz — ou nome de loja, para as que ainda não
    têm o código cadastrado.
    """
    from django.db.models import Q
    return (Sector.objects.filter(Q(name__istartswith='loja') | ~Q(adabas=''))
            .order_by('name'))


def lojas_do_usuario(user):
    """Lojas que a pessoa enxerga: todas para gestor, a dela para os demais."""
    if e_gestor(user):
        return lojas()
    if not user or not user.is_authenticated:
        return lojas().none()
    ids = {s.id for s in user.sectors.all()}
    if user.sector_id:
        ids.add(user.sector_id)
    return lojas().filter(id__in=ids)


def pode_ver_caixa(user):
    """Gestor ou alguém lotado numa loja — o resto não tem caixa para contar."""
    if not user or not user.is_authenticated:
        return False
    from users.module_access import user_has_module
    return e_gestor(user) or lojas_do_usuario(user).exists() or user_has_module(user, 'caixa')
