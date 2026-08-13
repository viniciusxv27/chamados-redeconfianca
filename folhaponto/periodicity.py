"""Classificação Semanal x Mensal das folhas de ponto.

Regra de negócio: o gestor reimporta a folha do período em aberto toda semana,
só para atualizar os valores. Enquanto ela é a folha mais recente do
colaborador, é uma prévia — não deve ser assinada. Quando chega a folha do
período seguinte, a anterior passa a ser o fechamento mensal e aí sim é
assinada.

Exceção da folha isolada: se o colaborador tem uma ÚNICA folha, não existe um
período anterior fechado para assinar. Tratá-la como semanal deixaria o
colaborador sem nada assinável (a prévia nunca vira fechamento porque a folha
seguinte pode não ser importada). Por isso, quando é a única folha do
colaborador, ela já é classificada como mensal (fechamento) e pode ser assinada.

Override manual: o gestor pode definir ``folha.periodicity_override`` como
'mensal' (assinável) ou 'semanal' (prévia), na importação em lote ou pelo botão
da tela de detalhe, ignorando a regra automática. Vazio = regra automática.

A classificação é feita em tempo de execução, a partir de (year, month); o único
estado persistido é o override ``periodicity_override``.
"""

from .models import FolhaPonto


SEMANAL = 'semanal'
MENSAL = 'mensal'

PERIODICITY_LABELS = {
    SEMANAL: 'Semanal',
    MENSAL: 'Mensal',
}


def period_key(year, month):
    """Competência como inteiro comparável (ano*12 + mês)."""
    return (year or 0) * 12 + (month or 0)


def stats_by_user(user_ids=None):
    """``{user_id: (competência mais recente, total de folhas)}`` por colaborador.

    Calculado sempre sobre a tabela inteira, nunca sobre a listagem já
    filtrada: se o admin filtra por Maio, a folha de Maio continua sendo
    mensal — não pode virar "a mais recente" só porque é a única na tela.
    """
    qs = FolhaPonto.objects.all()
    if user_ids is not None:
        user_ids = list(user_ids)
        if not user_ids:
            return {}
        qs = qs.filter(user_id__in=user_ids)

    # Max de (year, month) e contagem de folhas por usuário, em Python sobre os
    # pares distintos: o volume de competências é pequeno e a expressão
    # year*12+month não é indexada.
    stats = {}
    for user_id, year, month in qs.values_list('user_id', 'year', 'month'):
        key = period_key(year, month)
        latest, count = stats.get(user_id, (-1, 0))
        stats[user_id] = (key if key > latest else latest, count + 1)
    return stats


def latest_key_by_user(user_ids=None):
    """``{user_id: competência mais recente}`` de cada colaborador (compat)."""
    return {user_id: latest for user_id, (latest, _count) in stats_by_user(user_ids).items()}


def _classify_semanal(year, month, user_id, stats):
    """Decide se a folha é semanal a partir das estatísticas do colaborador.

    Semanal = é a folha mais recente do colaborador (período em aberto) E o
    colaborador tem mais de uma folha. Folha única nunca é semanal: sem período
    anterior fechado, ela já é o fechamento mensal (assinável).
    """
    latest, count = stats.get(user_id, (-1, 0))
    if count <= 1:
        return False
    return period_key(year, month) >= latest


def annotate_periodicity(folhas, stats=None):
    """Marca cada folha com ``periodicity``, ``is_semanal`` e ``can_sign``.

    Devolve a lista de folhas (materializa o queryset). Atributos anexados:

    - ``is_semanal``: é a folha mais recente do colaborador (período em aberto)
      e ele tem mais de uma folha
    - ``periodicity_label``: "Semanal" ou "Mensal", para exibição
    - ``can_sign``: assinável agora (mensal e ainda não assinada)

    Folhas semanais que já foram assinadas mantêm a assinatura visível; elas
    só deixam de aceitar novas assinaturas.
    """
    folhas = list(folhas)
    if stats is None:
        stats = stats_by_user({f.user_id for f in folhas})

    for folha in folhas:
        override = getattr(folha, 'periodicity_override', '') or ''
        if override:
            is_semanal_val = (override == SEMANAL)   # escolha manual do gestor
        else:
            is_semanal_val = _classify_semanal(folha.year, folha.month, folha.user_id, stats)
        folha.is_semanal = is_semanal_val
        folha.periodicity = SEMANAL if is_semanal_val else MENSAL
        folha.periodicity_label = PERIODICITY_LABELS[folha.periodicity]
        folha.can_sign = (not is_semanal_val) and (not folha.is_signed)
    return folhas


def is_semanal(folha):
    """Classificação de uma folha isolada (1 consulta)."""
    override = getattr(folha, 'periodicity_override', '') or ''
    if override:
        return override == SEMANAL   # escolha manual do gestor
    stats = stats_by_user([folha.user_id])
    return _classify_semanal(folha.year, folha.month, folha.user_id, stats)
