"""Sincronização diária do Tangerino sem cron.

Produção roda só gunicorn (3 workers sync): não existe cron, celery nem
processo separado. Então quem dispara a sincronização é a primeira requisição
que chega depois da hora marcada.

Três cuidados, porque isso mora no caminho de uma requisição de verdade:

1. **Nunca segura o usuário.** O trabalho vai para uma thread; a requisição que
   passou por aqui devolve a página na mesma hora.
2. **Roda uma vez só.** Os 3 workers batem no mesmo momento, então quem "ganha"
   o dia é decidido por um UPDATE condicional no banco — só uma linha volta
   afetada, e só esse worker segue.
3. **Não pesa.** Cada worker guarda em memória o instante da última checagem e
   só consulta o banco a cada minuto.

Se um dia houver cron de verdade (`python manage.py sync_tangerino --dados`),
ele grava o mesmo carimbo e o agendador simplesmente não encontra nada a fazer.
"""
import logging
import threading

from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)

# Um por processo: evita ir ao banco a cada requisição.
_ultima_checagem = None
_intervalo_checagem = 60          # segundos
_trava_local = threading.Lock()


def _hoje_na_hora(config, agora):
    """O instante de hoje em que a sincronização deveria ter acontecido."""
    return timezone.make_aware(
        timezone.datetime.combine(timezone.localdate(agora), config.hora_sincronizacao),
        timezone.get_current_timezone())


def esta_na_hora(config, agora=None):
    """Já passou do horário de hoje e ainda não rodou hoje?"""
    if not config.sincronizar_automatico:
        return False
    agora = agora or timezone.now()
    alvo = _hoje_na_hora(config, agora)
    if agora < alvo:
        return False
    anterior = config.ultima_sincronizacao_automatica
    return anterior is None or anterior < alvo


def _executar(dias):
    """O trabalho em si. Roda fora da requisição, numa thread."""
    from tangerino.client import TangerinoError
    from tangerino.models import SincronizacaoTangerino
    from tangerino.sync import (sincronizar_ferias, sincronizar_jornadas,
                                sincronizar_marcacoes, sincronizar_saldos)

    # A jornada vem primeiro: o previsto de cada dia depende dela.
    tarefas = (
        (SincronizacaoTangerino.Tipo.JORNADA, 'Jornadas', sincronizar_jornadas),
        (SincronizacaoTangerino.Tipo.PONTO, 'Marcações', lambda: sincronizar_marcacoes(dias=dias)),
        (SincronizacaoTangerino.Tipo.FERIAS, 'Férias', sincronizar_ferias),
        (SincronizacaoTangerino.Tipo.SALDO, 'Saldo de horas', sincronizar_saldos),
    )
    for tipo, rotulo, funcao in tarefas:
        registro = SincronizacaoTangerino(tipo=tipo)
        try:
            resultado = funcao()
            registro.criados = resultado.get('criados', 0)
            registro.atualizados = resultado.get('atualizados', 0)
            registro.sucesso = True
            registro.detalhe = 'Sincronização automática diária.'
            registro.save()
            logger.info('Tangerino automático — %s: %s novos, %s atualizados.',
                        rotulo, registro.criados, registro.atualizados)
        except TangerinoError as exc:
            registro.sucesso = False
            registro.detalhe = f'Sincronização automática: {exc}'[:2000]
            registro.save()
            logger.warning('Tangerino automático — %s falhou: %s', rotulo, exc)
        except Exception as exc:                     # nunca derruba a thread
            logger.exception('Tangerino automático — %s quebrou: %s', rotulo, exc)


def _em_segundo_plano(dias):
    try:
        _executar(dias)
    finally:
        # Thread própria abre conexão própria: devolver evita vazar conexão
        # no pool do Postgres a cada dia.
        close_old_connections()


def disparar_se_esta_na_hora():
    """Chamado pelo middleware. Devolve True se ESTA chamada disparou a sync."""
    global _ultima_checagem

    agora = timezone.now()
    with _trava_local:
        if (_ultima_checagem is not None
                and (agora - _ultima_checagem).total_seconds() < _intervalo_checagem):
            return False
        _ultima_checagem = agora

    try:
        from tangerino.client import integracao_ativa
        from tangerino.models import ConfiguracaoTangerino

        if not integracao_ativa():
            return False

        config = ConfiguracaoTangerino.get()
        if not esta_na_hora(config, agora):
            return False

        # Quem ganha o dia: só um worker vê uma linha afetada. O filtro repete
        # a condição de propósito — é ele que faz a corrida ser resolvida pelo
        # banco, e não pelo relógio de cada processo.
        alvo = _hoje_na_hora(config, agora)
        ganhou = (ConfiguracaoTangerino.objects
                  .filter(pk=config.pk)
                  .filter(models_Q_antes_de(alvo))
                  .update(ultima_sincronizacao_automatica=agora))
        if not ganhou:
            return False

        threading.Thread(target=_em_segundo_plano, args=(config.dias_sincronizacao,),
                         name='tangerino-sync-diaria', daemon=True).start()
        logger.info('Tangerino: sincronização automática das %s disparada.',
                    config.hora_sincronizacao)
        return True
    except Exception as exc:                          # jamais quebra a página
        logger.warning('Agendador do Tangerino ignorado por erro: %s', exc)
        return False


def models_Q_antes_de(alvo):
    """`ultima < alvo` OU nunca rodou — escrito uma vez, usado no UPDATE."""
    from django.db.models import Q
    return Q(ultima_sincronizacao_automatica__lt=alvo) | Q(
        ultima_sincronizacao_automatica__isnull=True)
