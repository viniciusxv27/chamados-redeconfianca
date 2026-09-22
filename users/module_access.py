"""Liberação individual de acessos por usuário (grant-only).

O SUPERADMIN liga, para uma pessoa específica, um módulo inteiro ou uma
permissão de detalhe dentro dele — na tela de edição de usuário
(/users/manage/users/<id>/edit/). A checagem é **somada (OR)** à regra normal
de cada módulo (hierarquia, grupo, configuração): nunca tira acesso, só
concede.

Como usar num gate (menu ou view):

    from users.module_access import user_has_module
    ...
    return regra_normal(user) or user_has_module(user, 'impulso.gestor')

Chaves têm dois níveis: ``'impulso'`` (entrar no módulo) e
``'impulso.gestor'`` (uma visão de detalhe dentro dele). Liberar o detalhe
libera junto a entrada no módulo, senão a pessoa teria a permissão e não
teria a porta.

``MODULOS`` é a fonte única do catálogo exibido na tela. Toda chave listada
tem um gate de verdade em ``GATES`` — é ele que o menu consulta e é ele que o
teste percorre para provar que nenhuma caixinha da tela é decorativa.

A exceção que vai no sentido contrário também mora aqui: ``padrao_restrito``
diz quando o PADRÃO fora do grupo GERENTES fica de fora de Chamados do setor,
Checklists ADM, Limpeza, Experiência Vivo e Contagem de Caixa — e a liberação
individual do módulo é justamente o que devolve o acesso a ele.
"""
from functools import wraps

