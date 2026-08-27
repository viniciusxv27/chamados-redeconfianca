"""Leitura e interpretação das marcações de ponto.

O Tangerino devolve cada registro como um **par** entrada/saída
(``dateIn``/``dateOut``). ``dateOut`` vazio quer dizer "entrou e ainda não
saiu". Um dia normal de quem almoça fora vem assim:

    par 1: dateIn 09:07  dateOut 11:58     <- manhã (a saída é o almoço)
    par 2: dateIn 12:46  dateOut  —        <- tarde, ainda dentro

Este módulo achata isso em eventos (ENTRADA/SAÍDA) e deriva o que a tela
precisa dizer em português: já bateu a entrada? já saiu para o almoço? tem
algum dia com ponto em aberto?
"""
import logging
from datetime import timedelta

from django.utils import timezone

from .client import (TangerinoError, de_millis, listar_marcacoes)

logger = logging.getLogger(__name__)

# Um par aberto (entrou e não saiu) em um dia ANTERIOR é ponto esquecido.
# No dia corrente é só "está trabalhando".
DIAS_PARA_TRAS_PENDENCIA = 30


def _sem_duplicatas(pares):
    """Remove pares repetidos pela paginação da API (mesmo ``id``).

    A API do Tangerino/Sólides devolve cada par em duas páginas (visto ao vivo:
    400 itens para 200 pares reais). Sem isto, ``_eventos`` achataria cada
    marcação duas vezes e a tela mostraria tudo em dobro. Mantém a 1ª ocorrência
    de cada id; pares sem id (raros) passam sem deduplicar. Espelha o ``vistos``
    de ``sync.sincronizar_marcacoes``, que já deixava a tabela sincronizada limpa.
    """
    vistos = set()
    unicos = []
    for par in pares or []:
        pid = par.get('id')
        if pid is not None:
            if pid in vistos:
                continue
            vistos.add(pid)
        unicos.append(par)
    return unicos


def _eventos(pares):
    """Achata os pares em uma linha do tempo de marcações individuais."""
    eventos = []
    for par in pares:
        entrada, saida = de_millis(par.get('dateIn')), de_millis(par.get('dateOut'))
        if entrada:
            eventos.append({'tipo': 'ENTRADA', 'quando': entrada, 'par_id': par.get('id'),
                            'editado': bool(par.get('editedIn')), 'nsr': par.get('nsrIn')})
        if saida:
            eventos.append({'tipo': 'SAIDA', 'quando': saida, 'par_id': par.get('id'),
                            'editado': bool(par.get('editedOut')), 'nsr': par.get('nsrOut')})
    return sorted(eventos, key=lambda e: e['quando'])


def _segundos_trabalhados(pares, ate=None):
    """Soma os pares fechados; o par aberto conta até agora."""
    ate = ate or timezone.localtime()
    total = 0
    for par in pares:
        entrada, saida = de_millis(par.get('dateIn')), de_millis(par.get('dateOut'))
        if not entrada:
            continue
        fim = saida or ate
        if fim > entrada:
            total += int((fim - entrada).total_seconds())
    return total


def _pares_do_dia(pares, dia):
    return sorted([p for p in pares if de_millis(p.get('dateIn'))
                   and de_millis(p['dateIn']).date() == dia],
                  key=lambda p: p['dateIn'])


