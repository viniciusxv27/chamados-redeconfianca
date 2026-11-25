"""Quem enxerga a Visão SAP — e de quais lojas.

Fica separado das views porque o menu do portal também precisa da resposta, e
importar views num context processor puxaria o módulo inteiro a cada request.
"""
import unicodedata

# 26/11/2026: "A visão SAP, todo mundo deveria ter acesso, porém, de início, só liberado o
# ESTOQUE, os demais, só naquele campo de liberação dentro de acessos e gestores." Todo mundo
# entra na Visão SAP e cai no Estoque; Auditoria, Painel, Renova e Automação só para o
# SUPERADMIN e para quem estiver liberado (``sap``, em /sap/gestores/ ou na edição do usuário)
# ou for gestor (``sap.gestor``). ADMIN/ADMINISTRATIVO e o gerente de loja deixaram de entrar
# sozinhos — quem precisar é liberado pessoa a pessoa (e aí vê a rede inteira).
GESTORES = ('SUPERADMIN',)


def _e_superadmin(user):
    return bool(user and getattr(user, 'is_authenticated', False)
                and (user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN'))


def ve_a_rede(user):
    """Vê a auditoria de todas as lojas: o SUPERADMIN e quem foi liberado (``sap`` ou ``sap.gestor``)."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if _e_superadmin(user):
        return True
    from users.module_access import user_has_module
    return bool(user_has_module(user, CHAVE_ACESSO) or user_has_module(user, CHAVE_GESTOR))


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
    """Abre a auditoria, o painel e a Renova e marca linha como resolvida — só quem foi liberado."""
    return ve_a_rede(user)


CHAVE_ESTOQUE = 'sap.estoque'


def pode_ver_estoque(user):
    """Abre /sap/estoque/: todo mundo logado (24/11/2026, "deixar liberado pra todos").

    Estoque não tem cliente nem documento — saldo, serial e preço servem a qualquer
    loja. Antes era quem via a Visão SAP mais uma lista do SUPERADMIN (sap.estoque);
    as liberações antigas ficam no banco sem efeito.
    """
    return bool(user and getattr(user, 'is_authenticated', False) and getattr(user, 'is_active', True))


def so_estoque(user):
    """Entra só no estoque: não vê a auditoria, o painel nem a Renova (o padrão desde 26/11/2026)."""
    return pode_ver_estoque(user) and not pode_ver(user)


# Centros do estoque de LP (26/11/2026): chegam na mesma MB52 das lojas, desde a carga de
# 25/11 13:49 — "LOJA SHOPPING VILA VELHA ES", "LOJA GUARAPARI"... Ficam FORA do estoque
# que todo mundo vê e vão para a aba "Estoque LP", só do SUPERADMIN (``estoque.fotografia``
# separa as duas). Centro novo de LP: acrescentar aqui (a imagem do pedido usa "E018" = "018E").
CENTROS_LP = frozenset({
    '018E', '019E', '020E', '021E', '022E', '023E', '024E',
    '101E', '104E', '105E', '106E', '107E', '108E', '109E', '110E', '113E', '114E',
})


def e_centro_lp(centro):
    """"018E", "E018" e "e018 " são o mesmo centro de LP."""
    texto = str(centro or '').strip().upper()
    if len(texto) == 4 and texto[0].isalpha() and texto[1:].isdigit():
        texto = texto[1:] + texto[0]
    return texto in CENTROS_LP


def pode_ver_estoque_lp(user):
    """A aba Estoque LP: só o SUPERADMIN."""
    return _e_superadmin(user)


def no_menu_da_gestao(user):
    """Não há mais Visão SAP recortada pela loja do gerente (26/11/2026): o menu tem uma entrada só."""
    return False


def e_gestor(user):
    """Manda atualizar o espelho lendo o MySQL do SAP.

    É uma leitura de ~30 s no banco do SAP: não é para qualquer um sair
    disparando, mas quem administra a auditoria precisa poder.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if _e_superadmin(user):
        return True
    from users.module_access import user_has_module
    return user_has_module(user, CHAVE_GESTOR)


CHAVE_GESTOR = 'sap.gestor'
# Liberação individual da Visão SAP (rede inteira), marcada pelo SUPERADMIN em /sap/gestores/.
CHAVE_ACESSO = 'sap'


def pode_escolher_gestores(user):
    """Só o SUPERADMIN escolhe quem gere a Visão SAP."""
    return _e_superadmin(user)


def gestores_escolhidos():
    """Quem o SUPERADMIN marcou como gestor do módulo (a liberação ``sap.gestor``).

    É a mesma liberação da tela de edição de usuário — uma lista só. Essas pessoas
    veem a rede inteira, atualizam do SAP e recebem aviso a cada linha resolvida.
    """
    from django.contrib.auth import get_user_model
    return (get_user_model().objects.filter(is_active=True, module_accesses__module_key=CHAVE_GESTOR)
            .distinct().order_by('first_name', 'last_name'))


# Lojas que o SAP às vezes escreve pelo código da filial: na tela viram o nome
# (e o filtro de uma pega as linhas das duas grafias).
CODIGOS_DE_LOJA = {'L220': 'ITAIM PAULISTA', 'L222': 'CARAPICUIBA', 'L223': 'EMBU DAS ARTES'}


def normalizar_loja(nome):
    """"Loja Glória" e "GLÓRIA" viram a mesma chave: sem "Loja", sem acento, maiúsculas.

    O código da filial ("L220") vira o nome da loja ("ITAIM PAULISTA").
    """
    texto = ' '.join(str(nome or '').split())
    if texto.lower().startswith('loja'):
        texto = texto[4:].lstrip(' -–—:')
    texto = unicodedata.normalize('NFKD', texto)
    texto = ''.join(c for c in texto if not unicodedata.combining(c)).upper().strip()
    return CODIGOS_DE_LOJA.get(texto, texto)


def nome_da_loja(pdv):
    """O nome para mostrar: "L220" → "ITAIM PAULISTA"; o resto como veio do SAP."""
    chave = ' '.join(str(pdv or '').split()).upper()
    return CODIGOS_DE_LOJA.get(chave, pdv or '')


def lojas_visiveis(user):
    """None quando vê a rede; senão o conjunto de lojas (normalizadas) que vê.

    A loja do gerente é o setor principal: o M2M de setores de quem é do
    escritório traz todas as lojas e abriria a rede inteira.
    """
    if ve_a_rede(user):
        return None
    return set()