# ---------------------------------------------------------------------------
# Catálogo: o que aparece na tela, por grupo
# ---------------------------------------------------------------------------
# chave, rótulo, grupo, descrição, acesso (a própria chave libera a entrada no
# módulo?), permissões de detalhe [(chave, rótulo, o que libera)].
MODULOS = [
    # ── Gestão Comercial ────────────────────────────────────────────────
    {'chave': 'comissionamento', 'rotulo': 'Comissionamento', 'grupo': 'Gestão Comercial',
     'acesso': True,
     'descricao': 'Ver o próprio comissionamento (para quem é PADRÃO e não está nos grupos de gerente/coordenador).',
     'permissoes': [
         ('comissionamento.projecao', 'Médias de comissão', 'Item "Médias de Comissão" do menu (projeção).'),
     ]},
    {'chave': 'cursos', 'rotulo': 'Cursos Vivo', 'grupo': 'Gestão Comercial',
     'acesso': True,
     'descricao': 'Ver e enviar comprovantes dos cursos, mesmo fora dos grupos/setores cobrados.',
     'permissoes': [
         ('cursos.gestao', 'Gestor dos cursos', 'Publicar curso, definir quem faz e aprovar comprovantes (quadro de gestão).'),
     ]},
    {'chave': 'ponto', 'rotulo': 'Ponto e Férias', 'grupo': 'Gestão Comercial',
     'acesso': True,
     'descricao': 'Ver o próprio ponto, férias e escala quando o módulo está restrito a um grupo.',
     'permissoes': []},

    # ── Pessoal ─────────────────────────────────────────────────────────
    {'chave': 'rh', 'rotulo': 'Pessoal (RH/DP)', 'grupo': 'Pessoal',
     'acesso': False,
     'descricao': 'As telas de administração de pessoal, hoje da hierarquia ADMINISTRAÇÃO.',
     'permissoes': [
         ('rh.gestao', 'Administrar pessoal',
          'Folha de ponto, documentos, contracheque, ponto da equipe, férias e escala — como a ADMINISTRAÇÃO.'),
     ]},
    {'chave': 'talentos', 'rotulo': 'Banco de Talentos', 'grupo': 'Pessoal',
     'acesso': True,
     'descricao': 'Pesquisar e importar currículos (dado pessoal: nasce fechado).',
     'permissoes': []},
    {'chave': 'clima', 'rotulo': 'Pesquisa de Clima', 'grupo': 'Pessoal',
     'acesso': True,
     'descricao': 'Gerenciar a pesquisa de clima (o mesmo que a tela de acessos da pesquisa).',
     'permissoes': []},
    {'chave': 'entrevista_desligamento', 'rotulo': 'Entrevista de Desligamento', 'grupo': 'Pessoal',
     'acesso': True,
     'descricao': 'Ver e conduzir entrevistas de desligamento.',
     'permissoes': []},

    # ── Operação ────────────────────────────────────────────────────────
    {'chave': 'projetos', 'rotulo': 'Projetos', 'grupo': 'Operação',
     'acesso': True,
     'descricao': 'Ver os projetos, mesmo sem o setor liberado.',
     'permissoes': [
         ('projetos.gestao', 'Gerenciar projetos', 'Item "Projetos – Gerenciar" (o mesmo que o grupo Gestores de Projetos).'),
     ]},
    {'chave': 'contestacao', 'rotulo': 'Contestação', 'grupo': 'Operação',
     'acesso': True,
     'descricao': 'Abrir o módulo de contestação.',
     'permissoes': []},
    {'chave': 'fornecedores', 'rotulo': 'Fornecedores', 'grupo': 'Operação',
     'acesso': True,
     'descricao': 'Cadastro de fornecedores (o mesmo que o grupo Gestores de Fornecedores).',
     'permissoes': []},
    {'chave': 'compras', 'rotulo': 'Compras', 'grupo': 'Operação',
     'acesso': True,
     'descricao': 'Compras e formas de pagamento (o mesmo que o grupo Gestores de Compras).',
     'permissoes': []},
    {'chave': 'almoxarifado', 'rotulo': 'Almoxarifado', 'grupo': 'Operação',
     'acesso': False,
     'descricao': 'Estoque e solicitações de itens.',
     'permissoes': [
         ('almoxarifado.gestao', 'Gerir o estoque', 'Menu e telas de gestão do inventário.'),
         ('almoxarifado.aprovar', 'Aprovar solicitações', 'Aprovar ou reprovar pedidos de itens.'),
     ]},
    {'chave': 'chamados', 'rotulo': 'Chamados', 'grupo': 'Operação',
     'acesso': False,
     'descricao': 'Todo mundo abre chamado e acompanha os seus; aqui é o que vai além disso.',
     'permissoes': [
         ('chamados.setor', 'Ver os chamados do setor',
          'Para PADRÃO fora do grupo GERENTES, que sem isto só vê os que abriu e os atribuídos a ele.'),
         ('chamados.todos', 'Ver chamados de todos os setores', 'Como a hierarquia ADMINISTRAÇÃO.'),
         ('painel.gestao', 'Painel de gestão', 'Painel de gestão de chamados (hoje SUPERVISOR e acima).'),
         ('painel.admin', 'Painel administrativo', 'Painel administrativo (hoje ADMINISTRAÇÃO e acima).'),
     ]},
    {'chave': 'checklists', 'rotulo': 'Checklists ADM', 'grupo': 'Operação',
     'acesso': True,
     'descricao': 'Entrar nos Checklists ADM sendo PADRÃO fora do grupo GERENTES.',
     'permissoes': []},
    {'chave': 'limpeza', 'rotulo': 'Limpeza', 'grupo': 'Operação',
     'acesso': True,
     'descricao': 'Registrar e consultar limpezas sendo PADRÃO fora do grupo GERENTES.',
     'permissoes': []},
    {'chave': 'experiencia', 'rotulo': 'Experiência Vivo', 'grupo': 'Operação',
     'acesso': True,
     'descricao': 'Preencher e consultar a Experiência Vivo sendo PADRÃO fora do grupo GERENTES.',
     'permissoes': []},

    # ── Administrativo ──────────────────────────────────────────────────
    {'chave': 'impulso', 'rotulo': 'Impulso', 'grupo': 'Administrativo',
     'acesso': True,
     'descricao': "Entrar no Impulso sem estar nos grupos ESCRITÓRIO (ADM) ou ADM's LOJAS.",
     'permissoes': [
         ('impulso.gestor', 'Gestor do Impulso', 'Aprovar e avaliar metas, conferir o Conectar, concluir projeto foco.'),
     ]},
    {'chave': 'caixa', 'rotulo': 'Contagem de Caixa', 'grupo': 'Administrativo',
     'acesso': True,
     'descricao': 'Contar o caixa mesmo sem estar lotado numa loja — e, sendo PADRÃO fora do grupo GERENTES, entrar no módulo.',
     'permissoes': [
         ('caixa.gestor', 'Gestor do caixa', 'Ver todas as lojas e importar a base.'),
     ]},
    {'chave': 'treinamentos', 'rotulo': 'Treinamentos', 'grupo': 'Administrativo',
     'acesso': False,
     'descricao': 'Todo mundo assiste; aqui é quem publica.',
     'permissoes': [
         ('treinamentos.gestao', 'Publicar e gerenciar treinamentos', 'Upload, categorias, ativar/desativar.'),
     ]},
    {'chave': 'quiz', 'rotulo': 'Quiz', 'grupo': 'Administrativo',
     'acesso': False,
     'descricao': 'Todo mundo joga nas salas para que foi chamado; aqui é quem cria.',
     'permissoes': [
         ('quiz.gestao', 'Criar e administrar quizzes',
          'Criar quizzes e perguntas, montar salas, conduzir a partida e ver resultados e relatórios.'),
     ]},
    {'chave': 'comunicados', 'rotulo': 'Comunicados', 'grupo': 'Administrativo',
     'acesso': False,
     'descricao': 'Todo mundo lê; aqui é quem escreve.',
     'permissoes': [
         ('comunicados.criar', 'Criar comunicados', 'Item "Novo Comunicado" e a tela de criação.'),
     ]},
    {'chave': 'usuarios', 'rotulo': 'Usuários', 'grupo': 'Administrativo',
     'acesso': False,
     'descricao': 'A área de gestão de usuários. A edição de cadastro continua só com SUPERADMIN e ADMINISTRAÇÃO.',
     'permissoes': [
         ('usuarios.gerenciar', 'Gerenciar usuários', 'Listar, conferir, exportar e analisar pré-cadastros.'),
     ]},
    {'chave': 'relatorios', 'rotulo': 'Relatórios', 'grupo': 'Administrativo',
     'acesso': False,
     'descricao': 'Relatórios do portal.',
     'permissoes': [
         ('relatorios.ver', 'Ver relatórios', 'Hoje SUPERVISOR e acima.'),
     ]},
    {'chave': 'categorias', 'rotulo': 'Categorias de chamado', 'grupo': 'Administrativo',
     'acesso': False,
     'descricao': 'Categorias dos chamados do setor.',
     'permissoes': [
         ('categorias.editar', 'Editar categorias do setor', 'Hoje ADMINISTRATIVO e acima.'),
     ]},
    {'chave': 'arquivos', 'rotulo': 'Arquivos', 'grupo': 'Administrativo',
     'acesso': False,
     'descricao': 'Biblioteca de arquivos do portal.',
     'permissoes': [
         ('arquivos.enviar', 'Enviar arquivos', 'Hoje ADMINISTRATIVO e acima.'),
     ]},
    {'chave': 'popups', 'rotulo': 'Popups do portal', 'grupo': 'Administrativo',
     'acesso': False,
     'descricao': 'Os avisos que aparecem ao entrar no portal.',
     'permissoes': [
         ('popups.gerenciar', 'Gerenciar popups', 'Criar, editar, ligar e desligar (hoje só SUPERADMIN).'),
     ]},

    # ── Financeiro ──────────────────────────────────────────────────────
    {'chave': 'cartoes', 'rotulo': 'Cartões', 'grupo': 'Financeiro',
     'acesso': True,
     'descricao': 'Entrar no módulo de cartões sem ser responsável por um cartão.',
     'permissoes': []},
    {'chave': 'cs', 'rotulo': 'C$ (moeda interna)', 'grupo': 'Financeiro',
     'acesso': False,
     'descricao': 'Saldo e transações de C$.',
     'permissoes': [
         ('cs.gerenciar', 'Gerenciar C$', 'Creditar, ajustar e aprovar transações (hoje ADMINISTRATIVO e acima).'),
     ]},
    {'chave': 'mercadinho', 'rotulo': 'Mercadinho', 'grupo': 'Financeiro',
     'acesso': False,
     'descricao': 'Todo mundo resgata; aqui é quem cuida da prateleira.',
     'permissoes': [
         ('mercadinho.gerenciar', 'Gerenciar prêmios e resgates', 'Cadastrar prêmios e atender resgates (hoje ADMINISTRATIVO e acima).'),
     ]},
]

