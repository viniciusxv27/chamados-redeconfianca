"""Assiduidade do Impulso lida do ponto eletrônico (Tangerino).

A regra, como o RH a descreve:

* o dia útil precisa ter as **4 batidas** (entrada, saída para o intervalo,
  volta e saída);
* esqueceu uma? dá para **ajustar em até 24 horas**;
* são **3 ajustes por mês** — passou disso, perde os 10 pontos;
* **falta injustificada** (sem motivo lançado) zera os 10 pontos.

Três coisas moldam a leitura:

* **Só dia útil entra na conta.** Folga, feriado, férias e atestado têm
  ``previsto_segundos = 0`` na marcação, então saem sozinhos — ninguém é
  cobrado por não bater ponto em dia que não trabalha.
* **O dia corrente nunca conta**, e o de ontem também não enquanto estiver
  dentro das 24 horas: quem esqueceu ontem à noite ainda tem prazo para
  ajustar hoje. Punir antes do prazo seria cobrar uma regra que a própria
  regra não cobra ainda.
* **Ajuste é marcação editada no Tangerino** (``MarcacaoPonto.editado``), que
  é como a correção de ponto esquecido aparece de lá.
* **Dia de exceção** (``ExcecaoAssiduidade``, criado pelo SUPERADMIN) tira o
  ajuste daquele dia da conta — e só isso. O dia segue sendo dia útil, segue
  precisando das 4 batidas, e falta injustificada nele continua zerando.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

PONTOS_ASSIDUIDADE = Decimal('10')

BATIDAS_ESPERADAS = 4          # entrada, saída para o intervalo, volta, saída
LIMITE_AJUSTES_MES = 3         # ajustes de ponto esquecido tolerados no mês
PRAZO_AJUSTE_HORAS = 24        # tempo para corrigir uma batida esquecida

# Motivo de ajuste no Tangerino que caracteriza falta sem justificativa.
MOTIVO_FALTA_INJUSTIFICADA = 8


def _batidas(marcacao):
    return len([x for x in (marcacao.entrada1, marcacao.saida1,
                            marcacao.entrada2, marcacao.saida2,
                            marcacao.entrada3, marcacao.saida3) if x])


def _marcacoes_do_mes(user, ano, mes):
    from tangerino.models import MarcacaoPonto

    if not getattr(user, 'tangerino_employee_id', None):
        return None
    return list(MarcacaoPonto.objects
                .filter(employee_id=user.tangerino_employee_id,
                        data__year=ano, data__month=mes)
                .order_by('data'))


def faltas_injustificadas(employee_id, ano, mes):
    """Dias com FALTA NÃO JUSTIFICADA lançada no Tangerino.

    Vem da API porque falta é lançamento de gestor, não marcação de ponto —
    não existe na tabela de marcações. Se a API estiver fora, devolve vazio: a
    nota sai sem essa penalidade em vez de sair errada para o outro lado.
    """
    from tangerino.client import de_millis, listar_ajustes

    try:
        lancamentos = listar_ajustes(MOTIVO_FALTA_INJUSTIFICADA)
    except Exception:
        return None

    dias = set()
    for item in lancamentos:
        if (item.get('employeeDTO') or {}).get('id') != employee_id:
            continue
        inicio = de_millis(item.get('startDate'))
        fim = de_millis(item.get('endDate')) or inicio
        if not inicio:
            continue
        dia = inicio.date()
        ultimo = fim.date()
        while dia <= ultimo:
            if dia.year == ano and dia.month == mes:
                dias.add(dia)
            dia += timedelta(days=1)
    return sorted(dias)


def avaliar(marcacoes, faltas=None, agora=None, excecoes=None):
    """Percorre o mês e separa o que conta contra a assiduidade.

    ``excecoes`` são os dias liberados pelo SUPERADMIN (ver
    ``ExcecaoAssiduidade``). O alcance é estreito de propósito: o dia continua
    valendo como dia útil e ainda precisa das 4 batidas — a exceção só faz o
    ajuste daquele dia não entrar na conta dos 3 do mês.
    """
    agora = agora or timezone.localtime()
    hoje = agora.date()
    limite_prazo = agora - timedelta(hours=PRAZO_AJUSTE_HORAS)

    faltas = set(faltas or [])
    excecoes = set(excecoes or [])
    dias_uteis = 0
    incompletos, no_prazo, ajustes, dias_completos = [], [], [], []
    ajustes_perdoados = []

    for m in marcacoes:
        if m.data >= hoje:
            continue                                  # dia em andamento
        if not (m.previsto_segundos or 0):
            continue                                  # folga, feriado, férias

        dias_uteis += 1
        batidas = _batidas(m)

        if m.editado:
            if m.data in excecoes:
                ajustes_perdoados.append({'data': m.data, 'batidas': batidas})
            else:
                ajustes.append({'data': m.data, 'batidas': batidas})

        if batidas >= BATIDAS_ESPERADAS:
            dias_completos.append(m.data)
            continue

        # Fim do dia + prazo: só vira falha quando o prazo de ajuste passa.
        fim_do_prazo = timezone.make_aware(
            timezone.datetime.combine(m.data, timezone.datetime.min.time())) \
            + timedelta(days=1, hours=PRAZO_AJUSTE_HORAS)
        registro = {'data': m.data, 'batidas': batidas,
                    'faltam': BATIDAS_ESPERADAS - batidas}
        if fim_do_prazo > agora:
            no_prazo.append(registro)                 # ainda dá tempo de ajustar
        else:
            incompletos.append(registro)

    return {
        'dias_uteis': dias_uteis,
        'dias_completos': len(dias_completos),
        'incompletos': incompletos,
        'no_prazo': no_prazo,
        'ajustes': ajustes,
        'total_ajustes': len(ajustes),
        'ajustes_perdoados': ajustes_perdoados,
        'total_perdoados': len(ajustes_perdoados),
        'limite_ajustes': LIMITE_AJUSTES_MES,
        'faltas': sorted(faltas),
        'total_faltas': len(faltas),
        'batidas_esperadas': BATIDAS_ESPERADAS,
        'prazo_horas': PRAZO_AJUSTE_HORAS,
    }


def nota_assiduidade_ponto(user, ano, mes, agora=None):
    """(pontos, pontos_aplicáveis, detalhes) da assiduidade no mês.

    Devolve ``None`` quando não há ponto sincronizado — aí quem responde é a
    folha de ponto importada, como antes.
    """
    from tangerino.models import nao_bate_ponto

    from .models import ExcecaoAssiduidade

    # Dispensado de bater ponto não é avaliado por assiduidade: a nota mediria
    # a ausência de algo que ninguém pediu para ele fazer.
    if nao_bate_ponto(user):
        return None

    marcacoes = _marcacoes_do_mes(user, ano, mes)
    if not marcacoes:
        return None

    faltas = faltas_injustificadas(user.tangerino_employee_id, ano, mes)
    excecoes = ExcecaoAssiduidade.dias_do_mes(ano, mes)
    resultado = avaliar(marcacoes, faltas=faltas, agora=agora, excecoes=excecoes)
    resultado['fonte'] = 'ponto'
    resultado['faltas_indisponiveis'] = faltas is None

    if not resultado['dias_uteis']:
        resultado['sem_dias_avaliaveis'] = True
        return Decimal('0'), Decimal('0'), resultado

    # Cada motivo é reportado, não só o primeiro: quem perdeu os pontos merece
    # ver tudo o que pesou, e não descobrir o segundo motivo no mês seguinte.
    motivos = []
    if resultado['total_faltas']:
        dias = ', '.join(d.strftime('%d/%m') for d in resultado['faltas'])
        motivos.append(f"falta injustificada em {dias}")
    if resultado['total_ajustes'] > LIMITE_AJUSTES_MES:
        motivos.append(f"{resultado['total_ajustes']} ajustes de ponto — o limite "
                       f"do mês é {LIMITE_AJUSTES_MES}")
    if resultado['incompletos']:
        dias = ', '.join(d['data'].strftime('%d/%m') for d in resultado['incompletos'][:4])
        resto = len(resultado['incompletos']) - 4
        motivos.append(f"dia sem as {BATIDAS_ESPERADAS} batidas e fora do prazo de "
                       f"ajuste: {dias}" + (f" e mais {resto}" if resto > 0 else ''))

    resultado['motivos'] = motivos
    resultado['perdeu'] = bool(motivos)

    if motivos:
        resultado['motivo'] = '; '.join(motivos).capitalize() + '.'
        return Decimal('0'), PONTOS_ASSIDUIDADE, resultado

    restantes = LIMITE_AJUSTES_MES - resultado['total_ajustes']
    perdoados = resultado['total_perdoados']
    resultado['motivo'] = (
        f"{resultado['dias_completos']} de {resultado['dias_uteis']} dias úteis com as "
        f"{BATIDAS_ESPERADAS} batidas"
        + (f", {resultado['total_ajustes']} ajuste(s) usado(s) de {LIMITE_AJUSTES_MES}"
           if resultado['total_ajustes'] else ', sem ajustes')
        + (f" ({perdoados} em dia de exceção, fora da conta)." if perdoados else '.'))
    resultado['ajustes_restantes'] = restantes
    return PONTOS_ASSIDUIDADE, PONTOS_ASSIDUIDADE, resultado
