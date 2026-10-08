"""Leitura automática do SAP a cada N horas (padrão: 3) — sem cron.

Mesmo arranjo do agendador do ponto (``tangerino/agendador.py``):

1. **Nunca segura o usuário.** A leitura (~30 s no MySQL do SAP) roda numa thread;
   a requisição que passou por aqui segue na hora.
2. **Roda uma vez só.** Os workers do gunicorn disputam o carimbo
   ``AgendaLeituraSap.ultima_automatica`` num UPDATE condicional: só um ganha.
3. **Não pesa.** Cada processo só olha o banco uma vez por minuto.

Leitura manual (botão "Atualizar do SAP" ou ``manage.py sincronizar_sap``) conta:
se alguém leu há pouco, a automática espera o intervalo a partir dela.
"""
import logging
import threading
from datetime import timedelta

from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

_ultima_checagem = None
_intervalo_checagem = 60          # segundos
_trava_local = threading.Lock()


def proxima_leitura(agenda=None, agora=None):
    """Quando a próxima leitura automática vence (ou None se desligada)."""
    from .models import AgendaLeituraSap, SincronizacaoAuditoria

    agenda = agenda or AgendaLeituraSap.get()
    if not agenda.ativo:
        return None
    marcos = [m for m in (agenda.ultima_automatica,
                          SincronizacaoAuditoria.objects.values_list('quando', flat=True).first()) if m]
    if not marcos:
        return agora or timezone.now()
    return max(marcos) + timedelta(hours=max(1, agenda.intervalo_horas or 3))


def esta_na_hora(agenda=None, agora=None):
    agora = agora or timezone.now()
    proxima = proxima_leitura(agenda, agora)
    return proxima is not None and agora >= proxima


def _ler_em_segundo_plano():
    from .espelho import sincronizar
    from .mysql import SapIndisponivel
    try:
        resumo = sincronizar(por=None)
        logger.info('Leitura automática do SAP: %s', resumo)
    except SapIndisponivel as exc:                    # já fica registrada como erro na sincronização
        logger.warning('Leitura automática do SAP falhou: %s', exc)
    except Exception as exc:                          # noqa: BLE001 — nunca derruba a thread
        logger.exception('Leitura automática do SAP quebrou: %s', exc)
    finally:
        close_old_connections()


def disparar_se_esta_na_hora():
    """Chamado pelo middleware. Devolve True se ESTA chamada disparou a leitura."""
    global _ultima_checagem

    agora = timezone.now()
    with _trava_local:
        if _ultima_checagem is not None and (agora - _ultima_checagem).total_seconds() < _intervalo_checagem:
            return False
        _ultima_checagem = agora

    try:
        from core.utils import processo_de_teste

        from .models import AgendaLeituraSap

        if processo_de_teste():                       # script de teste não lê o SAP de verdade
            return False
        agenda = AgendaLeituraSap.get()
        if not esta_na_hora(agenda, agora):
            return False
        limite = agora - timedelta(hours=max(1, agenda.intervalo_horas or 3))
        ganhou = (AgendaLeituraSap.objects.filter(pk=agenda.pk, ativo=True)
                  .filter(Q(ultima_automatica__lt=limite) | Q(ultima_automatica__isnull=True))
                  .update(ultima_automatica=agora))
        if not ganhou:
            return False
        threading.Thread(target=_ler_em_segundo_plano, name='sap-leitura-automatica', daemon=True).start()
        logger.info('Leitura automática do SAP disparada (a cada %s h).', agenda.intervalo_horas)
        return True
    except Exception as exc:                          # noqa: BLE001 — jamais quebra a página
        logger.warning('Agendador do SAP ignorado por erro: %s', exc)
        return False
