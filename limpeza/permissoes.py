"""Quem entra e o que faz na Limpeza.

Regra antiga (continua): o PADRÃO fora do grupo GERENTES não entra; gerencia quem
é SUPERADMIN/ADMINISTRATIVO ou está nos grupos Gerentes/ADMINS.
Novo (30/10/2026): o SUPERADMIN libera pessoas em /limpeza/acessos/ para
**Gerenciar** ou só **Ver** (ver = todas as lojas, sem mexer nos checklists).
"""
from functools import wraps

GERENCIAR, VER = 'GERENCIAR', 'VER'


def e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def papel_liberado(user):
    """'GERENCIAR', 'VER' ou None — a liberação do SUPERADMIN (guardada no request)."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return None
    if not hasattr(user, '_limpeza_papel'):
        from .models import AcessoLimpeza
        try:
            user._limpeza_papel = AcessoLimpeza.objects.filter(user=user).values_list('papel', flat=True).first()
        except Exception:  # noqa: BLE001 — tabela ainda não migrada noutro servidor
            user._limpeza_papel = None
    return user._limpeza_papel


def gerente_pela_hierarquia(user):
    if getattr(user, 'hierarchy', '') in ('SUPERADMIN', 'ADMINISTRATIVO') or user.is_superuser:
        return True
    return (user.groups.filter(name='Gerentes').exists()
            or user.groups.filter(name__iexact='ADMINS').exists())


def pode_entrar(user):
    from users.module_access import padrao_restrito
    return papel_liberado(user) is not None or not padrao_restrito(user, 'limpeza')


def pode_gerenciar(user):
    """Checklists, relatórios e arquivo, e registrar para qualquer setor."""
    return gerente_pela_hierarquia(user) or papel_liberado(user) == GERENCIAR


def ve_tudo(user):
    """Vê os registros de qualquer setor, os relatórios e o arquivo."""
    return pode_gerenciar(user) or papel_liberado(user) == VER


def ve_a_rede(user):
    """Relatório e arquivo de todas as lojas: SUPERADMIN e quem ele liberou (o gerente antigo vê os setores dele)."""
    return e_superadmin(user) or papel_liberado(user) is not None


def edita_qualquer_checklist(user):
    return e_superadmin(user) or papel_liberado(user) == GERENCIAR


def entrada_limpeza(view):
    """Trava de view: quem não entra volta para o início (API recebe 403), como o fechado_para_padrao."""
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not pode_entrar(request.user):
            from users.module_access import resposta_de_bloqueio
            return resposta_de_bloqueio(request, 'limpeza')
        return view(request, *args, **kwargs)
    return _view
