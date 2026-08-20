"""Quantas horas cada pessoa **deveria** ter trabalhado.

O Tangerino entrega o que foi marcado, mas o saldo de banco de horas depende
também da escala contratada — e essa escala existe na API, em
``/work-schedule/{id}``, ligada ao funcionário por ``currentWorkSchedule``.

Aqui a grade vira um número por dia:

    seg 15:00-18:00 + 19:00-24:00  ->  8h previstas
    sex 16:00-20:00                ->  4h previstas
    dom (sem linha na grade)       ->  folga, 0h

E do previsto saem os dias em que a pessoa não devia estar lá: feriado,
férias, atestado, folga abonada. Sem esse desconto, um mês com dois feriados
apareceria como se a pessoa devesse 16 horas que ninguém cobra dela.

O que este módulo calcula **não substitui** o saldo oficial do Tangerino
(``SaldoHoras.saldo_minutos``): aquele é o número que o colaborador vê no
app e o que vale para o RH. Estes servem para mostrar a conta por trás —
previsto e realizado — que a API não entrega pronta.
"""
import logging
from datetime import timedelta

from .client import de_millis, jornada as buscar_jornada, listar_ajustes, listar_funcionarios

logger = logging.getLogger(__name__)

# Motivos de ajuste que abonam o dia: a pessoa não precisava estar lá.
# Vem de /adjustment-reason/find-all, campo `allowance`.
MOTIVOS_QUE_ABONAM = {
    1: 'FÉRIAS',
    2: 'AFASTAMENTO',
    3: 'FOLGA',
    4: 'ABONO',
    5: 'ATESTADO MÉDICO',
    7: 'FALTA JUSTIFICADA ABONADA',
    9: 'AFASTAMENTO INSS',
    10: 'LICENÇA MATERNIDADE',
    11: 'LICENÇA PATERNIDADE',
    12: 'FERIADO',
    19: 'ACIDENTE DE TRABALHO',
    20: 'AVISO PRÉVIO',
}

# `day` na grade segue o Java Calendar: 1 = domingo … 7 = sábado.
# O weekday() do Python é 0 = segunda … 6 = domingo.
def _dia_da_semana_tangerino(data):
    return (data.weekday() + 1) % 7 + 1


def segundos_por_dia(payload):
    """Converte a grade da API em {dia_tangerino: segundos previstos}.

    Soma os turnos e ignora o intervalo — o intervalo não é trabalho. Dia sem
    linha na grade simplesmente não aparece no resultado: é folga.
    """
    grade = {}
    for linha in (payload or {}).get('workScheduleTimetableList') or []:
        dia = linha.get('day')
        if not dia:
            continue
        total = 0
        for inicio, fim in (('startShift1', 'endShift1'),
                            ('startShift2', 'endShift2'),
                            ('startShift3', 'endShift3')):
            a, b = linha.get(inicio), linha.get(fim)
            if a is not None and b is not None and b > a:
                total += (b - a) // 1000
        if total:
            grade[dia] = total
    return grade


def carregar_jornadas():
    """Grade de todas as escalas em uso, por id de escala.

    São ~30 escalas para 170 pessoas, então vale buscar uma vez e reaproveitar
    em vez de perguntar por funcionário.
    """
    ids = set()
    por_funcionario = {}
    for f in listar_funcionarios():
        escala = (f.get('currentWorkSchedule') or {}).get('id')
        if escala:
            ids.add(escala)
            por_funcionario[f.get('id')] = escala

    grades = {}
    for escala in ids:
        try:
            dados = buscar_jornada(escala)
            grades[escala] = {
                'id': escala,
                'nome': (dados.get('nome') or dados.get('name') or '')[:200],
                'grade': segundos_por_dia(dados),
            }
        except Exception as exc:  # a falta de uma escala não derruba as outras
            logger.warning('Escala %s não pôde ser lida: %s', escala, exc)
    return {'por_funcionario': por_funcionario, 'grades': grades}


def previsto_no_dia(grade, data):
    """Segundos previstos para essa pessoa nesse dia, sem descontar abono."""
    return (grade or {}).get(_dia_da_semana_tangerino(data), 0)


def carregar_abonos(inicio, fim, motivos=None):
    """Dias abonados por pessoa no período: {(employee_id, data): segundos}.

    Um lançamento de dia inteiro abona a jornada toda daquele dia; um
    lançamento com hora abona só a duração dele. O valor é limitado ao
    previsto na hora de aplicar, para um abono de 9h não zerar mais do que o
    dia tinha.
    """
    abonos = {}
    for motivo_id in (motivos or MOTIVOS_QUE_ABONAM):
        try:
            itens = listar_ajustes(motivo_id)
        except Exception as exc:
            logger.warning('Ajustes do motivo %s indisponíveis: %s', motivo_id, exc)
            continue

        for item in itens:
            eid = (item.get('employeeDTO') or {}).get('id')
            comeco, termino = de_millis(item.get('startDate')), de_millis(item.get('endDate'))
            if not eid or not comeco:
                continue
            termino = termino or comeco
            if termino.date() < inicio or comeco.date() > fim:
                continue

            if item.get('fullDay') or termino.date() > comeco.date():
                # Período de dias inteiros (férias, afastamento): marca cada dia.
                dia = max(comeco.date(), inicio)
                ultimo = min(termino.date(), fim)
                while dia <= ultimo:
                    abonos[(eid, dia)] = None      # None = dia inteiro
                    dia += timedelta(days=1)
            else:
                segundos = max(0, int((termino - comeco).total_seconds()))
                chave = (eid, comeco.date())
                if chave in abonos and abonos[chave] is None:
                    continue                        # já é dia inteiro
                abonos[chave] = (abonos.get(chave) or 0) + segundos
    return abonos


def previsto_liquido(grade, data, abonado):
    """Previsto do dia menos o que foi abonado, nunca negativo."""
    bruto = previsto_no_dia(grade, data)
    if abonado is None:          # dia inteiro abonado
        return 0
    return max(0, bruto - (abonado or 0))


def previsto_no_periodo(grade, inicio, fim, employee_id=None, abonos=None):
    """Soma o previsto de cada dia do período, já sem os dias abonados."""
    total = 0
    dia = inicio
    while dia <= fim:
        abonado = (abonos or {}).get((employee_id, dia), 0) if abonos is not None else 0
        total += previsto_liquido(grade, dia, abonado)
        dia += timedelta(days=1)
    return total


def formata_hhmm(segundos):
    """Segundos em HH:MM, aceitando negativo (saldo devedor)."""
    segundos = int(segundos or 0)
    sinal = '-' if segundos < 0 else ''
    segundos = abs(segundos)
    return f"{sinal}{segundos // 3600:02d}:{(segundos % 3600) // 60:02d}"
