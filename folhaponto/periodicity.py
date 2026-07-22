"""Classificação Semanal x Mensal das folhas de ponto.

Regra de negócio: o gestor reimporta a folha do período em aberto toda semana,
só para atualizar os valores. Enquanto ela é a folha mais recente do
colaborador, é uma prévia — não deve ser assinada. Quando chega a folha do
período seguinte, a anterior passa a ser o fechamento mensal e aí sim é
assinada.

A classificação é feita em tempo de execução, a partir de (year, month): não há
campo novo no banco.
"""

from django.db.models import Max

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


def latest_key_by_user(user_ids=None):
    """``{user_id: competência mais recente}`` de cada colaborador.

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

    # Max de (year, month) por usuário. Feito em Python sobre os pares
    # distintos porque a expressão year*12+month não é indexada e o volume
    # de competências distintas é pequeno.
    latest = {}
    for user_id, year, month in qs.values_list('user_id', 'year', 'month'):
        key = period_key(year, month)
        if key > latest.get(user_id, -1):
            latest[user_id] = key
    return latest


def annotate_periodicity(folhas, latest_map=None):
    """Marca cada folha com ``periodicity``, ``is_semanal`` e ``can_sign``.

    Devolve a lista de folhas (materializa o queryset). Atributos anexados:

    - ``is_semanal``: é a folha mais recente do colaborador (período em aberto)
    - ``periodicity_label``: "Semanal" ou "Mensal", para exibição
    - ``can_sign``: assinável agora (mensal e ainda não assinada)

    Folhas semanais que já foram assinadas mantêm a assinatura visível; elas
    só deixam de aceitar novas assinaturas.
    """
    folhas = list(folhas)
    if latest_map is None:
        latest_map = latest_key_by_user({f.user_id for f in folhas})

    for folha in folhas:
        is_semanal = period_key(folha.year, folha.month) >= latest_map.get(folha.user_id, -1)
        folha.is_semanal = is_semanal
        folha.periodicity = SEMANAL if is_semanal else MENSAL
        folha.periodicity_label = PERIODICITY_LABELS[folha.periodicity]
        folha.can_sign = (not is_semanal) and (not folha.is_signed)
    return folhas


def is_semanal(folha):
    """Classificação de uma folha isolada (1 consulta)."""
    latest = latest_key_by_user([folha.user_id]).get(folha.user_id, -1)
    return period_key(folha.year, folha.month) >= latest