# ---------------------------------------------------------------------------
# Gates: para cada chave, quem decide de verdade (regra normal OU liberação)
# ---------------------------------------------------------------------------
# Valor: 'metodo:<nome>' chama user.<nome>(); 'pacote.modulo:funcao' chama a
# função com o usuário. Todas já incluem `user_has_module` por dentro — este
# mapa é só o índice, para o menu e para o teste acharem o gate certo.
GATES = {
    'comissionamento': 'users.commission_views:pode_ver_comissionamento',
    'comissionamento.projecao': 'users.module_access:_gate_projecao',
    'cursos': 'cursos.permissions:pode_ver',
    'cursos.gestao': 'cursos.permissions:e_gestor',
    'ponto': 'users.module_access:_gate_ponto',
    'rh.gestao': 'metodo:can_manage_rh',
    'talentos': 'curriculos.permissions:pode_usar',
    'clima': 'feedback.views:_can_manage_surveys',
    'entrevista_desligamento': 'feedback.views:_can_access_exit_interview',
    'projetos': 'projects.views:user_can_access_projects',
    'projetos.gestao': 'projects.views:user_can_manage_projects_menu',
    'contestacao': 'contestacao.views:_can_access_contestation_module',
    'fornecedores': 'suppliers.views:user_can_manage_suppliers',
    'compras': 'purchases.views:user_can_manage_purchases',
    'almoxarifado.gestao': 'assets.context_processors:e_gestor_de_inventario',
    'almoxarifado.aprovar': 'assets.views:can_approve_requests',
    'chamados.setor': 'users.module_access:_gate_chamados_setor',
    'chamados.todos': 'metodo:can_view_all_tickets',
    'checklists': 'users.module_access:_gate_checklists',
    'limpeza': 'users.module_access:_gate_limpeza',
    'experiencia': 'users.module_access:_gate_experiencia',
    'painel.gestao': 'metodo:can_access_management_panel',
    'painel.admin': 'metodo:can_access_admin_panel',
    'impulso': 'impulso.utils:is_impulso_member',
    'impulso.gestor': 'impulso.utils:is_impulso_manager',
    'caixa': 'contagem_caixa.permissions:pode_ver_caixa',
    'caixa.gestor': 'contagem_caixa.permissions:e_gestor',
    'treinamentos.gestao': 'trainings.views:pode_gerenciar_treinamentos',
    'quiz.gestao': 'quiz.permissoes:pode_gerenciar',
    'comunicados.criar': 'metodo:can_create_communications',
    'usuarios.gerenciar': 'metodo:can_manage_users',
    'relatorios.ver': 'metodo:can_view_reports',
    'categorias.editar': 'metodo:can_edit_sector_categories',
    'arquivos.enviar': 'metodo:can_upload_files',
    'popups.gerenciar': 'portal_popups.views:_can_manage_popups',
    'cartoes': 'cartoes.permissions:can_access_cartoes',
    'cs.gerenciar': 'metodo:can_manage_cs',
    'mercadinho.gerenciar': 'metodo:can_manage_prizes',
}


