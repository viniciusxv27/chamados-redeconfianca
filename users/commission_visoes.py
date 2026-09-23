"""Quem abre cada visão do comissionamento.

O ``/users/commission/`` não é uma tela só: o papel da pessoa decide qual visão
ela recebe (consultor, recepcionista, gerente, coordenação, a parte, projeção).
Quem podia abrir cada uma estava fixo no código — e toda vez que a diretoria
pedia algo como "a projeção só para gerente e coordenador" alguém tinha de
mexer em view. Aqui o SUPERADMIN decide na tela de configuração, por visão.

Duas regras de ouro:

* O padrão de toda visão é ``TODOS``, que é exatamente o comportamento de
  antes — nada muda até alguém configurar.
* O SUPERADMIN nunca é bloqueado, senão ele se trancaria para fora da própria
  tela de configuração.

A liberação NÃO substitui ``pode_ver_comissionamento``: ela é um filtro a mais,
depois de a pessoa já ter direito ao módulo.
"""

# ─── Públicos ────────────────────────────────────────────────────────────────

PUBLICO_TODOS = 'TODOS'
PUBLICO_GERENTES = 'GERENTES'
PUBLICO_COORDENADORES = 'COORDENADORES'
PUBLICO_GERENTES_COORDENADORES = 'GERENTES_COORDENADORES'

PUBLICOS = (
    (PUBLICO_TODOS, 'Todos'),
    (PUBLICO_GERENTES, 'Somente Gerentes'),
    (PUBLICO_COORDENADORES, 'Somente Coordenadores'),
    (PUBLICO_GERENTES_COORDENADORES, 'Gerentes e Coordenadores'),
)
PUBLICOS_VALIDOS = {codigo for codigo, _ in PUBLICOS}
PUBLICOS_ROTULOS = dict(PUBLICOS)


# ─── Catálogo das visões ─────────────────────────────────────────────────────
# `codigo` é o que fica guardado no banco; mudar um deles apaga a configuração
# daquela visão (ela volta para o padrão TODOS).

VISAO_CONSULTOR = 'cn'
VISAO_RECEPCIONISTA = 'recepcionista'
VISAO_GERENTE = 'gerente'
VISAO_COORDENADOR = 'coordenador'
VISAO_APARTE = 'aparte'
VISAO_PROJECAO = 'projecao'
VISAO_SUPERADMIN = 'superadmin'

VISOES = (
    {'codigo': VISAO_CONSULTOR, 'nome': 'Consultor (CN)',
     'descricao': 'O comissionamento individual de quem vende.',
     'onde': '/users/commission/', 'fixa': False},
    {'codigo': VISAO_RECEPCIONISTA, 'nome': 'Recepcionista',
     'descricao': 'Comissionamento individual da recepção.',
     'onde': '/users/commission/', 'fixa': False},
    {'codigo': VISAO_GERENTE, 'nome': 'Gerente',
     'descricao': 'Visão gerencial: o resultado da equipe da loja.',
     'onde': '/users/commission/', 'fixa': False},
    {'codigo': VISAO_COORDENADOR, 'nome': 'Coordenação',
     'descricao': 'Visão de coordenação: todas as lojas da carteira.',
     'onde': '/users/commission/', 'fixa': False},
    {'codigo': VISAO_APARTE, 'nome': 'A parte',
     'descricao': 'Comissionamento à parte, de quem é pago fora da régua.',
     'onde': '/users/commission/', 'fixa': False},
    {'codigo': VISAO_PROJECAO, 'nome': 'Projeção',
     'descricao': 'Projeção do ganho do mês e médias da rede.',
     'onde': '/users/commission/projecao/', 'fixa': False},
    {'codigo': VISAO_SUPERADMIN, 'nome': 'SuperAdmin',
     'descricao': 'Lista todo mundo e abre o comissionamento de qualquer um.',
     'onde': '/users/commission/', 'fixa': True},
)

VISOES_POR_CODIGO = {v['codigo']: v for v in VISOES}


# ─── Leitura da configuração ─────────────────────────────────────────────────

def _config():
    from users.models import SystemConfig
    return SystemConfig.get_config()


def _mapa(config=None):
    """{codigo: publico} guardado. Nunca levanta: sem config, vale o padrão."""
    try:
        config = config if config is not None else _config()
        return dict(getattr(config, 'commission_view_access', None) or {})
    except Exception:                                           # noqa: BLE001
        return {}


def publico_da_visao(codigo, config=None):
    """Público liberado para a visão. Valor ausente ou estranho vira TODOS."""
    escolhido = _mapa(config).get(codigo)
    return escolhido if escolhido in PUBLICOS_VALIDOS else PUBLICO_TODOS


def pode_ver_visao(user, codigo, config=None):
    """A pessoa está no público liberado para esta visão?"""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False

    from .commission_views import is_user_coordenador, is_user_gerente, is_user_superadmin

    # Quem configura não pode se trancar para fora.
    if getattr(user, 'is_superuser', False) or is_user_superadmin(user):
        return True

    publico = publico_da_visao(codigo, config)
    if publico == PUBLICO_TODOS:
        return True

    e_gerente = is_user_gerente(user)
    e_coordenador = is_user_coordenador(user)
    if publico == PUBLICO_GERENTES:
        return e_gerente
    if publico == PUBLICO_COORDENADORES:
        return e_coordenador
    return e_gerente or e_coordenador           # GERENTES_COORDENADORES


def visoes_para_tela(config=None):
    """O catálogo com o público atual de cada visão, pronto para a tabela."""
    mapa = _mapa(config)
    return [
        dict(visao,
             publico=(mapa.get(visao['codigo']) if mapa.get(visao['codigo']) in PUBLICOS_VALIDOS
                      else PUBLICO_TODOS),
             publico_rotulo=PUBLICOS_ROTULOS[
                 mapa.get(visao['codigo']) if mapa.get(visao['codigo']) in PUBLICOS_VALIDOS
                 else PUBLICO_TODOS])
        for visao in VISOES
    ]


def salvar_publicos(dados, config=None):
    """Guarda o público de cada visão vindo do formulário.

    Ignora a visão fixa (SuperAdmin) e qualquer valor que não seja um público
    conhecido — formulário adulterado não vira configuração inválida. Devolve a
    config já com o novo mapa, sem salvar (quem chama decide o `save`).
    """
    config = config if config is not None else _config()
    mapa = _mapa(config)
    for visao in VISOES:
        if visao['fixa']:
            continue
        escolhido = (dados.get(f"publico_{visao['codigo']}") or '').strip().upper()
        if escolhido in PUBLICOS_VALIDOS:
            mapa[visao['codigo']] = escolhido
    config.commission_view_access = mapa
    return config
