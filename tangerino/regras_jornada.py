"""Regras de jornada que travam ou avisam o colaborador no portal.

São seis regras, e cinco delas **trancam a porta do portal**. Por isso tudo
aqui é conservador de propósito:

* cada regra tem a sua própria chave de liga-desliga, e todas nascem
  **desligadas**;
* quem não bate ponto nunca é travado — só entra na regra quem tem jornada
  prevista para o dia;
* qualquer erro (API fora do ar, dado faltando) **libera** a navegação. Trancar
  alguém do lado de fora do portal por causa de um timeout seria pior do que
  deixar de aplicar a regra.

As regras:

1. Voltar do almoço com menos de uma hora é recusado na hora de bater.
2. Entrou entre 07h e 10h e não saiu para almoçar até as 16h: lembrete.
3. Passou de 1h05 de almoço: aviso de que já passou da hora.
4. Durante o almoço o portal fica travado, liberando só a volta.
5. Sem bater a entrada, não usa o portal.
6. Sem a saída de ontem, não usa o portal.
"""
import logging
from datetime import time, timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

# ── Motivos de bloqueio ──────────────────────────────────────────────────────
SEM_ENTRADA = 'SEM_ENTRADA'
EM_ALMOCO = 'EM_ALMOCO'
SAIDA_PENDENTE = 'SAIDA_PENDENTE'

# ── Avisos (popup, não travam) ───────────────────────────────────────────────
ESQUECEU_ALMOCO = 'ESQUECEU_ALMOCO'
ALMOCO_LONGO = 'ALMOCO_LONGO'


def _minutos(delta):
    return int(delta.total_seconds() // 60)


def inicio_do_almoco(status):
    """Quando a pessoa saiu para almoçar — a primeira saída do dia."""
    for evento in status.get('eventos') or []:
        if evento['tipo'] == 'SAIDA':
            return evento['quando']
    return None


def entrada_do_dia(status):
    for evento in status.get('eventos') or []:
        if evento['tipo'] == 'ENTRADA':
            return evento['quando']
    return None


def em_almoco(status):
    """Saiu para o almoço e ainda não voltou."""
    return bool(status.get('saiu_almoco')) and not status.get('voltou_almoco')


def minutos_de_almoco(status, agora=None):
    """Há quantos minutos a pessoa está fora para o almoço. None se não saiu."""
    comeco = inicio_do_almoco(status)
    if not comeco or not em_almoco(status):
        return None
    return _minutos((agora or timezone.localtime()) - comeco)


def volta_do_almoco_liberada(status, config, agora=None):
    """(pode_voltar, faltam_minutos) — o almoço tem duração mínima.

    Quem bate a volta cedo demais gera um intervalo inválido na folha, que o
    RH depois tem de ajustar à mão. Barrar aqui evita criar o problema.
    """
    minimo = getattr(config, 'almoco_minimo_minutos', 60) or 0
    if not minimo or not em_almoco(status):
        return True, 0
    decorridos = minutos_de_almoco(status, agora)
    if decorridos is None or decorridos >= minimo:
        return True, 0
    return False, minimo - decorridos


def avisos(status, config, agora=None):
    """Avisos de popup para a jornada de hoje."""
    agora = agora or timezone.localtime()
    achados = []

    if not getattr(config, 'avisar_almoco', False):
        return achados

    entrada = entrada_do_dia(status)
    limite_lembrete = getattr(config, 'lembrete_almoco_hora', time(16, 0)) or time(16, 0)

    # 2. Entrou de manhã e não saiu para almoçar até o horário limite.
    if entrada and not status.get('saiu_almoco'):
        de = getattr(config, 'entrada_manha_de', time(7, 0)) or time(7, 0)
        ate = getattr(config, 'entrada_manha_ate', time(10, 0)) or time(10, 0)
        if de <= entrada.time() <= ate and agora.time() >= limite_lembrete:
            achados.append({
                'chave': ESQUECEU_ALMOCO,
                'titulo': 'Você ainda não registrou o almoço',
                'texto': (f'Sua entrada foi às {entrada:%H:%M} e até agora não há '
                          f'saída para o intervalo. Se você almoçou, registre a '
                          f'marcação para a folha não ficar errada.'),
            })

    # 3. Almoço passou do limite.
    decorridos = minutos_de_almoco(status, agora)
    maximo = getattr(config, 'almoco_maximo_minutos', 65) or 65
    if decorridos is not None and decorridos > maximo:
        horas, minutos = divmod(decorridos, 60)
        achados.append({
            'chave': ALMOCO_LONGO,
            'titulo': 'Seu intervalo já passou de uma hora',
            'texto': (f'Você saiu para o almoço há {horas}h{minutos:02d}. '
                      f'Registre a volta para não gerar desconto na folha.'),
        })
    return achados


def bloqueio(status, pendencias, config, agora=None):
    """Por que o portal deve ficar travado agora, ou None.

    ``pendencias`` são os dias anteriores com entrada sem saída, como devolvido
    por ``ponto.pendencias()``.
    """
    agora = agora or timezone.localtime()

    # 6. Saída de ontem em aberto — vem antes das outras porque é o problema
    # mais antigo e o que mais suja a folha.
    if getattr(config, 'bloquear_saida_pendente', False) and pendencias:
        mais_antiga = min(pendencias, key=lambda p: p['dia'])
        return {
            'motivo': SAIDA_PENDENTE,
            'titulo': 'Você tem um ponto em aberto',
            'texto': (f"Ficou uma entrada sem saída em "
                      f"{mais_antiga['dia']:%d/%m}, às {mais_antiga['entrada']:%H:%M}. "
                      f"Regularize para continuar usando o portal."),
            'dia': mais_antiga['dia'],
        }

    # 4. Em almoço: o portal só libera a volta.
    if getattr(config, 'bloquear_durante_almoco', False) and em_almoco(status):
        decorridos = minutos_de_almoco(status, agora) or 0
        minimo = getattr(config, 'almoco_minimo_minutos', 60) or 0
        return {
            'motivo': EM_ALMOCO,
            'titulo': 'Bom almoço!',
            'texto': ('O portal volta assim que você registrar a volta do '
                      'intervalo.'),
            'minutos': decorridos,
            'falta_para_o_minimo': max(0, minimo - decorridos),
        }

    # 5. Não bateu a entrada.
    if getattr(config, 'bloquear_sem_entrada', False) and not status.get('bateu_entrada'):
        return {
            'motivo': SEM_ENTRADA,
            'titulo': 'Registre sua entrada para começar',
            'texto': 'O portal libera assim que sua entrada do dia for registrada.',
        }

    return None