def _gate_projecao(user):
    """Menu "Médias de Comissão": SUPERADMIN, ou liberado — e, em qualquer
    caso, só para quem já enxerga o comissionamento."""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if user.is_superuser or getattr(user, 'hierarchy', '') == 'SUPERADMIN':
        return True
    if not user_has_module(user, 'comissionamento.projecao'):
        return False
    from users.commission_views import pode_ver_comissionamento
    return pode_ver_comissionamento(user)


def _gate_ponto(user):
    """O módulo de ponto responde pela própria configuração (ativo/restrito)."""
    try:
        from tangerino.models import ConfiguracaoTangerino
        return ConfiguracaoTangerino.get().libera(user)
    except Exception:  # noqa: BLE001
        return False


def _gate_chamados_setor(user):
    """Vê chamado pelo setor: a regra de sempre, menos o PADRÃO restrito."""
    return bool(user.can_view_sector_tickets()) and not padrao_restrito(user, 'chamados')


# Os três módulos abaixo não tinham regra de entrada: todo mundo logado entrava.
# O gate agora é só a trava do PADRÃO — quem não é PADRÃO restrito segue entrando.
def _gate_checklists(user):
    return not padrao_restrito(user, 'checklists')


def _gate_limpeza(user):
    return not padrao_restrito(user, 'limpeza')


