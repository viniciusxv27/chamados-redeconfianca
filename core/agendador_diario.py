"""Tarefas que rodam uma vez por dia, num horário, sem cron.

Produção roda só gunicorn (sem cron nem celery), então o mesmo arranjo do
agendador do Tangerino (tangerino/agendador.py) vale aqui, só que genérico:

1. **Nunca segura o usuário.** A tarefa roda numa thread; a requisição segue.
2. **Roda uma vez só.** Quem ganha o dia é decidido por um UPDATE condicional
   no registro ``TarefaDiaria`` — só um worker vê uma linha afetada.
3. **Não pesa.** Cada processo só olha o banco uma vez por minuto.

Para incluir uma tarefa, acrescente-a em ``TAREFAS``. Para rodar à mão:
``python manage.py rodar_tarefa_diaria <chave>``.
"""
import importlib
import logging
import threading
from datetime import time

from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# (chave, horário, função "modulo.funcao", descrição)
TAREFAS = [
    ('simulador_projecao', time(8, 0), 'simulator.projecao.gerar_projecao_diaria',
     'Projeção do simulador de cada pessoa'),
    ('quadro_hc_lembretes', time(8, 0), 'quadro_hc.servicos.enviar_lembretes',
     'Lembretes das movimentações do Quadro de HC'),
]

_ultima_checagem = None
_intervalo_checagem = 60  # segundos
_trava = threading.Lock()


def _alvo(hora, agora):
    return timezone.make_aware(timezone.datetime.combine(timezone.localdate(agora), hora),
                               timezone.get_current_timezone())


def _funcao(caminho):
    modulo, nome = caminho.rsplit('.', 1)
    return getattr(importlib.import_module(modulo), nome)


def executar(chave):
    """Roda a tarefa agora (na thread atual) e grava o resultado."""
    from core.models import TarefaDiaria

    tarefa = next((t for t in TAREFAS if t[0] == chave), None)
    if not tarefa:
        raise ValueError(f'Tarefa diária desconhecida: {chave}')
    _, _hora, caminho, descricao = tarefa
    registro, _ = TarefaDiaria.objects.get_or_create(chave=chave, defaults={'descricao': descricao})
    try:
        resultado = _funcao(caminho)()
        sucesso, detalhe = True, str(resultado or 'ok')[:2000]
    except Exception as exc:  # noqa: BLE001 — nunca derruba a thread
        logger.exception('Tarefa diária %s quebrou', chave)
        sucesso, detalhe = False, f'{type(exc).__name__}: {exc}'[:2000]
    TarefaDiaria.objects.filter(pk=registro.pk).update(ultimo_fim=timezone.now(), sucesso=sucesso, detalhe=detalhe)
    return sucesso, detalhe


def _em_segundo_plano(chave):
    try:
        executar(chave)
    except Exception:  # noqa: BLE001 — a thread nunca derruba nada
        logger.exception('Tarefa diária %s não rodou', chave)
    finally:
        close_old_connections()


def disparar_tarefas_vencidas(agora=None, em_thread=True):
    """Chamado pelo middleware. Devolve as chaves que ESTA chamada disparou."""
    global _ultima_checagem
    agora = agora or timezone.now()
    with _trava:
        if _ultima_checagem is not None and (agora - _ultima_checagem).total_seconds() < _intervalo_checagem:
            return []
        _ultima_checagem = agora

    disparadas = []
    try:
        from core.models import TarefaDiaria

        for chave, hora, _caminho, descricao in TAREFAS:
            alvo = _alvo(hora, agora)
            if agora < alvo:
                continue
            registro, _ = TarefaDiaria.objects.get_or_create(chave=chave, defaults={'descricao': descricao})
            ganhou = (TarefaDiaria.objects.filter(pk=registro.pk)
                      .filter(Q(ultima_execucao__lt=alvo) | Q(ultima_execucao__isnull=True))
                      .update(ultima_execucao=agora))
            if not ganhou:
                continue
            disparadas.append(chave)
            if em_thread:
                threading.Thread(target=_em_segundo_plano, args=(chave,), name=f'tarefa-{chave}',
                                 daemon=True).start()
            else:
                executar(chave)
            logger.info('Tarefa diária %s disparada.', chave)
    except Exception as exc:  # noqa: BLE001 — jamais quebra a página
        logger.warning('Agendador diário ignorado por erro: %s', exc)
    return disparadas


class TarefasDiariasMiddleware:
    """Acorda as tarefas diárias na primeira visita de alguém logado depois do horário."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, 'user', None)
        if usuario is not None and usuario.is_authenticated:
            import os

            from core.utils import processo_de_teste
            # Scripts de teste/manutenção não disparam nada (RC_SEM_TAREFAS_DIARIAS=1 desliga à mão).
            if not processo_de_teste() and os.environ.get('RC_SEM_TAREFAS_DIARIAS') != '1':
                disparar_tarefas_vencidas()
        return self.get_response(request)
