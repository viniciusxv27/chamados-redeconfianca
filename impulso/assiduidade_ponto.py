"""Assiduidade a partir do ponto eletrônico (Tangerino), não da folha em PDF.

A regra, como o gestor a descreve: **até 3 marcações esquecidas no mês, a
pessoa mantém os 10 pontos de assiduidade; passando disso, perde.** E manter
os pontos também exige ter cumprido o horário — quem bateu tudo mas ficou
muito abaixo da jornada contratada não está assíduo, está devendo hora.

O que conta como marcação esquecida, sobre os dias em que a pessoa **devia**
trabalhar (``previsto_segundos > 0``, então folga e feriado ficam de fora):

* dia sem nenhuma marcação;
* dia com entrada sem a saída correspondente (``em_aberto``).

O dia corrente nunca conta: quem entrou e ainda não saiu está trabalhando,
não esqueceu de bater.
"""
from decimal import Decimal

from django.utils import timezone

PONTOS_ASSIDUIDADE = Decimal('10')

# Quantas marcações esquecidas o mês tolera antes de zerar a assiduidade.
LIMITE_FALHAS = 3

# Quanto a jornada pode ficar abaixo do previsto no mês sem deixar de ser
# "horário certo". Uma hora cobre atraso pontual sem premiar quem deve o mês.
TOLERANCIA_MINUTOS = 60


def _marcacoes_do_mes(user, ano, mes):
    from tangerino.models import MarcacaoPonto

    if not getattr(user, 'tangerino_employee_id', None):
        return None
    return list(MarcacaoPonto.objects
                .filter(employee_id=user.tangerino_employee_id,
                        data__year=ano, data__month=mes)
                .order_by('data'))


def avaliar(marcacoes, hoje=None):
    """Conta as falhas de marcação e compara o trabalhado com o previsto."""
    hoje = hoje or timezone.localdate()

    falhas = []
    previsto = trabalhado = 0
    dias_uteis = 0

    for m in marcacoes:
        if m.data >= hoje:
            continue                      # dia em andamento não se julga
        previsto += m.previsto_segundos or 0
        trabalhado += m.total_segundos or 0
        if not (m.previsto_segundos or 0):
            continue                      # folga, feriado, férias

        dias_uteis += 1
        tem_marcacao = any([m.entrada1, m.saida1, m.entrada2, m.saida2,
                            m.entrada3, m.saida3])
        if not tem_marcacao:
            falhas.append({'data': m.data, 'motivo': 'Nenhuma marcação no dia'})
        elif m.em_aberto:
            falhas.append({'data': m.data, 'motivo': 'Entrada sem a saída correspondente'})

    saldo_minutos = (trabalhado - previsto) // 60
    return {
        'dias_uteis': dias_uteis,
        'falhas': falhas,
        'total_falhas': len(falhas),
        'limite': LIMITE_FALHAS,
        'previsto_segundos': previsto,
        'trabalhado_segundos': trabalhado,
        'saldo_minutos': saldo_minutos,
        'horario_ok': saldo_minutos >= -TOLERANCIA_MINUTOS,
    }


def nota_assiduidade_ponto(user, ano, mes, hoje=None):
    """(pontos, pontos_aplicáveis, detalhes) da assiduidade pelo ponto.

    Devolve ``None`` quando não há ponto sincronizado para essa pessoa nesse
    mês — aí quem responde é a folha de ponto importada, como antes.
    """
    marcacoes = _marcacoes_do_mes(user, ano, mes)
    if not marcacoes:
        return None

    resultado = avaliar(marcacoes, hoje=hoje)
    resultado['fonte'] = 'ponto'

    if not resultado['dias_uteis']:
        resultado['sem_dias_avaliaveis'] = True
        return Decimal('0'), Decimal('0'), resultado

    dentro_do_limite = resultado['total_falhas'] <= LIMITE_FALHAS
    resultado['dentro_do_limite'] = dentro_do_limite

    if dentro_do_limite and resultado['horario_ok']:
        resultado['motivo'] = (
            f"{resultado['total_falhas']} marcação(ões) esquecida(s), dentro do "
            f"limite de {LIMITE_FALHAS}, e jornada cumprida.")
        return PONTOS_ASSIDUIDADE, PONTOS_ASSIDUIDADE, resultado

    if not dentro_do_limite:
        resultado['motivo'] = (
            f"{resultado['total_falhas']} marcações esquecidas no mês — acima do "
            f"limite de {LIMITE_FALHAS}.")
    else:
        horas = abs(resultado['saldo_minutos']) // 60
        minutos = abs(resultado['saldo_minutos']) % 60
        resultado['motivo'] = (
            f"Marcações em dia, mas a jornada ficou {horas}h{minutos:02d} abaixo "
            f"do previsto no mês.")
    return Decimal('0'), PONTOS_ASSIDUIDADE, resultado