def _gate_experiencia(user):
    return not padrao_restrito(user, 'experiencia')


# ---------------------------------------------------------------------------
# Derivados do catálogo
# ---------------------------------------------------------------------------
def _todas_as_chaves():
    chaves = set()
    for m in MODULOS:
        if m['acesso']:
            chaves.add(m['chave'])
        for chave, _r, _d in m['permissoes']:
            chaves.add(chave)
    return chaves


MODULE_KEYS = _todas_as_chaves()

# Compatibilidade com quem importava a lista antiga (chave, rótulo, grupo).
MODULES = [(m['chave'], m['rotulo'], m['grupo']) for m in MODULOS if m['acesso']]

# chave de detalhe -> chave do módulo que precisa ir junto
_PAI = {chave: m['chave'] for m in MODULOS if m['acesso']
        for chave, _r, _d in m['permissoes']}

ROTULOS = {}
for _m in MODULOS:
    if _m['acesso']:
        ROTULOS[_m['chave']] = _m['rotulo']
    for _c, _r, _d in _m['permissoes']:
        ROTULOS[_c] = f"{_m['rotulo']} · {_r}"


def catalogo_por_grupo():
    """[(grupo, [modulo, ...]), ...] na ordem de MODULOS, para a tela."""
    grupos, ordem = {}, []
    for m in MODULOS:
        if m['grupo'] not in grupos:
            grupos[m['grupo']] = []
            ordem.append(m['grupo'])
        grupos[m['grupo']].append(m)
    return [(g, grupos[g]) for g in ordem]


def modules_by_group():
    """Forma antiga: [(grupo, [(chave, rótulo), ...])]. Só as chaves de acesso."""
    saida, ordem, grupos = [], [], {}
    for chave, rotulo, grupo in MODULES:
        if grupo not in grupos:
            grupos[grupo] = []
            ordem.append(grupo)
        grupos[grupo].append((chave, rotulo))
    for g in ordem:
        saida.append((g, grupos[g]))
    return saida


def rotulos_de(chaves):
    return [ROTULOS.get(c, c) for c in sorted(chaves)]


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------
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
    """``user`` recebeu liberação individual da chave ``key``?"""
    return key in granted_modules(user)


