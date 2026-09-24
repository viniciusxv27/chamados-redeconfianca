"""Quem enxerga a Visão SAP.

Fica separado das views porque o menu do portal também precisa da resposta, e
importar views num context processor puxaria o módulo inteiro a cada request.
"""
GESTORES = ('SUPERADMIN', 'ADMIN', 'ADMINISTRATIVO')


def pode_ver(user):
    """Abre a auditoria e marca linha como resolvida.

    Nasce na mão da administração: é conferência de nota fiscal e de dinheiro,
    com nome e documento de cliente na tela. Quem mais precisar entra pela
    liberação individual (``sap``), uma pessoa de cada vez.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    from users.module_access import user_has_module
    return bool(user.is_superuser
                or getattr(user, 'hierarchy', '') in GESTORES
                or user_has_module(user, 'sap')
                or user_has_module(user, 'sap.gestor'))


def e_gestor(user):
    """Manda atualizar o espelho lendo o MySQL do SAP.

    É uma leitura de ~30 s no banco do SAP: não é para qualquer um sair
    disparando, mas quem administra a auditoria precisa poder.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    from users.module_access import user_has_module
    return bool(user.is_superuser
                or getattr(user, 'hierarchy', '') in GESTORES
                or user_has_module(user, 'sap.gestor'))
