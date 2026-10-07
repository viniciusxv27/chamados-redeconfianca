"""Quem enxerga a Visão SAP — e de quais lojas.

Fica separado das views porque o menu do portal também precisa da resposta, e
importar views num context processor puxaria o módulo inteiro a cada request.
"""
import unicodedata

GESTORES = ('SUPERADMIN', 'ADMIN', 'ADMINISTRATIVO')


def ve_a_rede(user):
    """Vê a auditoria de todas as lojas.

    É a administração: conferência de nota fiscal e de dinheiro, com nome e
    documento de cliente na tela. Quem mais precisar da rede inteira entra pela
    liberação individual (``sap``), uma pessoa de cada vez.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    from users.module_access import user_has_module
    return bool(user.is_superuser
                or getattr(user, 'hierarchy', '') in GESTORES
                or user_has_module(user, 'sap')
                or user_has_module(user, 'sap.gestor'))


def e_gerente_de_loja(user):
    """PADRÃO do grupo GERENTES, com loja principal no cadastro.

    Entra na Visão SAP para resolver o que é da loja dele — e só isso: os
    erros das outras lojas trazem cliente e documento que não são assunto seu.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser or getattr(user, 'hierarchy', '') != 'PADRAO':
        return False
    if not getattr(user, 'sector_id', None):
        return False
    from users.module_access import e_do_grupo_gerentes
    return e_do_grupo_gerentes(user)


def pode_ver(user):
    """Abre a auditoria e marca linha como resolvida (o gerente, na loja dele)."""
    return ve_a_rede(user) or e_gerente_de_loja(user)


def no_menu_da_gestao(user):
    """O gerente acha a Visão SAP em "Gestão Administrativa".

    A administração continua com ela no menu ADMINISTRATIVO; quem vê a rede
    não ganha um segundo link.
    """
    return e_gerente_de_loja(user) and not ve_a_rede(user)


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


def normalizar_loja(nome):
    """"Loja Glória" e "GLÓRIA" viram a mesma chave: sem "Loja", sem acento, maiúsculas."""
    texto = ' '.join(str(nome or '').split())
    if texto.lower().startswith('loja'):
        texto = texto[4:].lstrip(' -–—:')
    texto = unicodedata.normalize('NFKD', texto)
    return ''.join(c for c in texto if not unicodedata.combining(c)).upper().strip()


def lojas_visiveis(user):
    """None quando vê a rede; senão o conjunto de lojas (normalizadas) que vê.

    A loja do gerente é o setor principal: o M2M de setores de quem é do
    escritório traz todas as lojas e abriria a rede inteira.
    """
    if ve_a_rede(user):
        return None
    if e_gerente_de_loja(user):
        return {normalizar_loja(user.sector.name)}
    return set()