def tem_acesso(user, chave):
    """Regra normal OU liberação individual — o que o menu e o teste perguntam.

    Falha para False: uma chave sem gate (ou gate que explode) nunca abre
    porta nenhuma.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    spec = GATES.get(chave)
    if not spec:
        return False
    try:
        if spec.startswith('metodo:'):
            return bool(getattr(user, spec[len('metodo:'):])())
        modulo, funcao = spec.split(':')
        from importlib import import_module
        return bool(getattr(import_module(modulo), funcao)(user))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# PADRÃO fora do grupo GERENTES: módulos fechados
# ---------------------------------------------------------------------------
# O PADRÃO que não é gerente não entra em Checklists ADM, Limpeza, Experiência
# Vivo e Contagem de Caixa, e em Chamados vê só o que é dele. As outras
# hierarquias, o grupo GERENTES e o superusuário seguem como antes.
#
# A exceção para uma pessoa é a mesma tela de liberação individual: qualquer
# uma das chaves listadas para o módulo devolve o acesso de antes. Assim ninguém
# precisa entrar no GERENTES só para contar caixa — o grupo pesa em comissão,
# contestação, escala e aprovações.
GRUPO_GERENTES = 'GERENTES'

LIBERACOES_DO_PADRAO = {
    'chamados': ('chamados.setor', 'chamados.todos'),
    'checklists': ('checklists',),
    'limpeza': ('limpeza',),
    'experiencia': ('experiencia',),
    'caixa': ('caixa', 'caixa.gestor'),
}


def e_do_grupo_gerentes(user):
    """Está no grupo de comunicação GERENTES?

    Nome exato, como em ``User.can_create_contestations``: existem também
    "GERENTES (CHECKLIST)", "Gerentes 1..4" e "Gerente / ADM", e um
    ``icontains`` com ``.first()`` depende da ordem alfabética para acertar.
    Cacheado na instância, como ``granted_modules``: o menu pergunta várias
    vezes no mesmo request.
    """
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    cached = getattr(user, '_e_do_grupo_gerentes_cache', None)
    if cached is None:
        try:
            cached = user.communication_groups.filter(name__iexact=GRUPO_GERENTES).exists()
        except Exception:  # noqa: BLE001
            cached = False
        try:
            user._e_do_grupo_gerentes_cache = cached
        except Exception:  # noqa: BLE001
            pass
    return cached


def padrao_restrito(user, modulo):
    """``user`` é o PADRÃO que fica de fora de ``modulo``?

    True só para quem é PADRÃO, não é superusuário, não está no grupo GERENTES
    e não tem nenhuma das liberações individuais do módulo
    (``LIBERACOES_DO_PADRAO``). Para qualquer outra pessoa, False — e o módulo
    segue com a regra que já tinha.

    É a pergunta única que menu, views e APIs desses módulos fazem, para o menu
    esconder exatamente o que o servidor bloqueia. Módulo desconhecido levanta
    KeyError: nome digitado errado não pode virar porta aberta.
    """
    chaves = LIBERACOES_DO_PADRAO[modulo]
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if user.is_superuser or getattr(user, 'hierarchy', '') != 'PADRAO':
        return False
    if e_do_grupo_gerentes(user):
        return False
    return not any(user_has_module(user, chave) for chave in chaves)


def _rotulo_do_modulo(modulo):
    return next((m['rotulo'] for m in MODULOS if m['chave'] == modulo), modulo)


def _quer_json(request):
    """Chamada de API/AJAX: quem pergunta espera JSON, não uma tela."""
    return ('/api/' in request.path
            or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in request.headers.get('Accept', '')
            or (request.content_type or '').startswith('application/json'))


def resposta_de_bloqueio(request, modulo):
    """O que o PADRÃO restrito recebe ao bater na porta de ``modulo``."""
    aviso = (f'O módulo {_rotulo_do_modulo(modulo)} não está liberado para o seu usuário. '
             'Se você precisa dele, fale com o seu gestor.')
    if _quer_json(request):
        from django.http import JsonResponse
        return JsonResponse({'success': False, 'error': aviso}, status=403)
    from django.contrib import messages
    from django.shortcuts import redirect
    messages.error(request, aviso)
    return redirect('home')


def fechado_para_padrao(modulo):
    """Trava de view: ``padrao_restrito`` não passa.

    Vai logo abaixo do ``@login_required`` (e acima do ``@require_POST``), para
    barrar também a URL digitada à mão e o POST direto — esconder o menu não
    basta. Tela volta para o início com o aviso; API/AJAX recebe 403 em JSON,
    porque um redirect chegaria ao ``fetch`` como HTML.
    """
    LIBERACOES_DO_PADRAO[modulo]  # nome errado quebra ao importar, não em produção

    def decorador(view):
        @wraps(view)
        def _view(request, *args, **kwargs):
            if padrao_restrito(request.user, modulo):
                return resposta_de_bloqueio(request, modulo)
            return view(request, *args, **kwargs)
        return _view
    return decorador


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------
def set_user_modules(user, keys, granted_by=None):
    """Sincroniza as liberações de ``user`` para exatamente ``keys`` (grant-only).

    Cria as que faltam, remove as que saíram; ignora chaves desconhecidas.
    Uma permissão de detalhe traz junto a entrada no módulo dela.
    Devolve (adicionadas, removidas) para a mensagem de auditoria.
    """
    from .models import UserModuleAccess
    alvo = {k for k in keys if k in MODULE_KEYS}
    alvo |= {_PAI[k] for k in list(alvo) if k in _PAI}
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
