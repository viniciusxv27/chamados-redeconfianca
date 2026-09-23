"""Quem enxerga cada versão do comissionamento.

Cada versão é um mês/ano numa fase ("Antes da Contestação" ou "Pós
Contestação"). A prévia costuma valer só para quem responde por equipe — ela
ainda vai mudar —, enquanto a versão fechada vai para todo mundo. Antes isso
não existia: liberar uma versão soltava para todos, sem meio-termo.

Duas regras de ouro:

* O padrão é ``TODOS``, que é exatamente o comportamento de antes — nada muda
  até o SUPERADMIN configurar.
* O SUPERADMIN enxerga todas as versões, senão ele não conseguiria conferir o
  que está liberando.

Isto não substitui o status da versão: rascunho continua invisível para quem
não é SUPERADMIN. A liberação é um filtro a mais, aplicado depois.
"""
from users.models import CommissionSpreadsheetVersion as _Versao

PUBLICO_TODOS = _Versao.LIBERADO_TODOS
PUBLICO_GERENTES = _Versao.LIBERADO_GERENTES
PUBLICO_COORDENADORES = _Versao.LIBERADO_COORDENADORES
PUBLICO_GERENTES_COORDENADORES = _Versao.LIBERADO_GERENTES_COORDENADORES

PUBLICOS = tuple(_Versao.LIBERADO_PARA_CHOICES)
PUBLICOS_VALIDOS = {codigo for codigo, _ in PUBLICOS}
PUBLICOS_ROTULOS = dict(PUBLICOS)


def _e_superadmin(user):
    from .commission_views import is_user_superadmin
    return bool(getattr(user, 'is_superuser', False) or is_user_superadmin(user))


def publico_permitido(user, publico):
    """A pessoa está no público liberado?

    Público desconhecido (linha antiga, banco mexido à mão) vale como TODOS —
    na dúvida não se esconde o comissionamento de quem tem direito a ele.
    """
    if publico not in PUBLICOS_VALIDOS or publico == PUBLICO_TODOS:
        return True

    from .commission_views import is_user_coordenador, is_user_gerente
    e_gerente = is_user_gerente(user)
    e_coordenador = is_user_coordenador(user)
    if publico == PUBLICO_GERENTES:
        return e_gerente
    if publico == PUBLICO_COORDENADORES:
        return e_coordenador
    return e_gerente or e_coordenador           # GERENTES_COORDENADORES


def pode_ver_versao(user, versao):
    """A pessoa enxerga esta versão (mês/ano + fase)?"""
    if not (user and getattr(user, 'is_authenticated', False)):
        return False
    if _e_superadmin(user):
        return True
    return publico_permitido(user, getattr(versao, 'liberado_para', PUBLICO_TODOS))


def versoes_liberadas(user):
    """As versões liberadas que ``user`` pode enxergar, da mais nova para a mais antiga.

    Só versões com status "Liberada": rascunho continua sendo assunto do
    SUPERADMIN na tela de configuração.
    """
    base = (_Versao.objects.filter(status=_Versao.STATUS_RELEASED)
            .order_by('-year', '-month', 'contestacao_phase'))
    if not (user and getattr(user, 'is_authenticated', False)):
        return []
    if _e_superadmin(user):
        return list(base)
    return [v for v in base if publico_permitido(user, v.liberado_para)]


def sem_versao_liberada(user):
    """Há versão liberada no sistema, mas nenhuma para esta pessoa?

    É o caso em que a tela não pode mostrar número nenhum: sem referência
    liberada, o ``get_excel_urls`` cairia no fallback e serviria justamente a
    planilha da versão restrita.
    """
    if not (user and getattr(user, 'is_authenticated', False)) or _e_superadmin(user):
        return False
    if not _Versao.objects.filter(status=_Versao.STATUS_RELEASED).exists():
        return False            # nada liberado para ninguém: comportamento antigo
    return not versoes_liberadas(user)


def versoes_para_tela():
    """Todas as versões para a tabela da configuração (inclusive rascunhos)."""
    return list(_Versao.objects.select_related('updated_by')
                .order_by('-year', '-month', 'contestacao_phase'))


def salvar_liberacoes(dados, usuario=None):
    """Grava o público de cada versão vindo do formulário.

    Só toca nas versões que vieram no POST e com valor conhecido — formulário
    adulterado não vira configuração inválida. Devolve quantas mudaram.
    """
    alteradas = 0
    for versao in _Versao.objects.all():
        escolhido = (dados.get(f'liberado_{versao.id}') or '').strip().upper()
        if escolhido not in PUBLICOS_VALIDOS or escolhido == versao.liberado_para:
            continue
        versao.liberado_para = escolhido
        campos = ['liberado_para']
        if usuario is not None:
            versao.updated_by = usuario
            campos.append('updated_by')
        versao.save(update_fields=campos)
        alteradas += 1
    return alteradas
