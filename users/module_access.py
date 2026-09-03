"""Liberação individual de módulos por usuário (grant-only).

O SUPERADMIN pode ligar um módulo para uma pessoa específica na tela de edição
de usuário (/users/manage/users/<id>/edit/). A checagem é **somada (OR)** à
regra normal de cada módulo — então nunca tira acesso, só concede.

Como usar num gate de módulo (menu ou view):

    from users.module_access import user_has_module
    ...
    return regra_normal(user) or user_has_module(user, 'cursos')

`MODULES` é a fonte única da lista exibida na tela. Para liberar um módulo novo,
basta acrescentar uma linha aqui e o ``or user_has_module(...)`` no gate dele.
"""

# (chave, rótulo exibido, grupo na tela). A ordem aqui é a ordem na tela.
MODULES = [
    ('comissionamento',        'Comissionamento',            'Gestão Comercial'),
    ('cursos',                 'Cursos Vivo',                'Gestão Comercial'),
    ('ponto',                  'Ponto e Férias',             'Gestão Comercial'),
    ('projetos',               'Projetos',                   'Operação'),
    ('clima',                  'Pesquisa de Clima',          'Operação'),
    ('entrevista_desligamento','Entrevista de Desligamento', 'Operação'),
    ('contestacao',            'Contestação',                'Operação'),
    ('impulso',                'Impulso',                    'Administrativo'),
    ('caixa',                  'Contagem de Caixa',          'Administrativo'),
    ('cartoes',                'Cartões',                    'Financeiro'),
]

MODULE_KEYS = {m[0] for m in MODULES}


def modules_by_group():
    """[(grupo, [(chave, rótulo), ...]), ...] preservando a ordem de MODULES."""
    grupos, ordem = {}, []
    for chave, rotulo, grupo in MODULES:
        if grupo not in grupos:
            grupos[grupo] = []
            ordem.append(grupo)
        grupos[grupo].append((chave, rotulo))
    return [(g, grupos[g]) for g in ordem]


def granted_modules(user):
    """Conjunto de chaves liberadas individualmente para ``user``.

    Cacheado na instância do usuário: o menu checa vários módulos por request e
    isto vira uma consulta só. Falha para conjunto vazio (nunca derruba a tela).
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return set()
    cached = getattr(user, '_granted_modules_cache', None)
    if cached is None:
        try:
            from .models import UserModuleAccess
            cached = set(UserModuleAccess.objects
                         .filter(user=user).values_list('module_key', flat=True))
        except Exception:
            cached = set()
        try:
            user._granted_modules_cache = cached
        except Exception:
            pass
    return cached


def user_has_module(user, key):
    """``user`` recebeu liberação individual do módulo ``key``?"""
    return key in granted_modules(user)


def set_user_modules(user, keys, granted_by=None):
    """Sincroniza as liberações de ``user`` para exatamente ``keys`` (grant-only).

    Cria as que faltam, remove as que saíram; ignora chaves desconhecidas.
    Devolve (adicionadas, removidas) para a mensagem de auditoria.
    """
    from .models import UserModuleAccess
    alvo = {k for k in keys if k in MODULE_KEYS}
    atuais = set(UserModuleAccess.objects.filter(user=user).values_list('module_key', flat=True))

    remover = atuais - alvo
    if remover:
        UserModuleAccess.objects.filter(user=user, module_key__in=remover).delete()

    adicionar = alvo - atuais
    for chave in adicionar:
        UserModuleAccess.objects.get_or_create(
            user=user, module_key=chave, defaults={'granted_by': granted_by})

    # Invalida o cache da instância, se houver.
    try:
        if hasattr(user, '_granted_modules_cache'):
            del user._granted_modules_cache
    except Exception:
        pass
    return adicionar, remover