def status_do_dia(employee_id, dia=None, pares=None):
    """Resumo do dia de UMA pessoa, pronto para a tela.

    ``pares`` permite reaproveitar uma consulta em lote (painel do gestor) sem
    bater na API de novo por pessoa.
    """
    dia = dia or timezone.localdate()
    agora = timezone.localtime()

    if pares is None:
        pares = _sem_duplicatas(listar_marcacoes(dia, dia, employee_id=employee_id))
    do_dia = _pares_do_dia(pares, dia)
    eventos = _eventos(do_dia)

    aberto = next((p for p in do_dia if de_millis(p.get('dateIn'))
                   and not de_millis(p.get('dateOut'))), None)
    dentro = aberto is not None
    desde = de_millis(aberto['dateIn']) if aberto else None

    bateu_entrada = bool(eventos)
    # A primeira saída do dia é, na prática, a saída para o almoço.
    saiu_almoco = len([e for e in eventos if e['tipo'] == 'SAIDA']) >= 1
    voltou_almoco = len([e for e in eventos if e['tipo'] == 'ENTRADA']) >= 2

    trabalhado = _segundos_trabalhados(do_dia, ate=agora)

    if not bateu_entrada:
        situacao, rotulo = 'SEM_ENTRADA', 'Você ainda não bateu a entrada hoje'
    elif dentro and not saiu_almoco:
        situacao, rotulo = 'TRABALHANDO', 'Trabalhando desde a entrada'
    elif dentro:
        situacao, rotulo = 'TRABALHANDO', 'Trabalhando (voltou do intervalo)'
    elif saiu_almoco and not voltou_almoco:
        situacao, rotulo = 'EM_INTERVALO', 'Fora — intervalo em andamento'
    else:
        situacao, rotulo = 'ENCERRADO', 'Jornada encerrada por enquanto'

    return {
        'dia': dia,
        'employee_id': employee_id,
        'eventos': eventos,
        'total_marcacoes': len(eventos),
        'bateu_entrada': bateu_entrada,
        'saiu_almoco': saiu_almoco,
        'voltou_almoco': voltou_almoco,
        'dentro': dentro,
        'desde': desde,
        'situacao': situacao,
        'rotulo': rotulo,
        'proxima_acao': 'SAIDA' if dentro else 'ENTRADA',
        'trabalhado_segundos': trabalhado,
        'trabalhado_hhmm': formata_hhmm(trabalhado),
        'primeira': eventos[0]['quando'] if eventos else None,
        'ultima': eventos[-1]['quando'] if eventos else None,
    }


def formata_hhmm(segundos):
    segundos = max(0, int(segundos or 0))
    return f"{segundos // 3600:02d}:{(segundos % 3600) // 60:02d}"


def pendencias(employee_id, dias=DIAS_PARA_TRAS_PENDENCIA, pares=None):
    """Dias anteriores com par aberto — ou seja, ponto esquecido.

    O dia corrente fica de fora de propósito: entrar e ainda não ter saído é o
    estado normal de quem está trabalhando, não uma pendência.
    """
    hoje = timezone.localdate()
    inicio = hoje - timedelta(days=dias)
    if pares is None:
        pares = _sem_duplicatas(listar_marcacoes(inicio, hoje, employee_id=employee_id, ttl=300))

    abertos = []
    for par in pares:
        entrada, saida = de_millis(par.get('dateIn')), de_millis(par.get('dateOut'))
        if not entrada or saida:
            continue
        if entrada.date() >= hoje:
            continue
        abertos.append({'par_id': par.get('id'), 'entrada': entrada, 'dia': entrada.date()})
    return sorted(abertos, key=lambda p: p['entrada'], reverse=True)


def resumo_para_usuario(usuario, dia=None):
    """Payload do widget da home. Nunca levanta exceção: se o Tangerino falhar,
    devolve ``disponivel=False`` e a home segue normalmente."""
    if not getattr(usuario, 'tangerino_employee_id', None):
        return {'disponivel': False, 'motivo': 'sem_vinculo'}
    try:
        status = status_do_dia(usuario.tangerino_employee_id, dia=dia)
        status['pendencias'] = pendencias(usuario.tangerino_employee_id)
        status['disponivel'] = True
        return status
    except TangerinoError as exc:
        logger.warning('Ponto indisponível para %s: %s', usuario, exc)
        return {'disponivel': False, 'motivo': 'indisponivel'}


def painel_da_empresa(dia=None):
    """Todo mundo do dia numa consulta só, para o painel do SuperAdmin.

    A API aceita a busca sem ``employeeId``, o que evita 168 chamadas.
    """
    dia = dia or timezone.localdate()
    pares = _sem_duplicatas(listar_marcacoes(dia, dia, ttl=120))
    por_funcionario = {}
    for par in pares:
        por_funcionario.setdefault(par.get('employeeId'), []).append(par)
    return {eid: status_do_dia(eid, dia=dia, pares=lista)
            for eid, lista in por_funcionario.items()}
