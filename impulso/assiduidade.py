"""Leitura da folha de ponto (app folhaponto) para a nota de assiduidade.

A folha é um PDF raspado: `FolhaPonto.daily_records` guarda uma linha por dia
com os batimentos e os totais num texto cru (`registro`). Formatos reais:

    "08:40 12:10 | 13:13 18:07 | 08:24 08:00 00:24"
     └── batimentos ─────────┘   └ trabalhadas previstas saldo ┘
    "08:49 13:06 | 04:17 04:00 00:17"      (meio período: 2 batimentos)
    "FALTA NAO JUSTIFICADA 08:00 -8:00"    (falta: previstas e saldo negativo)
    "-"                                     (domingo/sem expediente)

Ou seja: o ÚLTIMO grupo (separado por "|") são sempre os totais; os grupos
anteriores são os batimentos.

Regra de assiduidade (10 pontos): a cada semana com pelo menos um dia
problemático, desconta 2,5 pontos. Um dia é problemático quando há falta,
saldo negativo acima da tolerância (10 min) ou batimentos incompletos.
"""
import re
from datetime import date
from decimal import Decimal

# Ex.: "08:40", "-8:00", "00:24"
TIME_RE = re.compile(r'-?\d{1,3}:\d{2}')

TOLERANCIA_NEGATIVA_MIN = 10      # minutos negativos tolerados no dia
BATIMENTOS_DIA_COMPLETO = 4       # dia inteiro
BATIMENTOS_MEIO_PERIODO = 2       # ex.: sábado
MEIO_PERIODO_LIMITE_MIN = 6 * 60  # previstas abaixo disso => meio período
PENALIDADE_SEMANA = Decimal('2.5')
PONTOS_ASSIDUIDADE = Decimal('10')


def _to_min(txt):
    """'08:40' -> 520 ; '-8:00' -> -480."""
    negativo = txt.strip().startswith('-')
    limpo = txt.strip().lstrip('-')
    try:
        horas, minutos = limpo.split(':')
        valor = int(horas) * 60 + int(minutos)
    except (ValueError, AttributeError):
        return 0
    return -valor if negativo else valor


def parse_registro(registro):
    """Extrai batimentos e totais de uma linha da folha."""
    texto = (registro or '').strip()
    maiusc = texto.upper()
    info = {
        'vazio': (not texto) or texto in {'-', '--'},
        'falta': 'FALTA' in maiusc,
        'feriado': 'FERIADO' in maiusc,
        'batimentos': 0,
        'trabalhadas_min': None,
        'previstas_min': None,
        'saldo_min': None,
    }
    if info['vazio']:
        return info

    partes = [p.strip() for p in texto.split('|')]
    totais_txt = partes[-1] if partes else ''
    batimentos_txt = ' '.join(partes[:-1]) if len(partes) > 1 else ''

    info['batimentos'] = len(TIME_RE.findall(batimentos_txt))

    tempos = [_to_min(t) for t in TIME_RE.findall(totais_txt)]
    negativos = [t for t in tempos if t < 0]
    positivos = [t for t in tempos if t >= 0]

    if negativos:
        # Saldo negativo é sempre explícito com "-" na folha.
        info['saldo_min'] = min(negativos)
        if positivos:
            info['previstas_min'] = positivos[-1]
    elif len(tempos) >= 3:
        info['trabalhadas_min'], info['previstas_min'], info['saldo_min'] = tempos[:3]
    elif len(tempos) == 2:
        info['trabalhadas_min'], info['previstas_min'] = tempos
        info['saldo_min'] = tempos[0] - tempos[1]
    return info


def dia_problematico(info, semana):
    """True (problema) / False (ok) / None (dia não avaliável)."""
    nome_semana = (semana or '').strip().lower()
    if info['vazio'] or info['feriado']:
        return None
    if nome_semana.startswith('domingo'):
        return None
    if info['falta']:
        return True
    if info['saldo_min'] is not None and info['saldo_min'] < -TOLERANCIA_NEGATIVA_MIN:
        return True

    batimentos = info['batimentos']
    if batimentos == 0:
        # Sem batimentos e sem falta registrada: não dá para julgar.
        return None
    minimo = BATIMENTOS_DIA_COMPLETO
    if info['previstas_min'] is not None and info['previstas_min'] < MEIO_PERIODO_LIMITE_MIN:
        minimo = BATIMENTOS_MEIO_PERIODO
    if batimentos < minimo or batimentos % 2 != 0:
        return True
    return False


def _data_do_registro(dia_txt, folha):
    """'20/07' + folha(7/2026) -> date(2026, 7, 20). Trata virada de ano."""
    try:
        dia_str, mes_str = str(dia_txt).split('/')
        dia, mes = int(dia_str), int(mes_str)
    except (ValueError, AttributeError):
        return None
    ano = folha.year or 0
    if folha.month and mes != folha.month:
        if folha.month == 1 and mes == 12:
            ano -= 1
        elif folha.month == 12 and mes == 1:
            ano += 1
    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def avaliar_folha(folha):
    """Agrupa os dias por semana e conta as semanas com problema."""
    semanas = {}
    dias_problema = []
    dias_avaliados = 0

    for registro in (folha.daily_records or []):
        if not isinstance(registro, dict):
            continue
        info = parse_registro(registro.get('registro'))
        problema = dia_problematico(info, registro.get('semana'))
        if problema is None:
            continue
        dias_avaliados += 1

        data = _data_do_registro(registro.get('dia', ''), folha)
        chave = str(data.isocalendar()[:2]) if data else f"dia-{registro.get('dia')}"
        semanas.setdefault(chave, False)
        if problema:
            semanas[chave] = True
            dias_problema.append({
                'dia': registro.get('dia'),
                'batimentos': info['batimentos'],
                'saldo_min': info['saldo_min'],
                'falta': info['falta'],
                'registro': registro.get('registro'),
            })

    return {
        'dias_avaliados': dias_avaliados,
        'semanas_avaliadas': len(semanas),
        'semanas_com_problema': sum(1 for ruim in semanas.values() if ruim),
        'dias_problema': dias_problema,
    }


def nota_assiduidade(user, ano, mes):
    """(pontos, pontos_aplicaveis, detalhes) da assiduidade no mês.

    O ponto eletrônico manda quando existe: ele é o dado do dia, com a jornada
    contratada de cada um. A folha em PDF continua respondendo pelos meses em
    que o ponto ainda não foi sincronizado, para nenhum histórico ficar sem nota.
    """
    try:
        from .assiduidade_ponto import nota_assiduidade_ponto
        pelo_ponto = nota_assiduidade_ponto(user, ano, mes)
        if pelo_ponto is not None:
            return pelo_ponto
    except Exception:                       # ponto fora do ar não derruba a nota
        pass

    try:
        from folhaponto.models import FolhaPonto
    except Exception:
        return Decimal('0'), Decimal('0'), {'indisponivel': True}

    folha = FolhaPonto.objects.filter(user=user, year=ano, month=mes).first()
    if not folha or not folha.daily_records:
        return Decimal('0'), Decimal('0'), {'sem_folha': True}

    resultado = avaliar_folha(folha)
    if resultado['semanas_avaliadas'] == 0:
        resultado['sem_dias_avaliaveis'] = True
        return Decimal('0'), Decimal('0'), resultado

    desconto = PENALIDADE_SEMANA * resultado['semanas_com_problema']
    pontos = PONTOS_ASSIDUIDADE - desconto
    if pontos < 0:
        pontos = Decimal('0')
    resultado['desconto'] = float(desconto)
    resultado['total_saldo_mes'] = folha.total_saldo
    return pontos, PONTOS_ASSIDUIDADE, resultado
